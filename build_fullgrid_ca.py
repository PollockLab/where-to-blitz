"""Canada-wide where-to-blitz grid — reproducible rebuild (issue #58).

Rebuilds cluster_results/ca/webapp_data_<GROUP>.json + index.json from first principles,
streaming the per-taxon iNaturalist density directly from the public Biodiversite Quebec
bucket so the whole grid is reproducible from a clean checkout (no lost ad-hoc builder).

Recipe (recovered from commit 15c68b5's own description + _build_walkthrough.py):
  density   = AVERAGE-resample of the 1 km LAEA iNat heatmap onto this 0.25-deg WGS84 grid
  n_train   = round(cell-mean density x cell_km2)            (implied research-grade records)
  discover  = lexicographic rank (n_train descending, climate-surprisal/env ascending) -> 0..1
              i.e. fewer records = higher; zero-record ties broken by climate distinctiveness.
              (Replaces the old saturating norm(1/density); see #58 / commit 106445d.)
  env       = climate-surprisal percentile rank over the grid (CHELSA, density-weighted KDE)
  urgency   = norm(Hansen forest-loss fraction)
  travel    = mean Weiss 2018 travel-time over the cell's land sub-points
  conservation = COSEWIC/SARA at-risk richness   (joined from ca_atrisk_richness.csv)
  staleness    = iNat recent-vs-all-time density  (joined from ca_staleness.csv; else inverse-density proxy)

MASK FIX (the #58 bug): keep every cell inside the iNat density COG footprint (Canada extent),
NOT "Natural-Earth-land AND footprint". The old intersection dropped data-rich water-edge urban
cells whose centroid lands on water (e.g. Laval / Lac-Saint-Louis, 78k-153k obs). US-side cells
fall outside the Canada heatmap footprint and so are naturally excluded (matches the Canada-only
default). Provenance/vintage note: density reflects the CURRENT public heatmap, so per-cell scores
refresh vs the prior (lost-vintage) grid.
"""
import csv
import json
import os

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

RES = 0.25
BBOX = (-141.0, 41.0, -52.0, 84.0)          # minlon, minlat, maxlon, maxlat
H = 1.0                                       # climate KDE bandwidth (standardized units)
KM_PER_DEG = 111.32
EPS = 1e-3                                    # density floor for the staleness inverse-density proxy

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

# ----------------------------------------------------------------- destination 0.25-deg grid
NCOL = round((BBOX[2] - BBOX[0]) / RES)
NROW = round((BBOX[3] - BBOX[1]) / RES)
DST_T = from_origin(BBOX[0], BBOX[3], RES, RES)


def avg_density(cog):
    """Average-resample a 1 km LAEA iNat heatmap onto the 0.25-deg WGS84 grid.
    Returns (NROW, NCOL) cell-mean density; NaN outside the COG footprint."""
    url = f"{BUCKET}/{cog}_density_inat_1km.tif"
    dst = np.full((NROW, NCOL), np.nan, np.float32)
    with rasterio.open(url) as src:
        reproject(rasterio.band(src, 1), dst,
                  src_transform=src.transform, src_crs=src.crs,
                  dst_transform=DST_T, dst_crs="EPSG:4326",
                  src_nodata=src.nodata, dst_nodata=np.nan, resampling=Resampling.average)
    return dst


print("streaming All-taxa density for the master footprint ...")
allgrid = avg_density("All")                          # master grid = All COG footprint
rows, cols = np.where(np.isfinite(allgrid))           # MASK: keep every in-footprint cell (the #58 fix)
clat = BBOX[3] - (rows + 0.5) * RES
clon = BBOX[0] + (cols + 0.5) * RES
gi = np.round(clat / RES - 0.5).astype(int)
gj = np.round(clon / RES - 0.5).astype(int)
N = len(clat)
print(f"footprint cells (master grid): {N}")
lonlat = list(zip(clon, clat))

# ----------------------------------------------------------------- travel (Weiss, multi-point land mean)
SUB = list(np.linspace(-0.11, 0.11, 7))
n = len(SUB)
subpts = [(lo + dx, la + dy) for la, lo in zip(clat, clon) for dx in SUB for dy in SUB]
with rasterio.open(TRAVEL) as ds:
    vv = np.array([v[0] for v in ds.sample(subpts)], float).reshape(N, n * n)
    tnd = ds.nodata
