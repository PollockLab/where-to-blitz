"""Multi-objective "where to go" engine for Blitz the Gap — tangible implementation.

Turns the factor taxonomy (2026-06-12-sampling-priority-factors.md) into a working,
backtested engine. A where-to-go map is NOT one number: it is a chosen OBJECTIVE,
ROI-divided by accessibility cost, under guardrails. Each objective is a per-cell
score computed from pre-T (leakage-free) data, and each is BACKTESTED against its
OWN target outcome on post-T data — the discipline is: don't assert, test.

Objectives (value terms), each computed train-only:
  discover       under-sampling (validated: predicts new-species discovery)
  conservation   go where rare/range-restricted species already are
  staleness      time since last visit
  env_coverage   under-represented climate space   (added when climate raster present)
  urgency        recent habitat loss               (added when forest-loss raster present)

Cost: Weiss 2018 travel-time (minutes). ROI(objective) = objective / (1 + travel_h).

The headline experiment: discovery and conservation are DIFFERENT objectives that
serve DIFFERENT goals — discover wins at finding MANY species, a purpose-built
conservation objective wins at finding RARE ones. Proven head-to-head, not asserted.
"""
import glob
import json
import os
import sys

import _rootpath  # noqa: F401
import numpy as np
import pandas as pd
import voi_conservation as vc

import voi_backtest as vb

TT = "cluster_results/bc_travel_time.tif"


def travel_minutes(clat, clon):
    import rasterio
    with rasterio.open(TT) as ds:
        v = np.array([x[0] for x in ds.sample(list(zip(clon, clat)))], float)
    v[v < 0] = np.nan
    return v


def norm(s):
    s = np.asarray(s, float)
    r = np.nanmax(s) - np.nanmin(s)
    return (s - np.nanmin(s)) / r if r else s * 0.0


def build(name, df, env_raster=None, loss_raster=None):
    """Per-cell objective scores + the post-T outcomes to backtest them against."""
    cells = vb.build_cells(df)
    if cells is None:
        return None
    aux = cells.attrs["aux"]
    weights = vc.species_rarity(df)                       # taxon_id -> rarity (mean 1)

    # ---- value objectives, computed from TRAIN only ----
    cells["o_discover"] = cells["scarcity"]               # 1/n_train (validated discovery signal)
    cells["o_staleness"] = cells["staleness"]
    # conservation: mean rarity of the species already recorded in the cell (train).
    # cells that host range-restricted species are where rare species will turn up.
    cons = []
    for _, row in cells.iterrows():
        seen, _ = aux.get((row.gi, row.gj), (set(), []))
        ws = [weights.get(s, 1.0) for s in seen]
        cons.append(float(np.mean(ws)) if ws else np.nan)
    cells["o_conservation"] = norm(pd.Series(cons).fillna(np.nanmin(cons)))

    # ---- external layers (optional) ----
    cells["travel_min"] = travel_minutes(cells.clat.values, cells.clon.values)
    if env_raster and os.path.exists(env_raster):
        cells["o_env_coverage"] = _env_coverage(cells, env_raster)
    if loss_raster and os.path.exists(loss_raster):
        cells["o_urgency"] = _urgency(cells, loss_raster)

    # ---- post-T outcomes (effort-equalized, rarefied to K) ----
    cells["rare_newK"] = vb.rarefy_new_at_k(cells, aux, np.random.default_rng(vb.SEED), K=5)
    cells["w_newK"] = vc.rarefy_weighted(cells, aux, weights, np.random.default_rng(vb.SEED), K=5)
    cells["meanrarity_newK"] = cells.w_newK / cells.rare_newK.where(cells.rare_newK > 0)
    cells.attrs["name"] = name
    return cells


def _env_coverage(cells, env_raster, h=1.0):
    """Environmental under-sampling, in CLIMATE space — distinct from geographic
    under-sampling. A cell scores high if its climate (temp/seasonality/precip) is
    rare among existing RECORDS, regardless of where it sits geographically. So a
    remote cell with a common climate scores LOW (env-redundant), and a record-dense
    cell with an unusual climate scores HIGH — the geographic≠environmental gap.

    record density at a cell's climate = Σ_j n_train_j · exp(-||z_i - z_j||² / 2h²)
    (Gaussian KDE in standardized climate space, weighted by records). priority = 1/density."""
    import rasterio
    with rasterio.open(env_raster) as ds:
        samp = np.array([next(iter(ds.sample([(lon, lat)]))) for lat, lon in zip(cells.clat, cells.clon)], float)
    samp[samp < -1e30] = np.nan
    Z = (samp - np.nanmean(samp, 0)) / (np.nanstd(samp, 0) + 1e-9)      # standardize each climate band
    w = cells.n_train.values.astype(float)                              # record weight per cell
    out = np.full(len(cells), np.nan)
    ok = ~np.any(np.isnan(Z), axis=1)
    Zok, wok = Z[ok], w[ok]
    for i in np.where(ok)[0]:
        d2 = np.sum((Zok - Z[i]) ** 2, axis=1)
        dens = np.sum(wok * np.exp(-d2 / (2 * h * h)))                  # record density at this climate
        out[i] = 1.0 / dens if dens > 0 else np.nan
    return norm(out)                                                     # rare climate among records = high priority


