"""Artifact writes for build_fullgrid_ca (stage 2 of the build).

The 7-band GeoTIFF stacks, the per-group webapp JSON, and the two index.json files.
Extracted from build_fullgrid_ca.py; output paths, file formats, and rounding are
unchanged.
"""
import json
import os

import numpy as np
import rasterio

import national_breaks as nb
from fullgrid_fields import GROUP_TO_COG, GROUPS, OUT_DIR
from grid_schema import BANDS


def _sr(x):
    return round(float(x), 3) if np.isfinite(x) else 0.0


def _tr(x):
    return round(float(x), 0) if np.isfinite(x) else -1.0


def write_stack(f, group, bands):
    """7-band float32 lattice stack; NaN off-mask. Input for build_grid_values.py (#87 P2)."""
    os.makedirs(f.rast_dir, exist_ok=True)
    path = os.path.join(f.rast_dir, f"{group.replace(' ', '_')}.tif")
    with rasterio.open(path, "w", driver="GTiff", width=f.lat.ncol, height=f.lat.nrow,
                       count=len(BANDS), dtype="float32", crs=f.lat.crs, transform=f.lat.transform,
                       nodata=np.nan, compress="deflate", predictor=3, tiled=True,
                       blockxsize=256, blockysize=256) as dst:
        for b, name in enumerate(BANDS, start=1):
            plane = np.full(f.lat.shape, np.nan, np.float32)
            plane[f.rows, f.cols] = bands[name]
            dst.write(plane, b)
            dst.set_band_description(b, name)
    return path


def write_webapp_json(f, group, bands):
    """Per-cell rows for the app's vector/popup layer; returns the file size in bytes."""
    os.makedirs(OUT_DIR, exist_ok=True)
    rows_out = [[round(float(f.clat[i]), 3), round(float(f.clon[i]), 3),
                 _sr(bands["discover"][i]), _sr(bands["conservation"][i]), _sr(bands["env"][i]),
                 _sr(bands["staleness"][i]), _sr(bands["urgency"][i]), _tr(f.travel_min[i]),
                 int(bands["n_train"][i])]
                for i in range(f.n)]
    fn = os.path.join(OUT_DIR, f"webapp_data_{group.replace(' ', '_')}.json")
    with open(fn, "w") as _fh:
        json.dump({group: rows_out}, _fh, separators=(",", ":"))
    return os.path.getsize(fn)


def _index(f):
    return {
        "groups": GROUPS,
        "files": {g: f"webapp_data_{g.replace(' ', '_')}.json" for g in GROUPS},
        "n_cells": f.n,
        "res_m": f.res,
        "crs": f.lat.crs.to_string(),
        "lattice": {"x0": f.lat.x0, "y1": f.lat.y1, "ncol": f.lat.ncol, "nrow": f.lat.nrow,
                    "cell_km2": f.lat.cell_km2},
        "row_format": ["lat", "lon"] + BANDS,
        "geometry": ("equal-area cells on the density COGs' own WGS84 Lambert Azimuthal Equal Area "
                     f"lattice at {f.res} m (#87). Cells are square and {f.res//1000} km on both axes "
                     "everywhere; the 25 km tier is an exact 5x5 aggregate of the 5 km tier. lat/lon "
                     "are the WGS84 coordinates of the projected cell centre."),
        "land_mask": ("iNaturalist density COG footprint (Biodiversite Quebec, current vintage); keeps every "
                      "in-footprint cell with Weiss land coverage or non-zero density, incl. data-bearing "
                      "water-edge urban cells (#58)."),
        "discover_method": ("under-sampling score (fewer records per km2 = higher); zero-record cells on top, "
                            "ordered among themselves by climate distinctiveness (env)."),
        "staleness_method": "iNaturalist recent-vs-all-time density (ca_staleness.csv); inverse-density proxy where absent.",
        "ramps": (f"discover, env, urgency and the staleness proxy are mapped to 0..1 through the national "
                  f"ramps in breaks.json, fitted once on the {nb.REF_RES // 1000} km tier (see national_breaks.py). "
                  f"A cell's value is a function of its own raw quantity, so the two zoom tiers agree."),
        "resample_note": ("every raster layer is an area-weighted cell mean over the equal-area cell. "
                          "conservation and staleness are joined from CSVs keyed on the legacy 0.25-deg "
                          "lattice, so each cell inherits its 0.25-deg parent's value (nearest-neighbour "
                          "resample of a coarser layer)."),
        "axes_status": {
            "discover": "REAL (under-sampling rank from iNaturalist density COG)",
            "conservation": "REAL (COSEWIC/SARA at-risk richness, CAN-SAR x GBIF; 0.25-deg source)",
            "env": "REAL (CHELSA climate surprisal, density-weighted)",
            "staleness": "REAL (iNaturalist recent vs all-time density; 0.25-deg source)",
            "urgency": "REAL (Hansen loss fraction)" if f.urgency_real else "DEFERRED (0)",
            "travel_min": "REAL (Weiss 2018 travel-time)",
            "n_train": "REAL-proxy (cell-mean density x cell km2)",
        },
        "group_to_cog": GROUP_TO_COG,
        "density_vintage": (f"iNaturalist (Biodiversite Quebec) inat_canada_heatmaps, current public vintage, "
                            f"exact {f.res//1000}x{f.res//1000} block mean of the 1 km LAEA COG"),
    }


def write_indexes(f, group_arg, n_groups, sizes, write_global):
    """breaks.json (reference tier), the global index.json, and the raster-dir index."""
    if f.fit:
        print(f"wrote national ramps for {len(f.breaks['taxa'])} taxa -> {nb.save(f.breaks)}")
    index = _index(f)
    # Only write the global OUT_DIR/index.json when building the full set (no --group) at the
    # JSON_RES tier (25 km). Per-group 5 km builds should not produce the global index.
    if write_global:
        with open(os.path.join(OUT_DIR, "index.json"), "w") as _fh:
            json.dump(index, _fh, indent=2)
        print(f"\nwrote {len(GROUPS)} group files + index.json ({sum(sizes.values())//1024} KB total, {f.n} cells each)")
    # Write an index into the raster directory only when building the full set; avoid
    # overwriting a partial index during per-group runs.
    if group_arg is None:
        with open(os.path.join(f.rast_dir, "index.json"), "w") as _fh:
            json.dump(index, _fh, indent=2)
        print(f"wrote {len(GROUPS)} raster stacks to {f.rast_dir}/")
    else:
        print(f"wrote {n_groups} raster stacks to {f.rast_dir}/ (group build)")
