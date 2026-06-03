#!/usr/bin/env bash
# edapack local-build.sh — run a tool's manylinux build locally, ROOTLESS.
#
# Why this exists: the legacy per-tool wrappers ran the container as root with
# the workspace bind-mounted and build trees written in-tree, leaving
# root-owned files in the repo that needed sudo to remove. This wrapper instead:
#   * runs the builder image as the host UID/GID (--user), so nothing it writes
#     is root-owned;
#   * mounts the source read-only and directs all scratch to a named docker
#     volume (never the workspace);
#   * lands only the final tarball + manifest in ./dist (host-owned).
#
# Usage:
#   local-build.sh <tool-dir> [build]   # default: build
#   local-build.sh <tool-dir> clean     # remove the work volume + ./dist
#   local-build.sh <tool-dir> shell     # interactive shell in the builder
#
# Env:
#   EC_IMAGE     override builder image (default ghcr.io/edapack/<IMAGE_NAME>)
#   IMAGE_NAME   manylinux variant (default manylinux_2_28_x86_64)
#   core_ref / input_overrides   passed through to the build as env
set -euo pipefail

TOOL_DIR="${1:?usage: local-build.sh <tool-dir> [build|clean|shell]}"
CMD="${2:-build}"
TOOL_DIR="$(cd "$TOOL_DIR" && pwd)"
# This script lives in edapack-common/scripts; the repo root is its grandparent.
EC_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL="$(basename "$TOOL_DIR")"
IMAGE_NAME="${IMAGE_NAME:-manylinux_2_28_x86_64}"
IMAGE="${EC_IMAGE:-ghcr.io/edapack/${IMAGE_NAME}}"
VOLUME="edapack-${TOOL}-work"
DIST="$TOOL_DIR/dist"

log() { printf '[local-build] %s\n' "$*"; }

case "$CMD" in
  clean)
    log "removing work volume '$VOLUME' and $DIST"
    docker volume rm "$VOLUME" >/dev/null 2>&1 || log "(volume not present)"
    rm -rf "$DIST"
    log "clean complete (no sudo required)"
    exit 0
    ;;
  build|shell) ;;
  *) echo "unknown command: $CMD" >&2; exit 2 ;;
esac

mkdir -p "$DIST"
docker volume create "$VOLUME" >/dev/null

# Avoiding root-owned files depends on the daemon mode:
#   * rootful daemon (GitHub Actions, most CI): container-root maps to host
#     root, so we MUST run as the host UID to keep outputs host-owned.
#   * rootless daemon: container-root already maps to the host user, and
#     forcing --user to an arbitrary in-container uid breaks bind-mount writes.
# Detect rootless and only pass --user when running rootful. EC_RUN_AS_USER
# may force the behavior (1=yes, 0=no).
detect_user_flag() {
  if [ -n "${EC_RUN_AS_USER:-}" ]; then
    [ "$EC_RUN_AS_USER" = "1" ] && echo "yes" || echo "no"; return
  fi
  if docker info -f '{{range .SecurityOptions}}{{println .}}{{end}}' 2>/dev/null \
       | grep -q 'name=rootless'; then
    echo "no"
  else
    echo "yes"
  fi
}

common_args=(
  --rm
  -v "$TOOL_DIR:/src:ro"
  -v "$VOLUME:/work"
  -v "$DIST:/dist"
  -e SRC_DIR=/src -e WORK_DIR=/work -e OUT_DIR=/dist
  -e EC_COMMON=/ec-common
  -e EC_IMAGE_NAME="$IMAGE_NAME"
  -e core_ref="${core_ref:-}"
  -e input_overrides="${input_overrides:-}"
  -v "$EC_COMMON_DIR:/ec-common:ro"
  -w /work
)

if [ "$(detect_user_flag)" = "yes" ]; then
  common_args+=(--user "$(id -u):$(id -g)")
  log "daemon=rootful -> running as $(id -u):$(id -g)"
else
  log "daemon=rootless -> container user already maps to host user"
fi

if [ "$CMD" = "shell" ]; then
  exec docker run -it "${common_args[@]}" "$IMAGE" /bin/bash
fi

log "tool=$TOOL image=$IMAGE volume=$VOLUME"
log "outputs -> $DIST"
docker run "${common_args[@]}" "$IMAGE" /src/scripts/build.sh
log "build complete; artifacts in $DIST"
ls -1 "$DIST" 2>/dev/null || true
