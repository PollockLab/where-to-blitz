"""Canada-wide where-to-blitz grid — reproducible rebuild (issues #58, #87).

Rebuilds cluster_results/ca/webapp_data_<GROUP>.json + index.json from first principles,
streaming the per-taxon iNaturalist density directly from the public Biodiversite Quebec
bucket so the whole grid is reproducible from a clean checkout (no lost ad-hoc builder).

GEOMETRY (#87): cells are defined on a projected equal-area lattice in the density COGs' own
CRS (WGS84 Lambert Azimuthal Equal Area, lat_0=45 lon_0=-100) at GRID_RES metres -- see
grid_lattice.py. Every cell is square, equal-area and actually GRID_RES across, 1 km -> cell
aggregation is an exact integer block reduce, and the 25 km tier is an exact 5x5 aggregate of
the 5 km tier. The previous 0.25-deg definition made a "25 km" cell 21.0 km wide at Windsor
and 2.9 km wide at 84N.

Every layer is now a cell mean over the equal-area cell (reprojected with an area-weighted
average), replacing the old cell-centre point samples: resolution-independent, and it means a
25 km value is the mean of its 25 constituent 5 km values by construction.

RAMPS (#87): the 0..1 axes are mapped from their raw quantities through the national ramps in
breaks.json, fitted once on the 25 km tier -- see national_breaks.py. They used to be percentile
ranks and min-max normalizations over whichever grid was being built, which made a cell's value
depend on the size and composition of that grid, and so put the same place at two different
colours at two zooms.

Recipe:
  density   = exact 5x5 (or k x k) block mean of the 1 km LAEA iNat heatmap onto the lattice
  n_train   = round(cell-mean density x cell_km2)            (implied research-grade records)
  discover  = under-sampling score from density per km2 (fewer records = higher); zero-record
              cells on top, ordered among themselves by climate distinctiveness (env).
  env       = climate surprisal (CHELSA, density-weighted KDE) on the national ramp
  urgency   = Hansen forest-loss fraction on the national ramp
  travel    = cell-mean Weiss 2018 travel-time over the cell's land pixels
  conservation = COSEWIC/SARA at-risk richness   (joined from ca_atrisk_richness.csv)
  staleness    = iNat recent-vs-all-time density  (joined from ca_staleness.csv; else inverse-density proxy)

The conservation and staleness CSVs are keyed on the old 0.25-deg (gi, gj) lattice, so each
projected cell inherits the value of the 0.25-deg cell its centre falls in. That is a
nearest-neighbour resample of a coarser layer, recorded in index.json; rebuilding those two
layers natively on the lattice is separate work (they are network pulls from CAN-SAR/GBIF).

MASK: keep every cell inside the iNat density COG footprint that has Weiss land coverage OR
non-zero density. The data-bearing clause recovers water-edge urban cells whose centroid lands
on water (the #58 bug, e.g. Laval / Lac-Saint-Louis, 78k-153k obs).

Outputs:
  cluster_results/ca/grid_<RES>m/<GROUP>.tif   7-band float32 stack on the lattice (all RES)
  cluster_results/ca/webapp_data_<GROUP>.json  per-cell rows for the app  (25 km tier only)
  cluster_results/ca/index.json                                          (25 km tier only)
  cluster_results/ca/breaks.json               national 0..1 ramps       (25 km tier only)

Usage:  python build_fullgrid_ca.py            # 25 km tier: fits the ramps, must run first
        GRID_RES=5000 python build_fullgrid_ca.py
"""
import argparse
import csv
import json
import os

import numpy as np
import rasterio

import national_breaks as nb
from climate_kde import surprisal
from grid_lattice import Lattice, block_mean, read_crs, reproject_mean

RES = int(os.environ.get("GRID_RES", "25000"))  # lattice cell size in metres; 25000 or 5000
JSON_RES = 25000                                # the tier the app's vector/popup layer ships from
H = 1.0                                         # climate KDE bandwidth (standardized units)
PARENT_RES_DEG = 0.25                           # lattice of the conservation/staleness CSV joins
EPS = 1e-3                                      # density floor for the staleness inverse-density proxy

