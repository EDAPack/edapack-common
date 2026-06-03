#!/usr/bin/env bash
# edapack build-common.sh — shared shell library sourced by each tool's
# scripts/build.sh. Provides directory conventions that keep all transient
# build state OUT of the bind-mounted workspace (so local rootless builds never
# write into the source tree), plus thin wrappers around the shared Python
# tools (stage-skills, gen-manifest) and tarball packaging.
#
# Contract — the caller may pre-set these; ec_init_dirs fills sane defaults:
#   SRC_DIR   read-only source checkout (the tool repo)            [default: PWD]
#   WORK_DIR  writable scratch: clones, build trees, staging       [default: $SRC_DIR/.build or /work]
#   OUT_DIR   where the final tarball + manifest land (host-owned) [default: $SRC_DIR/dist or /dist]
#   EC_COMMON edapack-common checkout (where this file lives)      [auto-detected]
#
# All functions are namespaced ec_*. Source this file; do not execute it.

# Resolve EC_COMMON to the repo containing this script (scripts/..).
if [ -z "${EC_COMMON:-}" ]; then
    _ec_self="${BASH_SOURCE[0]}"
    EC_COMMON="$(cd "$(dirname "${_ec_self}")/.." && pwd)"
fi

# ec_log MESSAGE...  — to stderr, so value-returning functions keep stdout clean.
ec_log() { printf '[edapack] %s\n' "$*" >&2; }

# ec_die MESSAGE...  — print to stderr and exit non-zero.
ec_die() { printf '[edapack] ERROR: %s\n' "$*" >&2; exit 1; }

# ec_init_dirs — establish SRC_DIR/WORK_DIR/OUT_DIR with container-vs-local
# defaults and create the writable ones. In the build container we mount the
# source at /src (read-only), scratch at /work, output at /dist. Locally we
# fall back to in-repo .build/ and dist/ (both gitignored).
ec_init_dirs() {
    : "${SRC_DIR:=$PWD}"
    if [ -d /work ] && [ -d /src ]; then
        : "${WORK_DIR:=/work}"
        : "${OUT_DIR:=/dist}"
    else
        : "${WORK_DIR:=$SRC_DIR/.build}"
        : "${OUT_DIR:=$SRC_DIR/dist}"
    fi
    mkdir -p "$WORK_DIR" "$OUT_DIR"
    ec_log "SRC_DIR=$SRC_DIR"
    ec_log "WORK_DIR=$WORK_DIR"
    ec_log "OUT_DIR=$OUT_DIR"
    # git may refuse to operate on a bind-mounted repo owned by another uid.
    git config --global --add safe.directory "$SRC_DIR" 2>/dev/null || true
    git config --global --add safe.directory '*' 2>/dev/null || true
}

# ec_clone_input NAME REPO REF [SUBDIR]
# Clone REPO@REF into $WORK_DIR/<SUBDIR|NAME>. Never writes into SRC_DIR.
# Echoes the checkout path on stdout.
ec_clone_input() {
    local name="$1" repo="$2" ref="$3" subdir="${4:-$1}"
    local dest="$WORK_DIR/$subdir"
    [ -n "$name" ] && [ -n "$repo" ] && [ -n "$ref" ] || ec_die "ec_clone_input: name/repo/ref required"
    rm -rf "$dest"
    ec_log "clone $name: $repo @ $ref -> $dest"
    git clone --quiet "$repo" "$dest" >/dev/null
    git -C "$dest" checkout --quiet "$ref" 2>/dev/null \
        || git -C "$dest" checkout --quiet "FETCH_HEAD" 2>/dev/null \
        || ec_die "ec_clone_input: cannot checkout $ref of $repo"
    git -C "$dest" submodule update --init --recursive --quiet 2>/dev/null || true
    printf '%s\n' "$dest"
}

# ec_require_file PATH [DESCRIPTION]  — hard-fail if a release artifact is missing.
ec_require_file() {
    local path="$1" desc="${2:-$1}"
    [ -e "$path" ] || ec_die "required release artifact missing: $desc ($path)"
}

# ec_stage_skills SOURCE_ROOT RELEASE_ROOT [--strict]
# Validate + copy skills and write skills/index.json. Requires
# $SOURCE_ROOT/scripts/skill-manifest.yaml.
ec_stage_skills() {
    local source_root="$1" release_root="$2"; shift 2
    local manifest="$source_root/scripts/skill-manifest.yaml"
    ec_require_file "$manifest" "skill-manifest.yaml"
    python3 "$EC_COMMON/scripts/stage-skills.py" \
        --manifest "$manifest" \
        --source-root "$source_root" \
        --release-root "$release_root" \
        --dest "$release_root/skills" "$@"
}

