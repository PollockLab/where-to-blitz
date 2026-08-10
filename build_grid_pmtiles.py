#!/usr/bin/env python3
"""Build raster PMTiles from the per-group lattice stacks.

One archive per (taxon, goal) per the #87 plan: each goal is a fixed blend of the
0..1 scoring axes (goal_presets.PRESETS - the slider weights were removed in #20, so
every goal is a precomputable scalar field, and a scalar field is a raster). Blends
are computed from the 7-band stack, rendered as RGBA (viridis, alpha=0 for nodata),
and tiled with rio-pmtiles.

Usage:
  python build_grid_pmtiles.py                          # all groups, all resolutions
  python build_grid_pmtiles.py --group Insecta          # one group, all resolutions
  python build_grid_pmtiles.py --res 5000               # all groups, 5km only
  python build_grid_pmtiles.py --group Insecta --res 5000

Output: cluster_results/ca/pmtiles/<group>_<goal>_<res>.pmtiles
"""
import argparse
import concurrent.futures
import json
import subprocess
import tempfile
from pathlib import Path

import matplotlib
import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.warp import transform as warp_transform

from goal_presets import AXES, PRESETS
from grid_schema import BAND_INDEX

HERE = Path(__file__).resolve().parent
OUT = HERE / "cluster_results" / "ca" / "pmtiles"
US_MASK_FILE = HERE / "cluster_results" / "ca" / "us_cells.json"

TILE_BANDS = {ax: BAND_INDEX[ax] for ax in AXES}
# Native zoom per tier: 25km -> z8 (shown z<=9), 5km -> z9 (plan measured 20.9 MB at z0-9).
ZOOM_BY_RES = {"grid_5000m": "0..9", "grid_25000m": "0..8"}

_VIRIDIS = matplotlib.colormaps["viridis"]
_LUT = (np.asarray([_VIRIDIS(i / 255.0) for i in range(256)])[:, :3] * 255).round().astype(np.uint8)

parser = argparse.ArgumentParser()
parser.add_argument("--group", default=None, help="Only build PMTiles for this group")
parser.add_argument("--res", type=int, default=None, help="Only build for this resolution (5000 or 25000)")
args = parser.parse_args()

# Find matching grid directories
if args.res:
    RAST_DIRS = [HERE / "cluster_results" / "ca" / f"grid_{args.res}m"]
else:
    RAST_DIRS = sorted(HERE.glob("cluster_results/ca/grid_*m"))

RAST_DIRS = [d for d in RAST_DIRS if d.exists()]
if not RAST_DIRS:
    print("no lattice stacks found; run build_fullgrid_ca.py first")
    raise SystemExit(1)

OUT.mkdir(parents=True, exist_ok=True)


def _load_us_parent_cells(crs, origin_x, origin_y):
    if not US_MASK_FILE.exists():
        return set()
    with US_MASK_FILE.open() as fh:
        keys = json.load(fh).get("us_cells", [])
    if not keys:
        return set()
    latlon = [tuple(map(float, k.split(","))) for k in keys]
    lats = [p[0] for p in latlon]
    lons = [p[1] for p in latlon]
    xs, ys = warp_transform("EPSG:4326", crs, lons, lats)
    out = set()
    for x, y in zip(xs, ys):
        c = int(np.floor((x - origin_x) / 25000.0))
        r = int(np.floor((origin_y - y) / 25000.0))
        out.add((r, c))
    return out


def _hidden_mask(height, width, res_m, us_parent_cells):
    if not us_parent_cells:
        return np.zeros((height, width), dtype=bool)
    if res_m == 25000:
        hidden = np.zeros((height, width), dtype=bool)
        for r, c in us_parent_cells:
            if 0 <= r < height and 0 <= c < width:
                hidden[r, c] = True
        return hidden
    if res_m == 5000:
        parent_h = height // 5
        parent_w = width // 5
        hidden_parent = np.zeros((parent_h, parent_w), dtype=bool)
        for r, c in us_parent_cells:
            if 0 <= r < parent_h and 0 <= c < parent_w:
                hidden_parent[r, c] = True
        return np.repeat(np.repeat(hidden_parent, 5, axis=0), 5, axis=1)[:height, :width]
    return np.zeros((height, width), dtype=bool)


_US_PARENT_CACHE = None

jobs = []  # (tmp_tif, out_path, zoom); rendered serially, tiled in parallel below
for rdir in RAST_DIRS:
    res_label = rdir.name
    res_m = int(res_label.split("_")[1].replace("m", ""))
    tifs = sorted(rdir.glob("*.tif"))
    for tif in tifs:
        if tif.stem == "index":
            continue
        group = tif.stem
        if args.group and group != args.group.replace(" ", "_"):
            continue
        with rasterio.open(tif) as src:
            crs = src.crs
            transform = src.transform
            height, width = src.height, src.width
            if _US_PARENT_CACHE is None:
                _US_PARENT_CACHE = _load_us_parent_cells(crs, transform.c, transform.f)
            hidden_us = _hidden_mask(height, width, res_m, _US_PARENT_CACHE)

            # Read the five scoring axes once; each preset is a linear blend of them.
            axes = {ax: src.read(TILE_BANDS[ax]).astype(np.float64) for ax in AXES}
            for preset in PRESETS:
                slug = preset["name"].lower().replace(" ", "_")
                data = np.zeros((height, width), dtype=np.float64)
                for ax, w in zip(AXES, preset["w"], strict=True):
                    if w:
                        data += w * np.where(np.isfinite(axes[ax]), axes[ax], 0.0)
                data = np.clip(data, 0, 1)
                valid = np.isfinite(axes["discover"]) & ~hidden_us  # hide foreign-border cells in both tiers
                norm = np.where(valid, np.clip(data, 0, 1), 0)
                idx = (norm * 255).round().astype(np.uint8)
                rgb = _LUT[idx]
                alpha = np.where(valid, 255, 0).astype(np.uint8)

                out = OUT / f"{group}_{slug}_{res_label}.pmtiles"

                with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
                    tmp_path = tmp.name
                with rasterio.open(tmp_path, "w", driver="GTiff",
                                   width=width, height=height, count=4,
                                   dtype="uint8", crs=crs, transform=transform,
                                   compress="deflate", tiled=True,
                                   blockxsize=512, blockysize=512) as dst:
                    dst.write(rgb[..., 0], 1)
                    dst.write(rgb[..., 1], 2)
                    dst.write(rgb[..., 2], 3)
                    dst.write(alpha, 4)
                    dst.colorinterp = [ColorInterp.red, ColorInterp.green,
                                       ColorInterp.blue, ColorInterp.alpha]

                zoom = ZOOM_BY_RES.get(res_label, "0..9")
                print(f"  staged {tif.name} ({preset['name']}) -> {out.name} (z{zoom})")
                jobs.append((tmp_path, out, zoom))


def _tile(job):
    tmp_path, out, zoom = job
    subprocess.run(["rio", "pmtiles", tmp_path, str(out),
                    "--zoom-levels", zoom,
                    "--format", "WEBP",
                    "--resampling", "average"],
                   check=True, capture_output=True)
    Path(tmp_path).unlink()
    return out


print(f"tiling {len(jobs)} files with 4 workers ...")
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
    for out in ex.map(_tile, jobs):
        print(f"    wrote {out.name} ({out.stat().st_size / 1e6:.1f} MB)")

print("done")