TRAVEL = "cluster_results/ca_travel_time.tif"   # Weiss 2018 (minutes), Canada clip
CLIM = "cluster_results/ca_bioclim.tif"         # 3 bands: temp, seasonality, precip (CHELSA)
LOSS = "cluster_results/ca_forestloss.tif"      # Hansen loss fraction (optional)
OUT_DIR = "cluster_results/ca"
ATRISK = "cluster_results/ca/ca_atrisk_richness.csv"
STALE = "cluster_results/ca/ca_staleness.csv"
BUCKET = "/vsicurl/https://object-arbutus.cloud.computecanada.ca/bq-io/io/inat_canada_heatmaps"

# 11 published groups -> their per-taxon density COG name on the bucket ("All" = all-taxa).
GROUP_TO_COG = {
    "All biodiversity": "All", "Plantae": "Plantae", "Insecta": "Insecta", "Aves": "Aves",
    "Fungi": "Fungi", "Mammalia": "Mammalia", "Actinopterygii": "Actinopterygii",
    "Reptilia": "Reptilia", "Amphibia": "Amphibia", "Arachnida": "Arachnida", "Mollusca": "Mollusca",
}
GROUPS = list(GROUP_TO_COG)
# Accept an optional per-group build via --group or the GROUP env var. When provided,
# the script will build only that group's stack (and JSON if GRID_RES==JSON_RES). This
# keeps the default behaviour unchanged when no group is specified.
parser = argparse.ArgumentParser(description="Build per-group or full Canada grid")
parser.add_argument("--group", dest="group", default=os.environ.get("GROUP"),
                    help="Optional group name to build (must match one of GROUPS)")
args = parser.parse_args()
GROUP_ARG = args.group

# If a group was requested, validate it and restrict work to that single group.
if GROUP_ARG:
    if GROUP_ARG not in GROUPS:
        raise SystemExit(f"Unknown group {GROUP_ARG!r}; must be one of: {GROUPS}")
    groups_to_build = [GROUP_ARG]
else:
    groups_to_build = GROUPS

# band order of the per-group raster stack; matches the JSON row format after [lat, lon]
BANDS = ["discover", "conservation", "env", "staleness", "urgency", "travel_min", "n_train"]


def cog_url(cog):
    return f"{BUCKET}/{cog}_density_inat_1km.tif"


# ----------------------------------------------------------------- destination lattice
LAT = Lattice(RES, read_crs(cog_url("All")))
RAST_DIR = os.path.join(OUT_DIR, f"grid_{RES}m")
print(f"lattice: {LAT.ncol} x {LAT.nrow} = {LAT.ncol * LAT.nrow:,} cells of {RES} m "
      f"({LAT.cell_km2:g} km2) in {LAT.crs.to_string()}")

print("streaming All-taxa density for the master footprint ...")
allgrid = block_mean(cog_url("All"), LAT)             # master grid = All COG footprint

# ----------------------------------------------------------------- travel (Weiss cell mean over land)
travel_grid, land_frac = reproject_mean(TRAVEL, LAT, nonneg=True)

# MASK: inside the COG footprint, keep Canadian land OR any data-bearing cell. The data-bearing
# clause recovers water-edge urban cells whose centroid lands on water (the #58 bug, e.g. Laval).
mask = np.isfinite(allgrid) & ((land_frac > 0) | (allgrid > 0))
rows, cols = np.where(mask)
N = len(rows)
clon, clat = LAT.centres_lonlat(rows, cols)
travel_min = travel_grid[rows, cols]
gi = np.round(clat / PARENT_RES_DEG - 0.5).astype(int)
gj = np.round(clon / PARENT_RES_DEG - 0.5).astype(int)
print(f"after footprint/land/data mask: {N:,} of {LAT.ncol * LAT.nrow:,} lattice cells")

# ----------------------------------------------------------------- climate (CHELSA) -> standardized Z
with rasterio.open(CLIM) as ds:
    nbands = ds.count
clim = np.column_stack([reproject_mean(CLIM, LAT, band=b)[0][rows, cols]
                        for b in range(1, nbands + 1)]).astype(float)
clim[clim < -1e30] = np.nan
Z = (clim - np.nanmean(clim, 0)) / (np.nanstd(clim, 0) + 1e-9)
os.makedirs(RAST_DIR, exist_ok=True)
np.save(os.path.join(RAST_DIR, "climate_z.npy"), Z.astype(np.float32))   # real field for test_climate_kde.py