valid = (vv != tnd) & (vv >= 0) & np.isfinite(vv)
travel_min = np.where(valid.any(1), np.where(valid, vv, 0).sum(1) / np.maximum(valid.sum(1), 1), np.nan)

# MASK: keep Canadian land (any Weiss land sub-point) OR any data-bearing cell. The data-bearing
# clause recovers water-edge urban cells whose centroid lands on water (the #58 bug, e.g. Laval);
# the Weiss clause drops the in-footprint ocean/empty-extent cells the bare COG footprint kept.
land_pts = valid.sum(1)
cand_dens = allgrid[rows, cols]
keep = (land_pts >= 1) | (np.isfinite(cand_dens) & (cand_dens > 0))
rows, cols = rows[keep], cols[keep]
clat, clon, gi, gj, travel_min = clat[keep], clon[keep], gi[keep], gj[keep], travel_min[keep]
N = len(clat)
lonlat = list(zip(clon, clat))
print(f"after land/data mask: {N} cells")

# ----------------------------------------------------------------- climate (CHELSA) -> standardized Z
with rasterio.open(CLIM) as ds:
    clim = np.array([next(iter(ds.sample([(lo, la)]))) for lo, la in lonlat], float)
clim[clim < -1e30] = np.nan
Z = (clim - np.nanmean(clim, 0)) / (np.nanstd(clim, 0) + 1e-9)

# ----------------------------------------------------------------- urgency (Hansen forest loss)
URGENCY_REAL = os.path.exists(LOSS)
if URGENCY_REAL:
    with rasterio.open(LOSS) as ds:
        lo = np.array([v[0] for v in ds.sample(lonlat)], float)
        lnd = ds.nodata
    lo = np.where((lo == lnd) | ~np.isfinite(lo) | (lo < 0), 0.0, lo)
    r = lo.max() - lo.min()
    o_urgency = (lo - lo.min()) / r if r else lo * 0.0
else:
    o_urgency = np.zeros(N)

cell_km2 = (RES * KM_PER_DEG) * (RES * KM_PER_DEG * np.cos(np.radians(clat)))


def env_coverage(weights):
    """Climate surprisal -log(density-weighted KDE in climate space), percentile-ranked to 0..1.
    Cells whose climate is rare among recorded cells score high."""
    R = weights > 0
    if R.sum() == 0:
        return np.zeros(N)
    ZR, wR = Z[R], weights[R]
    ok = ~np.any(np.isnan(Z), axis=1)
    s = np.full(N, np.nan)
    idx = np.where(ok)[0]
    ZK = Z[idx]
    inv2h2 = 1.0 / (2 * H * H)
    ZR2 = np.sum(ZR ** 2, axis=1)
    for b in range(0, len(idx), 2048):
        Zq = ZK[b:b + 2048]
        d2 = np.sum(Zq ** 2, axis=1)[:, None] - 2 * Zq @ ZR.T + ZR2[None, :]
        np.maximum(d2, 0, out=d2)
        s[idx[b:b + len(Zq)]] = -np.log(np.exp(-d2 * inv2h2) @ wR + 1e-9)
    out = np.zeros(N)
    v = ok & np.isfinite(s)
    if v.sum() > 1:
        out[np.where(v)[0]] = np.argsort(np.argsort(s[v])) / (v.sum() - 1)
    return out


# ----------------------------------------------------------------- conservation / staleness joins
def load_norm(path, col):
    d = {}
    if os.path.exists(path):
        for r in csv.DictReader(open(path)):
            try:
                d[(int(r["gi"]), int(r["gj"]))] = float(r[col])
            except (KeyError, ValueError):
                pass
    return d


atrisk = load_norm(ATRISK, "conservation_norm")
stale = load_norm(STALE, "staleness_norm")
print(f"joins: atrisk {len(atrisk)} cells, staleness {len(stale)} cells")


def sr(x):
    return round(float(x), 3) if np.isfinite(x) else 0.0


def tr(x):
    return round(float(x), 0) if np.isfinite(x) else -1.0


