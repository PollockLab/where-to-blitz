#!/usr/bin/env python3
"""Derive the final 25 km lattice stacks from the 5 km stacks (#87 nesting).

The two tiers must be one product at two resolutions: a 25 km cell IS the aggregate
of its 25 constituent 5 km cells. Quantities that are resolution-dependent when
computed natively (the density-weighted climate KDE, the 0.25 deg CSV joins, the
land-weighted travel mean, the n_train count) cannot nest if each tier is computed
independently - the validator measured exactly that (env mean |diff| 0.01, n_train
off by the 25x area factor). So the displayed 25 km stacks are aggregates of the
5 km stacks, and the nesting gate passes by construction.

Per-band rules (band order = build_fullgrid_ca.BANDS):
  discover / conservation / env / staleness / urgency / travel_min  -> NaN-aware mean
  n_train (implied record count, an extensive quantity)             -> NaN-aware sum

The 25 km webapp JSON and the national ramps still come from the native 25 km build
(fit-25km job): the vector tier keeps today's values, and the ramps are fitted at the
reference resolution as designed. Differences between the vector tier and the
aggregated raster tier are within the quantisation step the P3 gate checks.

Usage: python aggregate_25km_from_5km.py [--group Aves]
Reads  cluster_results/ca/grid_5000m/<Group>.tif
Writes cluster_results/ca/grid_25000m/<Group>.tif (overwrites the native build)
"""
import argparse
from pathlib import Path

import numpy as np
import rasterio

from grid_lattice import Lattice, mean_pool_block, sum_pool_block
from grid_schema import BANDS, SUM_BANDS

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--group", help="aggregate only this group (matrix jobs do one each)")

args = parser.parse_args()
GROUP = args.group.replace(" ", "_") if args.group else None

HERE = Path(__file__).resolve().parent
FINE = HERE / "cluster_results" / "ca" / "grid_5000m"
COARSE = HERE / "cluster_results" / "ca" / "grid_25000m"
COARSE.mkdir(parents=True, exist_ok=True)

# CRS comes from a local 5 km stack (same lattice CRS; no network read needed).
_first = next(p for p in sorted(FINE.glob("*.tif")) if p.stem != "index")
with rasterio.open(_first) as _src:
    crs = _src.crs
LAT5 = Lattice(5000, crs)
LAT25 = Lattice(25000, crs)
K = 25000 // 5000
assert LAT25.nrow * K == LAT5.nrow and LAT25.ncol * K == LAT5.ncol, "lattices must align 5x5"

for tif in sorted(FINE.glob("*.tif")):
    if tif.stem == "index" or (GROUP and tif.stem != GROUP):
        continue
    out = COARSE / tif.name
    with rasterio.open(tif) as src:
        n_bands = src.count
        planes = []
        for b in range(1, n_bands + 1):
            a = src.read(b).astype(np.float64)
            band_name = BANDS[b - 1] if b - 1 < len(BANDS) else f"band{b}"
            pool = sum_pool_block if band_name in SUM_BANDS else mean_pool_block
            planes.append(pool(a, K).astype(np.float32))
    with rasterio.open(out, "w", driver="GTiff", width=LAT25.ncol, height=LAT25.nrow,
                       count=n_bands, dtype="float32", crs=LAT25.crs, transform=LAT25.transform,
                       nodata=np.nan, compress="deflate", predictor=3, tiled=True,
                       blockxsize=256, blockysize=256) as dst:
        for i, plane in enumerate(planes, start=1):
            dst.write(plane, i)
            dst.set_band_description(i, BANDS[i - 1] if i - 1 < len(BANDS) else f"band{i}")
    print(f"{tif.name}: {n_bands} bands -> {out.name}")

print("done")