# ----------------------------------------------------------------- national ramps (#87)
# The reference tier fits every 0..1 ramp and writes breaks.json; finer tiers reuse it, so a
# cell's colour depends on its own raw quantity rather than on the grid it was built with.
FIT = RES == nb.REF_RES
BREAKS = nb.new(RES, N) if FIT else nb.load(RES, groups_to_build)
print(f"national ramps: {'fitting from this tier' if FIT else f'reusing {nb.PATH}'}")

# ----------------------------------------------------------------- urgency (Hansen forest loss)
URGENCY_REAL = os.path.exists(LOSS)
if URGENCY_REAL:
    loss = reproject_mean(LOSS, LAT, nonneg=True)[0][rows, cols].astype(float)
    loss = np.where(np.isfinite(loss), loss, 0.0)
    if FIT:
        BREAKS["shared"]["urgency"] = nb.fit_minmax(loss)
    o_urgency = nb.to_unit_minmax(loss, BREAKS["shared"]["urgency"])
    sat = float((o_urgency >= 1.0).mean())
    print(f"urgency: ramp {BREAKS['shared']['urgency']}, {sat:.3%} of cells at the top")
else:
    if FIT:
        BREAKS["shared"]["urgency"] = {"lo": 0.0, "hi": 0.0}
    o_urgency = np.zeros(N)

cell_km2 = LAT.cell_km2      # constant: the whole point of an equal-area lattice


# ----------------------------------------------------------------- conservation / staleness joins
def load_norm(path, col):
    d = {}
    if os.path.exists(path):
        with open(path) as _fh:
            for r in csv.DictReader(_fh):
                try:
                    d[(int(r["gi"]), int(r["gj"]))] = float(r[col])
                except (KeyError, ValueError):
                    pass
    return d


atrisk = load_norm(ATRISK, "conservation_norm")
stale = load_norm(STALE, "staleness_norm")
print(f"joins: atrisk {len(atrisk)} cells, staleness {len(stale)} cells")
keys = list(zip(gi.tolist(), gj.tolist()))
o_cons = np.array([atrisk.get(k, 0.0) for k in keys])
stale_hit = np.array([k in stale for k in keys])
stale_val = np.array([stale.get(k, 0.0) for k in keys])


def sr(x):
    return round(float(x), 3) if np.isfinite(x) else 0.0


def tr(x):
    return round(float(x), 0) if np.isfinite(x) else -1.0


def write_stack(group, bands):
    """7-band float32 lattice stack; NaN off-mask. Input for build_grid_pmtiles.py (#87 P2)."""
    os.makedirs(RAST_DIR, exist_ok=True)
    path = os.path.join(RAST_DIR, f"{group.replace(' ', '_')}.tif")
    with rasterio.open(path, "w", driver="GTiff", width=LAT.ncol, height=LAT.nrow,
                       count=len(BANDS), dtype="float32", crs=LAT.crs, transform=LAT.transform,
                       nodata=np.nan, compress="deflate", predictor=3, tiled=True,
                       blockxsize=256, blockysize=256) as dst:
        for b, name in enumerate(BANDS, start=1):
            plane = np.full(LAT.shape, np.nan, np.float32)
            plane[rows, cols] = bands[name]
            dst.write(plane, b)
            dst.set_band_description(b, name)
    return path


