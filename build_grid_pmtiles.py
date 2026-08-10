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
import subprocess
import tempfile
from pathlib import Path

import matplotlib
import numpy as np
import rasterio
from rasterio.enums import ColorInterp

from goal_presets import AXES, PRESETS
from grid_schema import BAND_INDEX

HERE = Path(__file__).resolve().parent
OUT = HERE / "cluster_results" / "ca" / "pmtiles"

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

jobs = []  # (tmp_tif, out_path, zoom); rendered serially, tiled in parallel below
for rdir in RAST_DIRS:
    res_label = rdir.name
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

            # Read the five scoring axes once; each preset is a linear blend of them.
            axes = {ax: src.read(TILE_BANDS[ax]).astype(np.float64) for ax in AXES}
            for preset in PRESETS:
                slug = preset["name"].lower().replace(" ", "_")
                data = np.zeros((height, width), dtype=np.float64)
                for ax, w in zip(AXES, preset["w"], strict=True):
                    if w:
                        data += w * np.where(np.isfinite(axes[ax]), axes[ax], 0.0)
                data = np.clip(data, 0, 1)
                valid = np.isfinite(axes["discover"])  # the footprint mask is shared
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
