#!/usr/bin/env bash
# Functional tests for build-common.sh. No network: ec_clone_input is exercised
# against a throwaway local git repo. Run: bash tests/shell/test_build_common.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EC_COMMON="$(cd "$HERE/../.." && pwd)"
# shellcheck source=/dev/null
source "$EC_COMMON/scripts/build-common.sh"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf '  ok   - %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL - %s\n' "$1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

# --- ec_init_dirs: local fallback creates .build and dist under SRC_DIR ------
(
    SRC_DIR="$SANDBOX/repo"; mkdir -p "$SRC_DIR"
    unset WORK_DIR OUT_DIR
    ec_init_dirs >/dev/null
    [ -d "$SRC_DIR/.build" ] && [ -d "$SRC_DIR/dist" ]
) && ok "ec_init_dirs creates local .build/ and dist/" || bad "ec_init_dirs local dirs"

# --- ec_require_file ---------------------------------------------------------
( touch "$SANDBOX/exists"; ec_require_file "$SANDBOX/exists" >/dev/null 2>&1 ) \
    && ok "ec_require_file passes for existing file" || bad "ec_require_file existing"
( ec_require_file "$SANDBOX/nope" >/dev/null 2>&1 ) \
    && bad "ec_require_file should fail for missing" || ok "ec_require_file fails for missing file"

# --- ec_clone_input writes only under WORK_DIR, not SRC_DIR -------------------
(
    # build an upstream repo to clone
    UP="$SANDBOX/upstream"; mkdir -p "$UP"
    git -C "$UP" init -q
    git -C "$UP" config user.email t@t; git -C "$UP" config user.name t
    echo hello > "$UP/file.txt"
    git -C "$UP" add -A && git -C "$UP" commit -qm init
    git -C "$UP" tag v1.0.0

    SRC_DIR="$SANDBOX/src"; mkdir -p "$SRC_DIR"
    WORK_DIR="$SANDBOX/work"; OUT_DIR="$SANDBOX/out"
    ec_init_dirs >/dev/null
    dest="$(ec_clone_input upstream "$UP" v1.0.0)"
    # checkout landed under WORK_DIR
    [ -f "$dest/file.txt" ] && [ "$dest" = "$WORK_DIR/upstream" ] || exit 1
    # nothing was written into SRC_DIR
    [ -z "$(ls -A "$SRC_DIR")" ] || exit 1
) && ok "ec_clone_input clones to WORK_DIR and leaves SRC_DIR clean" || bad "ec_clone_input isolation"

# --- ec_make_tarball ---------------------------------------------------------
(
    WORK_DIR="$SANDBOX/work2"; OUT_DIR="$SANDBOX/out2"; mkdir -p "$WORK_DIR" "$OUT_DIR"
    rel="$WORK_DIR/release/widget-1.0"; mkdir -p "$rel/bin"; echo x > "$rel/bin/widget"
    out="$(ec_make_tarball "$rel" widget-1.0.tar.gz)"
    [ -f "$out" ] && [ -f "$OUT_DIR/widget-1.0.tar.gz.sha256" ] \
        && tar tzf "$out" | grep -q '^widget-1.0/bin/widget$'
) && ok "ec_make_tarball produces tarball + sha256 with correct top dir" || bad "ec_make_tarball"

# --- ec_finalize_release: skills + envrc + manifest, all enforced -----------
(
    SRC_DIR="$SANDBOX/ftool"; mkdir -p "$SRC_DIR/scripts" "$SRC_DIR/skills/widget/references"
    WORK_DIR="$SANDBOX/fwork"; OUT_DIR="$SANDBOX/fout"
    ec_init_dirs >/dev/null
    # source artifacts
    printf 'PATH_add bin\n' > "$SRC_DIR/scripts/export.envrc"
    printf 'skills:\n  - name: widget\n    path: skills/widget\n    binaries: [widget]\n' > "$SRC_DIR/scripts/skill-manifest.yaml"
    printf -- '---\nname: widget\ndescription: does widget things\nversion: "1.2"\n---\nbody\n' > "$SRC_DIR/skills/widget/SKILL.md"
    echo ref > "$SRC_DIR/skills/widget/references/cli.md"
    # release tree with the declared binary
    rel="$WORK_DIR/release/widget-1.2"; mkdir -p "$rel/bin"; echo x > "$rel/bin/widget"
    # candidate
    cand="$WORK_DIR/candidate.json"
    cat > "$cand" <<'JSON'
{"inputs_digest":"sha256:0000000000000000000000000000000000000000000000000000000000000000",
 "inputs":[{"name":"widget","role":"core","repo":"r","ref":"v1.2","resolved_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","version":"1.2","tracked":true}]}
JSON
    EC_PACKAGE=widget-bin EC_VERSION=1.2.20260607 EC_TAG=v1.2.20260607 \
      EC_TRIGGER=schedule EC_RECIPE_SHA=deadbeef EC_IMAGE_NAME=manylinux_2_28_x86_64 \
      ec_finalize_release "$SRC_DIR" "$rel" "$cand" >/dev/null
    # assertions
    [ -f "$rel/manifest.json" ] || exit 1
    [ -f "$rel/skills/index.json" ] || exit 1
    [ -f "$rel/export.envrc" ] || exit 1
    [ -f "$OUT_DIR/manifest-manylinux_2_28_x86_64.json" ] || exit 1
    python3 - "$rel/manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
assert m["schema"] == "edapack.manifest/1", m
assert m["package"] == "widget-bin"
assert m["platform"]["libc"] == "glibc_2.28", m["platform"]
assert m["skills"][0]["name"] == "widget"
PY
) && ok "ec_finalize_release emits manifest+skills+envrc and per-platform copy" || bad "ec_finalize_release"

# --- ec_finalize_release fails loudly when a skill binary is missing ---------
(
    SRC_DIR="$SANDBOX/ftool2"; mkdir -p "$SRC_DIR/scripts" "$SRC_DIR/skills/widget"
    WORK_DIR="$SANDBOX/fwork2"; OUT_DIR="$SANDBOX/fout2"
    ec_init_dirs >/dev/null
    printf 'PATH_add bin\n' > "$SRC_DIR/scripts/export.envrc"
    printf 'skills:\n  - name: widget\n    path: skills/widget\n    binaries: [widget]\n' > "$SRC_DIR/scripts/skill-manifest.yaml"
    printf -- '---\nname: widget\ndescription: d\nversion: "1"\n---\n' > "$SRC_DIR/skills/widget/SKILL.md"
    rel="$WORK_DIR/release/widget"; mkdir -p "$rel/bin"   # NOTE: no widget binary
    cand="$WORK_DIR/c.json"; echo '{"inputs_digest":"sha256:'"$(printf '0%.0s' {1..64})"'","inputs":[{"name":"w","role":"core","repo":"r","ref":"v","resolved_sha":"a","version":"1","tracked":true}]}' > "$cand"
    EC_PACKAGE=p EC_VERSION=1 EC_TAG=v1 ec_finalize_release "$SRC_DIR" "$rel" "$cand" >/dev/null 2>&1
) && bad "ec_finalize_release should fail on missing binary" || ok "ec_finalize_release fails on missing skill binary"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
