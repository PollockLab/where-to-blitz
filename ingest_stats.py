"""Stats-at-ingest for openbiodiversity.ca — turn a COG into STAC render metadata.

Design-01 MVP, made runnable: validate a COG, compute its display ceiling once,
and emit a STAC Item carrying the render extension so the frontend reads two
numbers instead of hardcoding a per-layer scale. Same logic the demo notebook
proves; this is the production-shaped tool.

Usage:
    python ingest_stats.py URL [URL ...] [--out DIR] [--percentile 98] [--license CC-BY-4.0]
    python ingest_stats.py URL --stats-only        # just print stats, no Item

All inputs are read over HTTP range reads (cloud-optimized); nothing is downloaded
whole. No auth, no GDAL config needed for public Arbutus COGs.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

RENDER_EXT = "https://stac-extensions.github.io/render/v2.0.0/schema.json"
RASTER_EXT = "https://stac-extensions.github.io/raster/v1.1.0/schema.json"


def compute_render_stats(cog_url: str, percentile: float = 98.0) -> dict:
    """One ingest pass: read masked, pick a display ceiling, branch by sparsity.

    Dense layers (richness, the 10 useful layers) -> percentile of valid pixels.
    Sparse SDM probability rasters (the "95% zeros" case) -> percentile of the
    *occupied* pixels, so a mostly-empty layer doesn't render invisible.
    Boundary cases (all-zero, empty) are handled explicitly.
    """
    with rasterio.open(cog_url) as src:
        band = src.read(1, masked=True)
        nodata = src.nodata
        crs = src.crs.to_string() if src.crs else None
        width, height = src.width, src.height
        # STAC requires the Item bbox in WGS84 lon/lat; these COGs are in a custom
        # Lambert Azimuthal projection, so reproject the corners.
        if src.crs and src.crs.to_epsg() != 4326:
            bounds = list(transform_bounds(src.crs, "EPSG:4326", *src.bounds))
        else:
            bounds = list(src.bounds)
    valid = band.compressed().astype("float64")
    n_total, n_valid = int(band.size), int(valid.size)
    if n_valid == 0:                                   # fully-masked raster
        return _empty_stats(crs, bounds, width, height, reason="no valid pixels")
    nonzero = valid[valid > 0]
    zero_frac = 1.0 - nonzero.size / n_valid
    sparse = zero_frac > 0.5 and nonzero.size > 0
    pool = nonzero if sparse else valid
    vmax = float(np.percentile(pool, percentile)) if pool.size else float(valid.max())
    if vmax <= 0:                                      # all-zero / degenerate
        vmax = max(1.0, float(valid.max()))
    return {
        "minimum": float(valid.min()),
        "maximum": float(valid.max()),
        "p2": float(np.percentile(valid, 2)),
        "p98": float(np.percentile(valid, 98)),
        "valid_percent": round(100.0 * n_valid / n_total, 4),
        "zero_frac": round(float(zero_frac), 4),
        "sparse_branch": bool(sparse),
        "render_vmax": round(vmax, 4),
        "nodata": None if nodata is None else float(nodata),
        "crs": crs, "bbox": bounds, "shape": [width, height],
    }


def _empty_stats(crs, bounds, width, height, reason):
    return {"minimum": None, "maximum": None, "p2": None, "p98": None,
            "valid_percent": 0.0, "zero_frac": 1.0, "sparse_branch": False,
            "render_vmax": 1.0, "nodata": None, "crs": crs, "bbox": bounds,
            "shape": [width, height], "empty": True, "reason": reason}


def stac_item(cog_url: str, stats: dict, *, item_id=None, title=None,
              colormap="viridis", license="CC-BY-4.0", providers=None) -> dict:
    """Build a STAC Item (render v2.0.0 + raster ext) from computed stats."""
    item_id = item_id or Path(urllib.parse.urlparse(cog_url).path).stem.lower()
    vmax = round(stats["render_vmax"], 2)
    band_stats = {k: stats[k] for k in ("minimum", "maximum", "valid_percent") if stats.get(k) is not None}
    if stats.get("p2") is not None:
        band_stats["percentile_2"], band_stats["percentile_98"] = round(stats["p2"], 2), round(stats["p98"], 2)
    return {
        "type": "Feature", "stac_version": "1.0.0", "id": item_id,
        "stac_extensions": [RENDER_EXT, RASTER_EXT],
        "bbox": stats.get("bbox"),
        "properties": {
            "title": title or item_id,
            "providers": providers or [{"name": "openbiodiversity.ca", "roles": ["processor"]}],
            "license": license,
            "proj:epsg": _epsg(stats.get("crs")),
            "raster:bands": [{"statistics": band_stats}],
            "renders": {"default": {"colormap_name": colormap, "rescale": [[0, vmax]],
                                    "title": f"{title or item_id} (0–{vmax:g})"}},
            **({"openbiodiversity:flag": "hide_empty"} if stats.get("empty") else {}),
        },
        "assets": {"cog": {"href": cog_url,
                           "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                           "roles": ["data"], "raster:bands": [{"statistics": band_stats}]}},
    }


def _epsg(crs_str):
    if not crs_str:
        return None
    try:
        return int(crs_str.split(":")[1])
    except (IndexError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("urls", nargs="+", help="COG URL(s)")
    ap.add_argument("--out", help="directory to write <id>.json STAC Items into")
    ap.add_argument("--percentile", type=float, default=98.0)
    ap.add_argument("--colormap", default="viridis")
    ap.add_argument("--license", default="CC-BY-4.0")
    ap.add_argument("--stats-only", action="store_true", help="print stats, skip Item")
    args = ap.parse_args(argv)

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    rc = 0
    for url in args.urls:
        try:
            stats = compute_render_stats(url, args.percentile)
        except Exception as e:                          # fail loud, keep going
            print(f"ERROR {url}: {e}", file=sys.stderr); rc = 1; continue
        if args.stats_only:
            print(json.dumps({"url": url, **stats}, indent=2)); continue
        item = stac_item(url, stats, colormap=args.colormap, license=args.license)
        if out_dir:
            p = out_dir / f"{item['id']}.json"
            p.write_text(json.dumps(item, indent=2))
            print(f"wrote {p}  vmax={stats['render_vmax']:g} valid%={stats['valid_percent']:g}")
        else:
            print(json.dumps(item, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
