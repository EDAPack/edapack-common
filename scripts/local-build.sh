#!/usr/bin/env bash
# edapack local-build.sh — run a tool's manylinux build locally in the STOCK
# manylinux image, installing deps at build time. No custom images.
#
# The shared scripts are delivered via ivpm: run `ivpm update -a` in the tool
# repo first so packages/edapack-common (this repo) is present; build.sh finds
# EC_COMMON there automatically.
#
# Keeping the source tree clean: all scratch goes to a named docker volume
# (/work) and only the tarball + manifest land in the tool's ./dist. The stock
# image needs root to install packages, so the container runs as root; on a
# rootful daemon that would leave root-owned files in ./dist, so we hand them
# back to you afterward (on a rootless daemon they are already yours).
#
# Usage:
#   local-build.sh <tool-dir> [build]    # default: build
#   local-build.sh <tool-dir> clean      # remove work volume + ./dist (no sudo)
#   local-build.sh <tool-dir> shell      # interactive shell in the image
#
# Env: IMAGE_NAME (default manylinux_2_28_x86_64), EC_IMAGE (full image override),
#      core_ref / input_overrides (passed through).
set -euo pipefail

TOOL_DIR="${1:?usage: local-build.sh <tool-dir> [build|clean|shell]}"
CMD="${2:-build}"
TOOL_DIR="$(cd "$TOOL_DIR" && pwd)"
TOOL="$(basename "$TOOL_DIR")"
IMAGE_NAME="${IMAGE_NAME:-manylinux_2_28_x86_64}"
IMAGE="${EC_IMAGE:-quay.io/pypa/${IMAGE_NAME}}"
VOLUME="edapack-${TOOL}-work"
DIST="$TOOL_DIR/dist"

log() { printf '[local-build] %s\n' "$*"; }

# sudo-free removal of a (possibly root-owned) path via a throwaway container.
rm_maybe_root() {
    rm -rf "$1" 2>/dev/null || docker run --rm -v "$(dirname "$1"):/p" "$IMAGE" rm -rf "/p/$(basename "$1")"
}

case "$CMD" in
  clean)
    log "removing work volume '$VOLUME' and $DIST"
    docker volume rm "$VOLUME" >/dev/null 2>&1 || log "(volume not present)"
    rm_maybe_root "$DIST"
    log "clean complete (no sudo required)"
    exit 0 ;;
  build|shell) ;;
  *) echo "unknown command: $CMD" >&2; exit 2 ;;
esac

if [ ! -f "$TOOL_DIR/packages/edapack-common/scripts/build-common.sh" ]; then
    log "NOTE: packages/edapack-common not found in $TOOL — run 'ivpm update -a' in the tool repo first."
fi

mkdir -p "$DIST"
docker volume create "$VOLUME" >/dev/null

common_args=(
  --rm
  -v "$TOOL_DIR:/src"
  -v "$VOLUME:/work"
  -e SRC_DIR=/src -e WORK_DIR=/work -e OUT_DIR=/src/dist
  -e EC_COMMON=/src/packages/edapack-common
  -e EC_INSTALL_DEPS=1
  -e EC_IMAGE_NAME="$IMAGE_NAME"
  -e core_ref="${core_ref:-}"
  -e input_overrides="${input_overrides:-}"
  -w /work
)

if [ "$CMD" = "shell" ]; then
  exec docker run -it "${common_args[@]}" "$IMAGE" /bin/bash
fi

log "tool=$TOOL image=$IMAGE volume=$VOLUME  ->  $DIST"
docker run "${common_args[@]}" "$IMAGE" bash /src/scripts/build.sh

# On a rootful daemon the outputs are root-owned; hand them back. On a rootless
# daemon container-root already maps to you, so this is a harmless no-op.
if ! docker info -f '{{range .SecurityOptions}}{{println .}}{{end}}' 2>/dev/null | grep -q 'name=rootless'; then
  docker run --rm -v "$DIST:/dist" "$IMAGE" chown -R "$(id -u):$(id -g)" /dist 2>/dev/null || true
fi

log "build complete; artifacts in $DIST"
ls -1 "$DIST" 2>/dev/null || true
