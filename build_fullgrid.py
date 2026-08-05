"""Fill the map: build webapp_data.json over EVERY British Columbia land cell, not
just the cells that already have iNaturalist records. A blank cell was never "no
biodiversity" — it was "no records yet". Here every land cell appears:

  - cells WITH records keep their computed scores (from whereto_cells_*.csv);
  - cells with NO records become **maximal discovery gaps** (discover = staleness =
    1; conservation = 0, since no rare species are known there);
  - climate-coverage, urgency, and travel cost are computed for ALL land cells from
    the real rasters (CHELSA, Hansen, Weiss), so those goals have full coverage.

Land vs ocean is decided by the Weiss travel-time raster (ocean = nodata -9999).
"""
import glob
import json

import numpy as np
import pandas as pd
import rasterio

RES = 0.25
BBOX = (-139.0, 48.0, -114.0, 60.0)
TRAVEL = "cluster_results/bc_travel_time.tif"
CLIM = "cluster_results/bc_bioclim.tif"          # 3 bands: temp, seasonality, precip
LOSS = "cluster_results/bc_forestloss.tif"
H = 1.0                                           # climate KDE bandwidth (standardized units)


def sample(path, lonlat, band=1):
    with rasterio.open(path) as ds:
        return np.array([v[band-1] for v in ds.sample(lonlat)], float)


def norm(x):
    x = np.asarray(x, float); r = np.nanmax(x) - np.nanmin(x)
    return (x - np.nanmin(x)) / r if r else x * 0.0


# 1) enumerate every cell centroid over the bbox, keep the land ones (Weiss != nodata)
gis = range(int(np.floor(BBOX[1]/RES)), int(np.floor(BBOX[3]/RES)) + 1)
gjs = range(int(np.floor(BBOX[0]/RES)), int(np.floor(BBOX[2]/RES)) + 1)
cells = [(gi, gj, (gi+0.5)*RES, (gj+0.5)*RES) for gi in gis for gj in gjs]
df = pd.DataFrame(cells, columns=["gi", "gj", "clat", "clon"])
# Robust land test: BC's fjords/islands mean a single 1 km sample at the centroid
# often hits water and wrongly drops a real land cell. Sample a 3x3 subgrid and
# keep the cell if >=2/9 points are land; travel cost = mean of the land points.
# Also always keep any cell that has iNaturalist records (records prove land).
SUB = list(np.linspace(-0.11, 0.11, 7))      # 7x7 subgrid (~3.7 km spacing) catches islands + fjord coast
n = len(SUB)
subpts = [(lo+dx, la+dy) for la, lo in zip(df.clat, df.clon) for dx in SUB for dy in SUB]
with rasterio.open(TRAVEL) as ds:
    vv = np.array([v[0] for v in ds.sample(subpts)], float).reshape(len(df), n*n)
valid = vv >= 0
df["land_pts"] = valid.sum(1)
df["travel_min"] = np.where(valid.any(1), np.where(valid, vv, 0).sum(1) / np.maximum(valid.sum(1), 1), -1.0)
recorded = set()
for f in glob.glob("cluster_results/whereto_cells_*.csv"):
    rc = pd.read_csv(f); recorded |= set(zip(rc.gi, rc.gj))
df["rec"] = [(gi, gj) in recorded for gi, gj in zip(df.gi, df.gj)]
df = df[(df.land_pts >= 1) | df.rec].reset_index(drop=True)   # any land point -> it's a land cell
df["travel_min"] = df["travel_min"].replace(-1.0, np.nan)
lonlat = list(zip(df.clon, df.clat))
print(f"BC land cells (multi-point Weiss mask + records): {len(df)}")

# 2) shared layers for ALL land cells
with rasterio.open(CLIM) as ds:
    clim = np.array([next(iter(ds.sample([(lon, lat)]))) for lat, lon in zip(df.clat, df.clon)], float)