# ----------------------------------------------------------------- per-group rows
data, sizes = {}, {}
os.makedirs(OUT_DIR, exist_ok=True)
for group in GROUPS:
    cog = GROUP_TO_COG[group]
    grid = allgrid if cog == "All" else avg_density(cog)
    dens = grid[rows, cols]
    dens0 = np.where(np.isfinite(dens) & (dens > 0), dens, 0.0)
    n_train = np.round(dens0 * cell_km2).astype(int)
    o_env = env_coverage(dens0)
    # discover = lexicographic rank (recovered recipe, commit 15c68b5): primary n_train DESCENDING
    # so well-sampled cells rank low and under-sampled cells rank high (fewer records = higher
    # discover); zero-record ties broken by climate-surprisal/env ASCENDING (more distinctive = higher).
    order = np.lexsort((o_env, -n_train))          # last key primary: -n_train asc (= n_train desc); then env asc
    rank = np.empty(N)
    rank[order] = np.arange(N)
    o_discover = rank / (N - 1)
    # inverse-density proxy used only where the real staleness layer has no value for a cell
    inv = 1.0 / (dens0 + EPS)
    proxy = (inv - inv.min()) / (inv.max() - inv.min()) if inv.max() > inv.min() else inv * 0.0
    rows_out = []
    for i in range(N):
        key = (int(gi[i]), int(gj[i]))
        cons = atrisk.get(key, 0.0)
        stl = stale.get(key, proxy[i])
        rows_out.append([round(float(clat[i]), 3), round(float(clon[i]), 3),
                         sr(o_discover[i]), sr(cons), sr(o_env[i]),
                         sr(stl), sr(o_urgency[i]), tr(travel_min[i]), int(n_train[i])])
    data[group] = rows_out
    fn = os.path.join(OUT_DIR, f"webapp_data_{group.replace(' ', '_')}.json")
    json.dump({group: rows_out}, open(fn, "w"), separators=(",", ":"))
    sizes[group] = os.path.getsize(fn)
    rec = int((n_train > 0).sum())
    print(f"  {group:16s} (COG={cog:14s}): {N} cells ({rec} recorded, {N - rec} gaps), {sizes[group]//1024} KB")

# ----------------------------------------------------------------- index.json
index = {
    "groups": GROUPS,
    "files": {g: f"webapp_data_{g.replace(' ', '_')}.json" for g in GROUPS},
    "n_cells": N,
    "bbox": list(BBOX),
    "res": RES,
    "row_format": ["lat", "lon", "discover", "conservation", "env", "staleness", "urgency", "travel_min", "n_train"],
    "land_mask": ("iNaturalist density COG footprint (Biodiversite Quebec, current vintage); keeps every "
                  "in-footprint cell incl. data-bearing water-edge urban cells (#58); US-side cells fall "
                  "outside the Canada footprint."),
    "discover_method": ("under-sampling rank (fewer records = higher); zero-record ties broken by climate "
                        "distinctiveness (env). Lexicographic n_train asc / env desc."),
    "staleness_method": "iNaturalist recent-vs-all-time density (ca_staleness.csv); inverse-density proxy where absent.",
    "axes_status": {
        "discover": "REAL (under-sampling rank from iNaturalist density COG)",
        "conservation": "REAL (COSEWIC/SARA at-risk richness, CAN-SAR x GBIF)",
        "env": "REAL (CHELSA climate surprisal, density-weighted)",
        "staleness": "REAL (iNaturalist recent vs all-time density)",
        "urgency": "REAL (Hansen loss fraction)" if URGENCY_REAL else "DEFERRED (0)",
        "travel_min": "REAL (Weiss 2018 travel-time)",
        "n_train": "REAL-proxy (cell-mean density x cell km2)",
    },
    "group_to_cog": GROUP_TO_COG,
    "density_vintage": "iNaturalist (Biodiversite Quebec) inat_canada_heatmaps, current public vintage, average-resampled 1km->0.25deg",
}
json.dump(index, open(os.path.join(OUT_DIR, "index.json"), "w"), indent=2)
print(f"\nwrote {len(GROUPS)} group files + index.json ({sum(sizes.values())//1024} KB total, {N} cells each)")
