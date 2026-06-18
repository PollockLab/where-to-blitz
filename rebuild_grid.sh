#!/usr/bin/env bash
# Reproducible rebuild of the Canada where-to-blitz grid (issue #58).
#
# Streams the per-taxon iNaturalist density from the public Biodiversite Quebec bucket and
# regenerates the climate/forest-loss rasters from public sources, so the only non-streamable
# input is the Weiss 2018 travel-time raster, fetched from this repo's GitHub Release.
#
# Usage:  ./rebuild_grid.sh        (needs: python env from requirements.txt, gh CLI authed)
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
REL="${GRID_INPUTS_RELEASE:-grid-inputs-v1}"

mkdir -p cluster_results
# 1) the one input with no public re-fetch script: Weiss 2018 travel-time (Canada clip), from the Release
if [ ! -f cluster_results/ca_travel_time.tif ]; then
  echo "fetching ca_travel_time.tif from release $REL ..."
  gh release download "$REL" --repo PollockLab/where-to-blitz -p ca_travel_time.tif -D cluster_results/
fi
# 2) regenerate the public-source rasters (idempotent; skip if present)
[ -f cluster_results/ca_bioclim.tif ]    || "$PY" clip_chelsa_ca.py     # CHELSA bioclimate (streamed /vsicurl)
[ -f cluster_results/ca_forestloss.tif ] || "$PY" build_hansen_ca.py    # Hansen Global Forest Change
# 3) build the grid: streams density COGs from the bucket, joins conservation/staleness from the committed CSVs
"$PY" build_fullgrid_ca.py
# 3b) re-tag out-of-Canada cells for the Canada-only view mask (us_cells.json). MUST follow the grid build:
#     if the grid's cell set changes, a stale mask leaks US coastal cells into the default view (#58 follow-up).
"$PY" build_canada_mask.py
# 4) regenerate the deployed single-page app
"$PY" build_webapp.py
echo "done — cluster_results/ca/ + index.html regenerated."
