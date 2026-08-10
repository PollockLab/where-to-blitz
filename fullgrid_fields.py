"""Shared per-cell fields for build_fullgrid_ca (stage 1 of the build).

Everything that does not depend on the taxon group: the destination lattice, the
master footprint/land mask, and the group-independent band inputs (Weiss travel,
CHELSA climate Z, Hansen urgency, the conservation/staleness CSV joins), plus the
per-group band computation itself. Extracted from build_fullgrid_ca.py so the
builder reads as stage composition; no numerical behaviour, paths, or formats changed.
"""
import csv
import os
from types import SimpleNamespace

import numpy as np
import rasterio

import national_breaks as nb
from climate_kde import surprisal
from grid_lattice import Lattice, block_mean, read_crs, reproject_mean

H = 1.0                # climate KDE bandwidth (standardized units)
PARENT_RES_DEG = 0.25  # lattice of the conservation/staleness CSV joins
EPS = 1e-3             # density floor for the staleness inverse-density proxy

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


def cog_url(cog):
    return f"{BUCKET}/{cog}_density_inat_1km.tif"


def _load_norm(path, col):
    d = {}
    if os.path.exists(path):
        with open(path) as _fh:
            for r in csv.DictReader(_fh):
                try:
                    d[(int(r["gi"]), int(r["gj"]))] = float(r[col])
                except (KeyError, ValueError):
                    pass
    return d


def shared_fields(res, groups_to_build):
    """Lattice, mask, group-independent inputs, and the national ramps (fit or load)."""
    lat = Lattice(res, read_crs(cog_url("All")))
    rast_dir = os.path.join(OUT_DIR, f"grid_{res}m")
    print(f"lattice: {lat.ncol} x {lat.nrow} = {lat.ncol * lat.nrow:,} cells of {res} m "
          f"({lat.cell_km2:g} km2) in {lat.crs.to_string()}")

    print("streaming All-taxa density for the master footprint ...")
    allgrid = block_mean(cog_url("All"), lat)       # master grid = All COG footprint

    # ------------------------------------------------- travel (Weiss cell mean over land)
    travel_grid, land_frac = reproject_mean(TRAVEL, lat, nonneg=True)

    # MASK: inside the COG footprint, keep Canadian land OR any data-bearing cell. The
    # data-bearing clause recovers water-edge urban cells whose centroid lands on water
    # (the #58 bug, e.g. Laval).
    mask = np.isfinite(allgrid) & ((land_frac > 0) | (allgrid > 0))
    rows, cols = np.where(mask)
    n = len(rows)
    clon, clat = lat.centres_lonlat(rows, cols)
    travel_min = travel_grid[rows, cols]
    gi = np.round(clat / PARENT_RES_DEG - 0.5).astype(int)
    gj = np.round(clon / PARENT_RES_DEG - 0.5).astype(int)
    print(f"after footprint/land/data mask: {n:,} of {lat.ncol * lat.nrow:,} lattice cells")

    # ------------------------------------------------- climate (CHELSA) -> standardized Z
    with rasterio.open(CLIM) as ds:
        nbands = ds.count
    clim = np.column_stack([reproject_mean(CLIM, lat, band=b)[0][rows, cols]
                            for b in range(1, nbands + 1)]).astype(float)
    clim[clim < -1e30] = np.nan
    z = (clim - np.nanmean(clim, 0)) / (np.nanstd(clim, 0) + 1e-9)
    os.makedirs(rast_dir, exist_ok=True)
    np.save(os.path.join(rast_dir, "climate_z.npy"), z.astype(np.float32))   # real field for test_climate_kde.py

    # ------------------------------------------------- national ramps (#87)
    # The reference tier fits every 0..1 ramp and writes breaks.json; finer tiers reuse it,
    # so a cell's colour depends on its own raw quantity, not the grid it was built with.
    fit = res == nb.REF_RES
    breaks = nb.new(res, n) if fit else nb.load(res, groups_to_build)
    print(f"national ramps: {'fitting from this tier' if fit else f'reusing {nb.PATH}'}")

    # ------------------------------------------------- urgency (Hansen forest loss)
    urgency_real = os.path.exists(LOSS)
    if urgency_real:
        loss = reproject_mean(LOSS, lat, nonneg=True)[0][rows, cols].astype(float)
        loss = np.where(np.isfinite(loss), loss, 0.0)
        if fit:
            breaks["shared"]["urgency"] = nb.fit_minmax(loss)
        o_urgency = nb.to_unit_minmax(loss, breaks["shared"]["urgency"])
        sat = float((o_urgency >= 1.0).mean())
        print(f"urgency: ramp {breaks['shared']['urgency']}, {sat:.3%} of cells at the top")
    else:
        if fit:
            breaks["shared"]["urgency"] = {"lo": 0.0, "hi": 0.0}
        o_urgency = np.zeros(n)

    # ------------------------------------------------- conservation / staleness joins
    atrisk = _load_norm(ATRISK, "conservation_norm")
    stale = _load_norm(STALE, "staleness_norm")
    print(f"joins: atrisk {len(atrisk)} cells, staleness {len(stale)} cells")
    keys = list(zip(gi.tolist(), gj.tolist()))
    o_cons = np.array([atrisk.get(k, 0.0) for k in keys])
    stale_hit = np.array([k in stale for k in keys])
    stale_val = np.array([stale.get(k, 0.0) for k in keys])

    return SimpleNamespace(
        res=res, lat=lat, rast_dir=rast_dir, allgrid=allgrid,
        rows=rows, cols=cols, n=n, clon=clon, clat=clat, travel_min=travel_min, z=z,
        fit=fit, breaks=breaks, urgency_real=urgency_real,
        o_urgency=o_urgency, o_cons=o_cons, stale_hit=stale_hit, stale_val=stale_val,
    )


