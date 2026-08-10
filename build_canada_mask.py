"""Tag grid cells that fall outside Canada, for the app's always-on 'Canada only' view.

The classification itself lives in border_mask, so the vector lattice the app clicks and the raster
PMTiles it renders are cut by exactly the same mask — they used to be derived separately and could
disagree at the border. A 25 km cell is hidden only when all 25 of its 5 km children are outside
Canada, so no cell holding Canadian ground is ever made unclickable.

The output keeps its us_cells.json name (the app fetches it by that path); its contents are all
cells hidden from the Canada-only view. Keys match the app's gekey: lat.toFixed(3)+','+lon.toFixed(3).
"""
import glob
import json
import os

import numpy as np
import rasterio
from rasterio.warp import transform as warp_transform

import border_mask

HERE = "cluster_results/ca"
STACK = f"{HERE}/grid_5000m/All_biodiversity.tif"

with rasterio.open(STACK) as src:
    crs, tr = src.crs, src.transform
    fine = border_mask.hidden_fine(crs, tr.c, tr.f, src.height, src.width)
coarse = border_mask.hidden_coarse(fine)

src_json = next(f for f in sorted(glob.glob(f"{HERE}/webapp_data_*.json")) if "gettingeven" not in f)
with open(src_json) as fh:
    d = json.load(fh)
rows = d[next(k for k, v in d.items() if isinstance(v, list))]

xs, ys = warp_transform("EPSG:4326", crs, [r[1] for r in rows], [r[0] for r in rows])
cols = np.floor((np.asarray(xs) - tr.c) / border_mask.COARSE_RES).astype(int)
lines = np.floor((tr.f - np.asarray(ys)) / border_mask.COARSE_RES).astype(int)
inside = (lines >= 0) & (lines < coarse.shape[0]) & (cols >= 0) & (cols < coarse.shape[1])

hidden = [f"{r[0]:.3f},{r[1]:.3f}"
          for r, ln, cl, ok in zip(rows, lines, cols, inside, strict=True) if ok and coarse[ln, cl]]
with open(os.path.join(HERE, "us_cells.json"), "w") as fh:
    json.dump({"us_cells": hidden}, fh, separators=(",", ":"))
print(f"{len(hidden)} / {len(rows)} cells fully outside Canada -> us_cells.json")
