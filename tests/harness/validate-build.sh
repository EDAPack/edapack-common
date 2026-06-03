#!/usr/bin/env bash
# Local validation harness for a tool's scripts/build.sh.
#
# Stubs the heavy build tools (git/configure/make/cmake/meson/ninja/nproc) so the
# build.sh *wiring* can be exercised offline and without a real compile:
#   - directory model (WORK_DIR/OUT_DIR, source tree untouched)
#   - reading resolved inputs from a candidate
#   - skills + export.envrc staging, manifest emission, tarball packaging
#
# It does NOT validate actual compilation — that is CI's job. It validates that
# build.sh produces a schema-valid manifest + a well-formed tarball and writes
# nothing into the source tree.
#
# Usage:
#   validate-build.sh <tool-dir> <core-name> "<bin1 bin2 ...>" [candidate.json]
# If candidate.json is omitted, a synthetic one is generated from the tool's
# build-inputs.yaml input names (offline; fake SHAs).
set -euo pipefail

TOOL_DIR="$(cd "${1:?tool-dir}" && pwd)"
CORE_NAME="${2:?core-name}"
FAKE_BINS="${3:?space-separated binaries}"
CANDIDATE_IN="${4:-}"

EC_COMMON="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; export EC_COMMON
TOOL="$(basename "$TOOL_DIR")"
SB="$(mktemp -d)"
trap 'rm -rf "$SB"' EXIT
export EC_FAKE_STATE="$SB/state"; mkdir -p "$EC_FAKE_STATE"
export EC_FAKE_BINS="$FAKE_BINS"
BIN="$SB/fakebin"; mkdir -p "$BIN"

# ---- fake tools ------------------------------------------------------------
cat > "$BIN/git" <<'EOF'
#!/usr/bin/env bash
# minimal git stub: clone makes a buildable-looking tree; everything else no-ops
sub="$1"; shift || true
case "$sub" in
  clone)
    dest="${@: -1}"
    mkdir -p "$dest"
    cat > "$dest/configure" <<'CFG'
#!/usr/bin/env bash
for a in "$@"; do case "$a" in --prefix=*) echo "${a#--prefix=}" > "$EC_FAKE_STATE/prefix";; esac; done
CFG
    chmod +x "$dest/configure"
    printf '#!/bin/sh\n:\n' > "$dest/autoconf.sh"; chmod +x "$dest/autoconf.sh"
    : > "$dest/CMakeLists.txt"
    ;;
  -C) d="$1"; shift; real="$1"; shift || true
      case "$real" in rev-parse) echo "0000000000000000000000000000000000000000";; esac ;;
  rev-parse) echo "0000000000000000000000000000000000000000" ;;
  config|checkout|submodule|fetch|init|add|commit|tag) : ;;
  *) : ;;
esac
EOF

cat > "$BIN/make" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [ "$a" = "install" ]; then
    prefix="$(cat "$EC_FAKE_STATE/prefix" 2>/dev/null)"
    [ -n "$prefix" ] || prefix="$EC_FAKE_STATE/defprefix"
    mkdir -p "$prefix/bin" "$prefix/share"
    for b in $EC_FAKE_BINS; do printf '#!/bin/sh\necho fake %s\n' "$b" > "$prefix/bin/$b"; chmod +x "$prefix/bin/$b"; done
  fi
done
EOF

cat > "$BIN/cmake" <<'EOF'
#!/usr/bin/env bash
prefix=""; build=""; mode="configure"; args=("$@")
for ((i=0;i<${#args[@]};i++)); do case "${args[$i]}" in
  -DCMAKE_INSTALL_PREFIX=*) prefix="${args[$i]#-DCMAKE_INSTALL_PREFIX=}";;
  -B) build="${args[$((i+1))]}";;
  --build) mode="build"; build="${args[$((i+1))]}";;
  --install) mode="install"; prefix_dir="${args[$((i+1))]}";;
esac; done
if [ "$mode" = "configure" ]; then mkdir -p "$build"; echo "$prefix" > "$build/.prefix";
  [ -n "$prefix" ] && echo "$prefix" > "$EC_FAKE_STATE/prefix"   # for a later `make install`
elif [ "$mode" = "build" ]; then prefix="$(cat "$build/.prefix" 2>/dev/null)";
  mkdir -p "$prefix/bin" "$prefix/share"
  for b in $EC_FAKE_BINS; do printf '#!/bin/sh\necho fake %s\n' "$b" > "$prefix/bin/$b"; chmod +x "$prefix/bin/$b"; done
fi
EOF

