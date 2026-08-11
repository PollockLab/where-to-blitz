#!/usr/bin/env bash
# Build the grid + tiles and publish the large binaries to a GitHub Release.
#
# Release assets (not git, not Pages artifacts) are the distribution channel:
# release download URLs redirect to S3-backed storage that supports HTTP range
# requests, which is all the pmtiles:// protocol needs. This mirrors how
# ca_travel_time.tif already ships via the grid-inputs-v1 release.
#
# Usage:  ./publish_grid_release.sh           (build + upload)
#         SKIP_BUILD=1 ./publish_grid_release.sh   (upload only)
set -euo pipefail
cd "$(dirname "$0")"
REPO="PollockLab/where-to-blitz"
REL="${GRID_OUTPUTS_RELEASE:-grid-outputs-v1}"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  ./rebuild_grid.sh
fi

# Create the release if it doesn't exist yet (rolling tag; notes record provenance)
if ! gh release view "$REL" --repo "$REPO" >/dev/null 2>&1; then
  gh release create "$REL" --repo "$REPO" --title "Grid outputs (auto-built)" \
    --notes "PMTiles + grid outputs from rebuild_grid.sh. Inputs: ${GRID_INPUTS_RELEASE:-grid-inputs-v1}. Rebuilt $(date -u +%Y-%m-%d)."
fi

# Stage with explicit asset names: density tiles get a density_ prefix; the per-(group, goal)
# cell-colour PNGs travel as one grid_values.tar.gz (66 small files make poor release assets).
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
for f in density/*.pmtiles; do
  [ -e "$f" ] || continue
  cp "$f" "$STAGE/density_$(basename "$f")"
done
if [ -d cluster_results/ca/values ]; then
  tar -czf "$STAGE/grid_values.tar.gz" -C cluster_results/ca/values .
fi

n=0
for f in "$STAGE"/*; do
  [ -e "$f" ] || continue
  echo "uploading $(basename "$f") ($(du -h "$f" | cut -f1)) ..."
  gh release upload "$REL" "$f" --clobber --repo "$REPO"
  n=$((n+1))
done
echo "published $n assets -> https://github.com/$REPO/releases/tag/$REL"