def group_bands(f, group):
    """The 7-band stack for one taxon group; fits the group's national ramps when f.fit."""
    cog = GROUP_TO_COG[group]
    # Both tiers block-mean the 1 km COG directly. The lattice is aligned, so a 25 km cell is
    # the mean of its 25 5 km children up to float associativity - and with the 25 km tier
    # fitting the national ramps FIRST and the 5 km tier reusing them (national_breaks.REF_RES),
    # the ramped bands nest the same way. The validator proves it per band.
    grid = f.allgrid if cog == "All" else block_mean(cog_url(cog), f.lat)
    dens = grid[f.rows, f.cols]
    dens0 = np.where(np.isfinite(dens) & (dens > 0), dens, 0.0)
    n_train = np.round(dens0 * f.lat.cell_km2).astype(int)  # constant cell_km2: an equal-area lattice
    s_env = surprisal(f.z, dens0, h=H)               # raw, in nats; NaN where the cell has no climate
    inv = 1.0 / (dens0 + EPS)                        # staleness proxy, used only where the CSV has no value
    if f.fit:
        f.breaks["taxa"][group] = {
            "p_zero": float((dens0 == 0).mean()),
            "density_knots": nb.fit_knots(dens0[dens0 > 0]),
            "env_knots": nb.fit_knots(s_env),
            "staleness_proxy": nb.fit_minmax(inv),
        }
    ramp = f.breaks["taxa"][group]
    o_env = np.where(np.isfinite(s_env), nb.to_unit(s_env, ramp["env_knots"]), 0.0)
    o_discover = nb.discover(dens0, o_env, ramp)
    o_stale = np.where(f.stale_hit, f.stale_val, nb.to_unit_minmax(inv, ramp["staleness_proxy"]))
    return {"discover": o_discover, "conservation": f.o_cons, "env": o_env, "staleness": o_stale,
            "urgency": f.o_urgency, "travel_min": f.travel_min, "n_train": n_train}