def _urgency(cells, loss_raster):
    """Mean recent-forest-loss FRACTION over each 0.25-deg cell (the BC loss raster
    is already a per-0.05-deg loss fraction, built from Hansen GFC on Fir). Average
    the ~5x5 sub-pixels covering the cell."""
    import rasterio
    out = np.full(len(cells), np.nan)
    rad = round(vb.RES / 0.05 / 2)                # half-cell in 0.05-deg pixels (~2-3)
    with rasterio.open(loss_raster) as ds:
        arr = ds.read(1, masked=True).filled(np.nan)
        for i, (lat, lon) in enumerate(zip(cells.clat, cells.clon)):
            try:
                r, c = ds.index(lon, lat)
                win = arr[max(0, r-rad):r+rad+1, max(0, c-rad):c+rad+1]
                v = np.nanmean(win) if win.size else np.nan
                out[i] = v
            except Exception:
                pass
    return norm(out)


def spearman(x, y):
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 6 or np.std(x[m]) == 0 or np.std(y[m]) == 0:
        return np.nan
    return float(np.corrcoef(pd.Series(x[m]).rank(), pd.Series(y[m]).rank())[0, 1])


def disagreement(cells, objs):
    """How differently do the objectives rank cells? (the 'it's a value choice' proof)"""
    M = pd.DataFrame(index=objs, columns=objs, dtype=float)
    for a in objs:
        for b in objs:
            M.loc[a, b] = round(spearman(cells[a].values, cells[b].values), 2)
    return M


def backtest_objectives(cells, objs):
    """Each objective vs each post-T target outcome (rarefied, effort-equalized).
    Shows which objective serves which goal. perm-tested."""
    rk = cells.dropna(subset=["rare_newK"]).copy()
    targets = {"total_new(@K)": "rare_newK", "rarity_wtd_new(@K)": "w_newK",
               "mean_rarity_of_new": "meanrarity_newK"}
    rows = []
    for o in objs:
        row = {"objective": o}
        for tname, tcol in targets.items():
            sub = rk.dropna(subset=[o, tcol])
            rho, p, _, _ = vb.perm_test(sub[o].values, sub[tcol].values, np.random.default_rng(vb.SEED + hash(o + tcol) % 1000))
            row[tname] = f"{rho:+.2f}" + ("*" if p < 0.05 else "")
        rows.append(row)
    return pd.DataFrame(rows).set_index("objective")


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(glob.glob("cluster_results/inat_*.csv"))
    env = "cluster_results/bc_bioclim.tif" if os.path.exists("cluster_results/bc_bioclim.tif") else None
    loss = "cluster_results/bc_forestloss.tif" if os.path.exists("cluster_results/bc_forestloss.tif") else None
    base_objs = ["o_discover", "o_conservation", "o_staleness"]
    summary = {}
    for f in files:
        name = f.split("inat_")[-1].replace(".csv", "")
        cells = build(name, pd.read_csv(f), env_raster=env, loss_raster=loss)
        if cells is None:
            continue
        objs = [o for o in base_objs + ["o_env_coverage", "o_urgency"] if o in cells]
        cells.to_csv(f"cluster_results/whereto_cells_{name}.csv", index=False)
        print(f"\n===== {name} =====")
        print("Objective DISAGREEMENT (Spearman ρ between cell rankings):")
        print(disagreement(cells, objs).to_string())
        print("\nBACKTEST — each objective vs post-T outcome (rarefied K=5; * = perm p<0.05):")
        print(backtest_objectives(cells, objs).to_string())
        summary[name] = {"n_cells": len(cells), "objectives": objs}
    json.dump(summary, open("cluster_results/whereto_summary.json", "w"), indent=2)
    print("\nwrote whereto_cells_*.csv + whereto_summary.json")
