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

# If the workflow/runner pre-fetched an artifact into rebuilt-site-$INPUTS_HASH, restore and exit.
if [ -n "${INPUTS_HASH:-}" ] && [ -d "rebuilt-site-$INPUTS_HASH" ]; then
  echo "Found extracted artifact rebuilt-site-$INPUTS_HASH — restoring artifacts and skipping rebuild"
  if [ -d "rebuilt-site-$INPUTS_HASH/cluster_results/ca" ]; then
    mkdir -p cluster_results/ca
    cp -R rebuilt-site-$INPUTS_HASH/cluster_results/ca/* cluster_results/ca/ || true
  fi
  if [ -f "rebuilt-site-$INPUTS_HASH/index.html" ]; then
    cp rebuilt-site-$INPUTS_HASH/index.html index.html || true
  fi
  echo "Restored from artifact; exiting."
  exit 0
fi

mkdir -p cluster_results
# 1) the one input with no public re-fetch script: Weiss 2018 travel-time (Canada clip), from the Release
if [ ! -f cluster_results/ca_travel_time.tif ]; then
  echo "fetching ca_travel_time.tif from release $REL ..."
  gh release download "$REL" --repo PollockLab/where-to-blitz -p ca_travel_time.tif -D cluster_results/
fi
# 2) regenerate the public-source rasters (idempotent; skip if present)
[ -f cluster_results/ca_bioclim.tif ]    || "$PY" clip_chelsa_ca.py     # CHELSA bioclimate (streamed /vsicurl)
[ -f cluster_results/ca_forestloss.tif ] || "$PY" build_hansen_ca.py    # Hansen Global Forest Change
# 3) build the grid: streams density COGs from the bucket, joins conservation/staleness from the committed CSVs.
#    Two tiers on the same projected equal-area lattice (#87); build the fine tier first and aggregate
#    to the coarser tier to guarantee nesting (5 km -> 25 km).
GRID_RES=5000  "$PY" build_fullgrid_ca.py
GRID_RES=25000 "$PY" build_fullgrid_ca.py
# 3b) re-tag out-of-Canada cells for the Canada-only view mask (us_cells.json). MUST follow the grid build:
#     if the grid's cell set changes, a stale mask leaks US coastal cells into the default view (#58 follow-up).
"$PY" build_canada_mask.py
# 3c) rebuild the Getting Even layer. MUST follow the grid build: it is keyed on cell centres,
#     so a stale copy from an older lattice matches nothing and the layer renders entirely grey.
"$PY" build_gettingeven.py
# 4) build density overlay PMTiles (per-taxon density RGBA -> pmtiles)
"$PY" build_density_pmtiles.py
# 5) export per-(group, goal) cell colours for client-side grid rendering (#116)
"$PY" build_grid_values.py
# 6) regenerate the deployed single-page app
"$PY" build_webapp.py
echo "done — cluster_results/ca/ + index.html regenerated."
