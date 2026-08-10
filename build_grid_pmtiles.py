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
import math
import subprocess
import tempfile
from pathlib import Path

import matplotlib
import numpy as np
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.transform import array_bounds
from rasterio.warp import calculate_default_transform, reproject
from rasterio.warp import transform as warp_transform

import border_mask
from border_mask import COARSE_RES
from goal_presets import AXES, PRESETS
from grid_schema import BAND_INDEX

MERCATOR = "EPSG:3857"

HERE = Path(__file__).resolve().parent
OUT = HERE / "cluster_results" / "ca" / "pmtiles"

TILE_BANDS = {ax: BAND_INDEX[ax] for ax in AXES}
# Native zoom per tier: 25km -> z8 (shown z<=9), 5km -> z9 (plan measured 20.9 MB at z0-9).
ZOOM_BY_RES = {"grid_5000m": "0..9", "grid_25000m": "0..8"}

_VIRIDIS = matplotlib.colormaps["viridis"]
_LUT = (np.asarray([_VIRIDIS(i / 255.0) for i in range(256)])[:, :3] * 255).round().astype(np.uint8)


def _hidden_mask(height, width, res_m, crs, origin_x, origin_y):
    """Cells outside Canada at this tier's own resolution (see border_mask)."""
    key = (str(crs), origin_x, origin_y, height, width, res_m)
    if key not in _MASK_CACHE:
        if res_m == COARSE_RES:
            fine = border_mask.hidden_fine(crs, origin_x, origin_y, height * 5, width * 5)
            _MASK_CACHE[key] = border_mask.hidden_coarse(fine)
        else:
            _MASK_CACHE[key] = border_mask.hidden_fine(crs, origin_x, origin_y, height, width)
    return _MASK_CACHE[key]


_MASK_CACHE = {}


# A LAEA cell is a square that appears as a *rotated* quadrilateral once warped into Mercator
# (the vector lattice draws this shape faithfully -- see cellPoly() in webapp/index.html). A
# GeoTIFF pixel grid cannot itself be rotated, so that shape can only be approximated by a
# staircase of small axis-aligned pixels. SUPERSAMPLE sets how many of those pixels span one
# source cell at the worst (southernmost, most Mercator-compressed) row; too few and every cell
# comes out as a plain unrotated rectangle -- the raster tiers silently stop matching the vector
# lattice (#116). 4 was the smallest factor that made the rotation visible under nearest
# resampling in a corner-of-country crop test; it costs ~16x the pixels of the unsampled pin but
# rio-pmtiles' own zoom pyramid (average resampling) is what turns the staircase into a clean
# diagonal edge at every zoom, so this only has to be "enough to stair-step", not "photo real".
SUPERSAMPLE = 4


def _write_mercator(path, bands, src_crs, src_transform, width, height):
    """Write the RGBA cell image as an EPSG:3857 GeoTIFF.

    rio-pmtiles derives its tile envelope from the source corners transformed to WGS84 — the
    plain corner transform, not the densifying one. A LAEA extent's edges bow poleward between
    its corners, so that envelope clipped every archive at the corner latitude (~61.7 N) and
    dropped the whole Arctic. Warping here fixes it, because GDAL's suggested output samples the
    edges too. It costs roughly 2.7x the tiling time (Aves 5 km: 68 s from LAEA, 184 s from
    mercator) and buys back the ~40% of the country the truncated envelope was dropping.

    The destination resolution is pinned rather than left to GDAL's suggestion. Mercator units
    stretch with latitude, and the suggested transform splits the difference across a 36-84 N
    span — 10.5 km units, which is 7.6 km on the ground at the southern edge and would alias
    away part of a 5 km grid. Sizing for the southernmost row keeps every cell resolvable, and
    SUPERSAMPLE further divides that pin so the cell's *rotated* boundary survives the warp
    (see SUPERSAMPLE docstring above) instead of just the cell's existence.
    """
    bounds = array_bounds(height, width, src_transform)
    (_, _), (south, _) = warp_transform(src_crs, "EPSG:4326", bounds[::2], bounds[1::2])
    dst_res = abs(src_transform.a) / math.cos(math.radians(south)) / SUPERSAMPLE
    dst_transform, dst_w, dst_h = calculate_default_transform(
        src_crs, MERCATOR, width, height, *bounds, resolution=dst_res)
    with rasterio.open(path, "w", driver="GTiff", width=dst_w, height=dst_h, count=4,
                       dtype="uint8", crs=MERCATOR, transform=dst_transform,
                       compress="deflate", tiled=True, blockxsize=512, blockysize=512) as dst:
        for i in range(4):
            reproject(source=bands[i], destination=rasterio.band(dst, i + 1),
                      src_transform=src_transform, src_crs=src_crs,
                      dst_transform=dst_transform, dst_crs=MERCATOR,
                      resampling=Resampling.nearest)   # keep hard cell edges and a binary alpha
        dst.colorinterp = [ColorInterp.red, ColorInterp.green,
                           ColorInterp.blue, ColorInterp.alpha]


def _tile(job):
    tmp_path, out, zoom = job
    subprocess.run(["rio", "pmtiles", tmp_path, str(out),
                    "--zoom-levels", zoom,
                    "--format", "WEBP",
                    "--resampling", "average"],
                   check=True, capture_output=True)
    Path(tmp_path).unlink()
    return out


def main():
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
                hidden_us = _hidden_mask(height, width, res_m, crs, transform.c, transform.f)

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
                    bands = np.stack([rgb[..., 0], rgb[..., 1], rgb[..., 2], alpha])
                    _write_mercator(tmp_path, bands, crs, transform, width, height)

                    zoom = ZOOM_BY_RES.get(res_label, "0..9")
                    print(f"  staged {tif.name} ({preset['name']}) -> {out.name} (z{zoom})")
                    jobs.append((tmp_path, out, zoom))

    print(f"tiling {len(jobs)} files with 4 workers ...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for out in ex.map(_tile, jobs):
            print(f"    wrote {out.name} ({out.stat().st_size / 1e6:.1f} MB)")

    print("done")


if __name__ == "__main__":
    main()
