#!/usr/bin/env bash
# edapack reset-root-owned.sh — delete pre-existing root-owned build dirs from a
# tool workspace WITHOUT sudo.
#
# Legacy local builds (run as root in the container) left root-owned directories
# in some repos (e.g. icestorm-bin/{icestorm,libftdi1-*,staging,release},
# yosys-bin/cmake-wrapper). A normal `rm` can't remove them. This helper deletes
# them by running a throwaway container AS ROOT over the bind-mounted workspace —
# the one place root is still legitimately available — so the host user needs no
# sudo.
#
# Usage: reset-root-owned.sh <tool-dir>
# It only targets known transient build dirs plus anything currently owned by
# uid 0; it never touches tracked source.
set -euo pipefail

TOOL_DIR="${1:?usage: reset-root-owned.sh <tool-dir>}"
TOOL_DIR="$(cd "$TOOL_DIR" && pwd)"

# Known transient build dirs across the core tools.
KNOWN=(
  icestorm libftdi1-1.5 libftdi1-build libftdi1-* staging release
  cmake-wrapper .build dist
)

echo "[reset-root-owned] scanning $TOOL_DIR for root-owned files..."
root_owned="$(find "$TOOL_DIR" -maxdepth 3 -uid 0 -print 2>/dev/null | head -20 || true)"
if [ -n "$root_owned" ]; then
  echo "[reset-root-owned] found root-owned paths (showing up to 20):"
  echo "$root_owned" | sed 's/^/    /'
fi

# Build the rm command for the container. Quote-safe via a heredoc script.
docker run --rm -v "$TOOL_DIR:/io" --workdir /io alpine:3 sh -s <<'EOSH'
set -e
for d in icestorm libftdi1-1.5 libftdi1-build libftdi1-* staging release cmake-wrapper .build dist; do
  for path in /io/$d; do
    if [ -e "$path" ]; then
      echo "  removing $path"
      rm -rf "$path"
    fi
  done
done
# Sweep any remaining uid-0 entries at the top two levels.
find /io -maxdepth 2 -uid 0 -exec rm -rf {} + 2>/dev/null || true
EOSH

echo "[reset-root-owned] done. Verifying no root-owned files remain:"
if find "$TOOL_DIR" -uid 0 -print -quit 2>/dev/null | grep -q .; then
  echo "[reset-root-owned] WARNING: root-owned files still present" >&2
  exit 1
fi
echo "[reset-root-owned] workspace is clean."
