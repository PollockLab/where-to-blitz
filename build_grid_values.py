#!/usr/bin/env python3
"""Export per-(taxon, goal) cell colours as small PNGs for client-side grid rendering.

Successor to build_grid_pmtiles.py (#116). The raster PMTiles route warped the LAEA
lattice into axis-aligned Mercator pixels, so every rotated cell edge became a staircase
that could only approximate the vector lattice the app draws for outlines and clicks.
Rendering the colours client-side from the same cellPoly() math kills that seam: the
coloured cells and the clickable lattice are the same polygons, aligned by construction
at every zoom.

Each PNG is the lattice grid itself, one pixel per cell (5 km: 1100x905, 25 km: 220x181),
carrying the exact colour the tiles used to bake: viridis(clip(blend / preset scale, 0, 1)),
with
alpha 0 for nodata/foreign-border cells. The webapp fetches the PNG, reads it back
through a canvas, and paints its cell polygons with the per-cell colours.

Usage:
  python build_grid_values.py                          # all groups, all resolutions
  python build_grid_values.py --group Insecta          # one group, all resolutions
  python build_grid_values.py --res 5000               # all groups, 5km only

Output: cluster_results/ca/values/<group>_<goal>_grid_<res>m.png
"""
import argparse
from pathlib import Path

import matplotlib
import matplotlib.image
import numpy as np
import rasterio

import border_mask
from goal_presets import AXES, PRESETS
from grid_schema import BAND_INDEX

HERE = Path(__file__).resolve().parent
OUT = HERE / "cluster_results" / "ca" / "values"

BANDS = {ax: BAND_INDEX[ax] for ax in AXES}

_VIRIDIS = matplotlib.colormaps["viridis"]
_LUT = (np.asarray([_VIRIDIS(i / 255.0) for i in range(256)])[:, :3] * 255).round().astype(np.uint8)


def render_goal_rgba(tif, preset, res_m):
    """The RGBA cell image for one goal blend: viridis colour + validity alpha.

    Same maths the PMTiles route baked into tiles, kept in one callable so the
    parity test can hold the PNGs to it.
    """
    with rasterio.open(tif) as src:
        height, width = src.height, src.width
        hidden = border_mask.hidden_for_tier(height, width, res_m, src.crs, src.transform.c, src.transform.f)
        axes = {ax: src.read(BANDS[ax]).astype(np.float64) for ax in AXES}
    data = np.zeros((height, width), dtype=np.float64)
    for ax, w in zip(AXES, preset["w"], strict=True):
        if w:
            data += w * np.where(np.isfinite(axes[ax]), axes[ax], 0.0)
    valid = np.isfinite(axes["discover"]) & ~hidden  # hide foreign-border cells in both tiers
    norm = np.where(valid, np.clip(data / preset["scale"], 0, 1), 0)
    idx = (norm * 255).round().astype(np.uint8)
    rgba = np.dstack([_LUT[idx], np.where(valid, 255, 0).astype(np.uint8)])
    return rgba


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", default=None, help="Only export values for this group")
    parser.add_argument("--res", type=int, default=None, help="Only export this resolution (5000 or 25000)")
    args = parser.parse_args()

    if args.res:
        rast_dirs = [HERE / "cluster_results" / "ca" / f"grid_{args.res}m"]
    else:
        rast_dirs = sorted(HERE.glob("cluster_results/ca/grid_*m"))
    rast_dirs = [d for d in rast_dirs if d.exists()]
    if not rast_dirs:
        print("no lattice stacks found; run build_fullgrid_ca.py first")
        raise SystemExit(1)

    OUT.mkdir(parents=True, exist_ok=True)
    for rdir in rast_dirs:
        res_label = rdir.name
        res_m = int(res_label.split("_")[1].replace("m", ""))
        for tif in sorted(rdir.glob("*.tif")):
            if tif.stem == "index":
                continue
            group = tif.stem
            if args.group and group != args.group.replace(" ", "_"):
                continue
            for preset in PRESETS:
                slug = preset["name"].lower().replace(" ", "_")
                rgba = render_goal_rgba(tif, preset, res_m)
                out = OUT / f"{group}_{slug}_{res_label}.png"
                matplotlib.image.imsave(out, rgba)
                print(f"  wrote {out.name} ({out.stat().st_size / 1e3:.0f} kB)")
    print("done")


if __name__ == "__main__":
    main()