# ec_copy_envrc SOURCE_ROOT RELEASE_ROOT  — ship export.envrc, fail if absent.
ec_copy_envrc() {
    local source_root="$1" release_root="$2"
    local envrc="$source_root/scripts/export.envrc"
    ec_require_file "$envrc" "export.envrc"
    cp "$envrc" "$release_root/export.envrc"
}

# ec_emit_manifest CANDIDATE_JSON RELEASE_ROOT PLATFORM_JSON
# Assemble manifest.json into the release root using release metadata from the
# environment (EC_PACKAGE, EC_VERSION, EC_TAG, EC_BUILT_AT, EC_TRIGGER,
# EC_RECIPE_SHA). PLATFORM_JSON may be "" to omit the platform block.
ec_emit_manifest() {
    local candidate="$1" release_root="$2" platform="$3"
    : "${EC_BUILT_AT:=$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
    local args=(assemble
        --candidate "$candidate"
        --package "${EC_PACKAGE:?EC_PACKAGE unset}"
        --version "${EC_VERSION:?EC_VERSION unset}"
        --tag "${EC_TAG:?EC_TAG unset}"
        --built-at "$EC_BUILT_AT"
        --trigger "${EC_TRIGGER:-push}"
        --recipe-sha "${EC_RECIPE_SHA:-unknown}"
        --skills-index "$release_root/skills/index.json"
        --output "$release_root/manifest.json")
    [ -n "$platform" ] && args+=(--platform "$platform")
    python3 "$EC_COMMON/scripts/gen-manifest.py" "${args[@]}"
}

# ec_platform_json OUTPATH  — write a platform block {os, arch, libc, image}.
# Derives libc from EC_IMAGE_NAME (manylinux_2_28 -> glibc_2.28) when set,
# else from `ldd --version`.
ec_platform_json() {
    local out="$1" arch os libc image
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"
    if [ -n "${EC_IMAGE_NAME:-}" ]; then
        case "$EC_IMAGE_NAME" in
            *2_28*) libc="glibc_2.28" ;;
            *2_34*) libc="glibc_2.34" ;;
            *2014*) libc="glibc_2.17" ;;
            *)      libc="unknown" ;;
        esac
        image="ghcr.io/edapack/${EC_IMAGE_NAME}"
    else
        libc="$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1 | sed 's/^/glibc_/' || echo unknown)"
        image="local"
    fi
    python3 - "$out" "$os" "$arch" "$libc" "$image" <<'PY'
import json, sys
out, os_, arch, libc, image = sys.argv[1:6]
json.dump({"os": os_, "arch": arch, "libc": libc, "image": image}, open(out, "w"))
PY
}

# ec_finalize_release SOURCE_ROOT RELEASE_ROOT CANDIDATE_JSON
# One call to do the standard tail of every build: generate the platform block,
# stage skills, ship envrc, emit the in-tarball manifest, export a per-platform
# manifest copy to OUT_DIR (for the publish merge), and enforce presence.
ec_finalize_release() {
    local source_root="$1" release_root="$2" candidate="$3"
    local platform="$WORK_DIR/platform.json"
    ec_platform_json "$platform"
    ec_stage_skills "$source_root" "$release_root" --strict
    ec_copy_envrc "$source_root" "$release_root"
    ec_emit_manifest "$candidate" "$release_root" "$platform"
    ec_require_file "$release_root/skills/index.json" "skills/index.json"
    ec_require_file "$release_root/export.envrc" "export.envrc"
    ec_require_file "$release_root/manifest.json" "manifest.json"
    # Export a per-platform copy for the publish step's merge.
    cp "$release_root/manifest.json" "$OUT_DIR/manifest-${EC_IMAGE_NAME:-local}.json"
}

# ec_make_tarball RELEASE_ROOT TARBALL_NAME  — tar.gz into $OUT_DIR.
# RELEASE_ROOT's basename becomes the top-level dir in the archive.
ec_make_tarball() {
    local release_root="$1" name="$2"
    local parent base out
    parent="$(cd "$(dirname "$release_root")" && pwd)"
    base="$(basename "$release_root")"
    out="$OUT_DIR/$name"
    ec_log "packaging $out"
    tar -C "$parent" -czf "$out" "$base"
    if command -v sha256sum >/dev/null 2>&1; then
        ( cd "$OUT_DIR" && sha256sum "$name" > "$name.sha256" )
    fi
    printf '%s\n' "$out"
}