# ----------------------------------------------------------------- per-group rows
data, sizes = {}, {}
os.makedirs(OUT_DIR, exist_ok=True)
for group in groups_to_build:
    cog = GROUP_TO_COG[group]
    # Both tiers block-mean the 1 km COG directly. The lattice is aligned, so a 25 km cell is
    # the mean of its 25 5 km children up to float associativity - and with the 25 km tier
    # fitting the national ramps FIRST and the 5 km tier reusing them (national_breaks.REF_RES),
    # the ramped bands nest the same way. The validator proves it per band.
    grid = allgrid if cog == "All" else block_mean(cog_url(cog), LAT)
    dens = grid[rows, cols]
    dens0 = np.where(np.isfinite(dens) & (dens > 0), dens, 0.0)
    n_train = np.round(dens0 * cell_km2).astype(int)
    s_env = surprisal(Z, dens0, h=H)               # raw, in nats; NaN where the cell has no climate
    inv = 1.0 / (dens0 + EPS)                      # staleness proxy, used only where the CSV has no value
    if FIT:
        BREAKS["taxa"][group] = {
            "p_zero": float((dens0 == 0).mean()),
            "density_knots": nb.fit_knots(dens0[dens0 > 0]),
            "env_knots": nb.fit_knots(s_env),
            "staleness_proxy": nb.fit_minmax(inv),
        }
    ramp = BREAKS["taxa"][group]
    o_env = np.where(np.isfinite(s_env), nb.to_unit(s_env, ramp["env_knots"]), 0.0)
    o_discover = nb.discover(dens0, o_env, ramp)
    o_stale = np.where(stale_hit, stale_val, nb.to_unit_minmax(inv, ramp["staleness_proxy"]))
    bands = {"discover": o_discover, "conservation": o_cons, "env": o_env, "staleness": o_stale,
             "urgency": o_urgency, "travel_min": travel_min, "n_train": n_train}
    write_stack(group, bands)

    rec = int((n_train > 0).sum())
    if RES == JSON_RES:
        rows_out = [[round(float(clat[i]), 3), round(float(clon[i]), 3),
                     sr(o_discover[i]), sr(o_cons[i]), sr(o_env[i]),
                     sr(o_stale[i]), sr(o_urgency[i]), tr(travel_min[i]), int(n_train[i])]
                    for i in range(N)]
        data[group] = rows_out
        fn = os.path.join(OUT_DIR, f"webapp_data_{group.replace(' ', '_')}.json")
        with open(fn, "w") as _fh:
            json.dump({group: rows_out}, _fh, separators=(",", ":"))
        sizes[group] = os.path.getsize(fn)
        print(f"  {group:16s} (COG={cog:14s}): {N} cells ({rec} recorded, {N - rec} gaps), {sizes[group]//1024} KB")
    else:
        print(f"  {group:16s} (COG={cog:14s}): {N} cells ({rec} recorded, {N - rec} gaps) -> stack only")

# ----------------------------------------------------------------- index.json
index = {
    "groups": GROUPS,
    "files": {g: f"webapp_data_{g.replace(' ', '_')}.json" for g in GROUPS},
    "n_cells": N,
    "res_m": RES,
    "crs": LAT.crs.to_string(),
    "lattice": {"x0": LAT.x0, "y1": LAT.y1, "ncol": LAT.ncol, "nrow": LAT.nrow,
                "cell_km2": LAT.cell_km2},
    "row_format": ["lat", "lon", "discover", "conservation", "env", "staleness", "urgency", "travel_min", "n_train"],
    "geometry": ("equal-area cells on the density COGs' own WGS84 Lambert Azimuthal Equal Area "
                 f"lattice at {RES} m (#87). Cells are square and {RES//1000} km on both axes "
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
        "urgency": "REAL (Hansen loss fraction)" if URGENCY_REAL else "DEFERRED (0)",
        "travel_min": "REAL (Weiss 2018 travel-time)",
        "n_train": "REAL-proxy (cell-mean density x cell km2)",
    },
    "group_to_cog": GROUP_TO_COG,
    "density_vintage": (f"iNaturalist (Biodiversite Quebec) inat_canada_heatmaps, current public vintage, "
                        f"exact {RES//1000}x{RES//1000} block mean of the 1 km LAEA COG"),
}
if FIT:
    print(f"wrote national ramps for {len(BREAKS['taxa'])} taxa -> {nb.save(BREAKS)}")
# Only write the global OUT_DIR/index.json when building the full set (no --group) at the
# JSON_RES tier (25 km). Per-group 5 km builds should not produce the global index.
if RES == JSON_RES and GROUP_ARG is None:
    with open(os.path.join(OUT_DIR, "index.json"), "w") as _fh:
        json.dump(index, _fh, indent=2)
    print(f"\nwrote {len(GROUPS)} group files + index.json ({sum(sizes.values())//1024} KB total, {N} cells each)")
# Write an index into the raster directory only when building the full set; avoid overwriting
# a partial index during per-group runs.
if GROUP_ARG is None:
    with open(os.path.join(RAST_DIR, "index.json"), "w") as _fh:
        json.dump(index, _fh, indent=2)
    print(f"wrote {len(GROUPS)} raster stacks to {RAST_DIR}/")
else:
    print(f"wrote {len(groups_to_build)} raster stacks to {RAST_DIR}/ (group build)")
