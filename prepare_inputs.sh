#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
REL="${GRID_INPUTS_RELEASE:-grid-inputs-v1}"
mkdir -p cluster_results
# Download the Weiss travel-time raster from the release if missing
if [ ! -f cluster_results/ca_travel_time.tif ]; then
  echo "Downloading ca_travel_time.tif from release $REL..."
  gh release download "$REL" --repo PollockLab/where-to-blitz -p ca_travel_time.tif -D cluster_results/
fi
# Generate CHELSA bioclim if missing
if [ ! -f cluster_results/ca_bioclim.tif ]; then
  echo "Generating ca_bioclim.tif (CHELSA clip)..."
  "$PY" clip_chelsa_ca.py
fi
# Generate Hansen forest loss if missing
if [ ! -f cluster_results/ca_forestloss.tif ]; then
  echo "Generating ca_forestloss.tif (Hansen clip)..."
  "$PY" build_hansen_ca.py
fi
# Package the inputs
echo "Prepared inputs in cluster_results/" 
