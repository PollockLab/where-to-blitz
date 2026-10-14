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
#         ./publish_grid_release.sh --package-only /path/grid_site_data_v1.tar.gz
#         ./publish_grid_release.sh --restore     (download app data without rebuilding)
set -euo pipefail
cd "$(dirname "$0")"
REPO="${GITHUB_REPOSITORY:-PollockLab/where-to-blitz}"
REL="${GRID_OUTPUTS_RELEASE:-grid-outputs-v1}"
PY="${PYTHON:-python3}"
CA_DIR="${GRID_DATA_DIR:-cluster_results/ca}"
BUNDLE="grid_site_data_v1.tar.gz"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

case "${1:-}" in
  --package-only)
    [ "$#" -eq 2 ] || { echo "Usage: $0 --package-only /path/$BUNDLE" >&2; exit 2; }
    "$PY" build_provenance.py --package-release "$2" --ca-dir "$CA_DIR"
    exit 0
    ;;
  --restore)
    [ "$#" -eq 1 ] || { echo "Usage: $0 --restore" >&2; exit 2; }
    if ! gh release download "$REL" --pattern "$BUNDLE" --dir "$STAGE" --repo "$REPO"; then
      echo "Missing $BUNDLE on $REL. Publish a validated rebuilt-site artifact before fast deployment." >&2
      exit 1
    fi
    "$PY" build_provenance.py --restore-release "$STAGE/$BUNDLE" --ca-dir "$CA_DIR"
    exit 0
    ;;
  "") ;;
  *) echo "Unknown argument: $1" >&2; exit 2 ;;
esac

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  ./rebuild_grid.sh
fi

# Stage with explicit asset names: density tiles get a density_ prefix; the per-(group, goal)
# colours and current JSON/provenance must travel together. Validate before publishing anything.
"$PY" build_provenance.py --package-release "$STAGE/$BUNDLE" --ca-dir "$CA_DIR"
for f in density/*.pmtiles; do
  [ -e "$f" ] || continue
  cp "$f" "$STAGE/density_$(basename "$f")"
done
# Retain the values-only asset for older consumers; fast deploy uses the complete bundle.
tar -czf "$STAGE/grid_values.tar.gz" -C "$CA_DIR/values" .

# Create the release if it doesn't exist yet (rolling tag; notes record provenance)
if ! gh release view "$REL" --repo "$REPO" >/dev/null 2>&1; then
  gh release create "$REL" --repo "$REPO" --title "Grid outputs (auto-built)" \
    --notes "PMTiles + grid outputs from rebuild_grid.sh. Inputs: ${GRID_INPUTS_RELEASE:-grid-inputs-v1}. Rebuilt $(date -u +%Y-%m-%d)."
fi

n=0
for f in "$STAGE"/*; do
  [ -e "$f" ] || continue
  echo "uploading $(basename "$f") ($(du -h "$f" | cut -f1)) ..."
  gh release upload "$REL" "$f" --clobber --repo "$REPO"
  n=$((n+1))
done
echo "published $n assets -> https://github.com/$REPO/releases/tag/$REL"