for t in meson ninja; do printf '#!/bin/sh\n:\n' > "$BIN/$t"; chmod +x "$BIN/$t"; done
printf '#!/bin/sh\necho 4\n' > "$BIN/nproc"; chmod +x "$BIN/nproc"
chmod +x "$BIN"/*

# ---- candidate -------------------------------------------------------------
CAND="$SB/candidate.json"
if [ -n "$CANDIDATE_IN" ]; then
  cp "$CANDIDATE_IN" "$CAND"
else
  python3 - "$TOOL_DIR/build-inputs.yaml" "$CAND" <<'PY'
import sys, json
sys.path.insert(0, __import__("os").path.join(sys.argv[0].rsplit("/tests/",1)[0], "scripts")) if False else None
# parse with the shared yaml helper
import importlib.util, pathlib
common = pathlib.Path(__file__).resolve()
# locate edapack-common scripts via env
import os
scripts = pathlib.Path(os.environ["EC_COMMON"]) / "scripts"
spec = importlib.util.spec_from_file_location("_ecyaml", scripts / "_ecyaml.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
spec_yaml = m.parse_simple_yaml(open(sys.argv[1]).read())
inputs = []
core = spec_yaml["core"]
inputs.append({"name": core["name"], "role": "core", "repo": core["repo"],
               "ref": "vTEST", "resolved_sha": "a"*40, "version": "9.9", "tracked": True})
for d in spec_yaml.get("dependencies") or []:
    inputs.append({"name": d["name"], "role": "dependency", "repo": d["repo"],
                   "ref": "main", "resolved_sha": "b"*40, "version": None, "tracked": d.get("track", True)})
json.dump({"inputs_digest": "sha256:"+"c"*64, "inputs": inputs}, open(sys.argv[2], "w"))
PY
fi

# ---- run build.sh ----------------------------------------------------------
echo "[validate] $TOOL: running build.sh with stubbed toolchain"
PRE_STATUS="$(cd "$TOOL_DIR" && git status --porcelain 2>/dev/null | wc -l)"
set +e
PATH="$BIN:$PATH" \
EC_COMMON="$EC_COMMON" EC_PACKAGE="$TOOL" EC_IMAGE_NAME=manylinux_2_28_x86_64 \
EC_VERSION=9.9.20260603 EC_TAG=v9.9.20260603 EC_TRIGGER=workflow_dispatch EC_RECIPE_SHA=testsha \
SRC_DIR="$TOOL_DIR" WORK_DIR="$SB/work" OUT_DIR="$SB/dist" CANDIDATE_JSON="$CAND" \
bash "$TOOL_DIR/scripts/build.sh" > "$SB/build.log" 2>&1
RC=$?
set -e
if [ "$RC" -ne 0 ]; then echo "[validate] FAIL: build.sh exited $RC"; tail -30 "$SB/build.log"; exit 1; fi

# ---- assertions ------------------------------------------------------------
tarball="$(ls "$SB"/dist/*.tar.gz 2>/dev/null | head -1)"
[ -n "$tarball" ] || { echo "[validate] FAIL: no tarball produced"; tail -20 "$SB/build.log"; exit 1; }
mkdir -p "$SB/x"; tar xzf "$tarball" -C "$SB/x"
man="$(find "$SB/x" -name manifest.json | head -1)"
[ -n "$man" ] || { echo "[validate] FAIL: no manifest.json in tarball"; exit 1; }

python3 - "$man" "$EC_COMMON/schemas/manifest.schema.json" "$CORE_NAME" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
try:
    import jsonschema; jsonschema.validate(m, json.load(open(sys.argv[2])))
    ok = "valid"
except ImportError:
    ok = "skipped (no jsonschema)"
core = [i for i in m["inputs"] if i["role"] == "core"]
assert core and core[0]["name"] == sys.argv[3], f"core input mismatch: {core}"
assert m["inputs_digest"].startswith("sha256:")
print(f"[validate]   manifest schema: {ok}; package={m['package']}; "
      f"inputs={[i['name'] for i in m['inputs']]}; skills={[s['name'] for s in m.get('skills',[])]}")
PY

# skills + envrc present
find "$SB/x" -name index.json -path '*skills*' | grep -q . || { echo "[validate] FAIL: skills/index.json missing"; exit 1; }
find "$SB/x" -name export.envrc | grep -q . || { echo "[validate] FAIL: export.envrc missing"; exit 1; }

# source tree untouched (no NEW untracked/modified beyond what was already there)
POST_STATUS="$(cd "$TOOL_DIR" && git status --porcelain 2>/dev/null | wc -l)"
if [ "$POST_STATUS" != "$PRE_STATUS" ]; then
  echo "[validate] FAIL: build.sh changed the source tree (status $PRE_STATUS -> $POST_STATUS)"
  (cd "$TOOL_DIR" && git status --porcelain); exit 1
fi

echo "[validate] $TOOL: PASS (manifest + skills + envrc + tarball; source tree clean)"