clim[clim < -1e30] = np.nan
Z = (clim - np.nanmean(clim, 0)) / (np.nanstd(clim, 0) + 1e-9)    # standardized climate
df["o_urgency"] = norm(sample(LOSS, lonlat))                      # recent forest loss, full coverage


def env_coverage(weights):
    """How under-recorded is each cell's CLIMATE? Surprisal = -log(record density
    in climate space). Cells whose climate is rare among records score high. We
    percentile-rank it to 0..1 so the heatmap spreads evenly (a raw 1/density is
    heavy-tailed and collapses almost every cell to 0)."""
    R = weights > 0
    if R.sum() == 0:
        return np.zeros(len(df))
    ZR, wR = Z[R], weights[R]
    s = np.full(len(df), np.nan)
    ok = ~np.any(np.isnan(Z), axis=1)
    for i in np.where(ok)[0]:
        d2 = np.sum((ZR - Z[i])**2, axis=1)
        s[i] = -np.log(np.sum(wR * np.exp(-d2/(2*H*H))) + 1e-9)   # surprisal: high = under-recorded climate
    out = np.zeros(len(df))
    v = ok & np.isfinite(s)
    if v.sum() > 1:
        out[np.where(v)[0]] = np.argsort(np.argsort(s[v])) / (v.sum() - 1)   # percentile rank -> even 0..1
    return out


# 3) per-taxon: merge recorded cells with gap cells
COLS = ["discover", "conservation", "env", "staleness", "urgency"]  # output order
data = {}
key = df.set_index(["gi", "gj"]).index
for f in sorted(glob.glob("cluster_results/whereto_cells_*.csv")):
    name = f.split("whereto_cells_")[-1].replace(".csv", "")
    rec = pd.read_csv(f).set_index(["gi", "gj"])
    g = df.copy()
    g["n_train"] = [int(rec.n_train.get((gi, gj), 0)) for gi, gj in zip(g.gi, g.gj)]
    g["o_discover"] = [rec.o_discover.get((gi, gj), 1.0) for gi, gj in zip(g.gi, g.gj)]        # gap = max
    g["o_staleness"] = [rec.o_staleness.get((gi, gj), 1.0) for gi, gj in zip(g.gi, g.gj)]      # gap = max
    g["o_conservation"] = [rec.o_conservation.get((gi, gj), 0.0) for gi, gj in zip(g.gi, g.gj)]  # gap = 0
    g["o_env_coverage"] = env_coverage(g.n_train.values.astype(float))
    sr = lambda x: round(float(x), 3) if pd.notna(x) else 0.0
    tr = lambda x: round(float(x), 0) if pd.notna(x) else -1.0
    rows = [[round(r.clat, 3), round(r.clon, 3),
             sr(r.o_discover), sr(r.o_conservation), sr(r.o_env_coverage),
             sr(r.o_staleness), sr(r.o_urgency), tr(r.travel_min), int(r.n_train)] for r in g.itertuples()]
    data[name] = rows
    print(f"  {name}: {len(rows)} cells ({(g.n_train>0).sum()} recorded, {(g.n_train==0).sum()} gaps)")

# 4) "All biodiversity" = mean of the per-taxon filled scores per cell
taxa = list(data)
arr = np.array([[r[2:7] for r in data[t]] for t in taxa], float)   # (taxa, cells, 5)
allmean = arr.mean(0)
alln = np.max([[r[8] for r in data[t]] for t in taxa], axis=0)
base = data[taxa[0]]
data["All biodiversity"] = [[base[i][0], base[i][1], *[round(float(x), 3) for x in allmean[i]],
                             base[i][7], int(alln[i])] for i in range(len(base))]

json.dump(data, open("cluster_results/webapp_data.json", "w"), separators=(",", ":"))
import os

print(f"\nwrote webapp_data.json — {len(data)} groups, {len(base)} land cells each, {os.path.getsize('cluster_results/webapp_data.json')//1024} KB")
