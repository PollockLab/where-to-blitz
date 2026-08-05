"""Accessibility as a COST, not a value — quantified on real data.

The accessibility literature (Weiss et al. 2018; Geldmann 2016; Geurts 2023;
Murdoch 2007 ROI) says: accessible cells are exactly the OVER-sampled ones, so
accessibility must enter a where-to-sample score as a cost denominator / feasibility
filter, NEVER as a priority booster (that just re-creates roadside bias). This
script tests that claim on the BC backtest cells using the real Weiss 2018
travel-time-to-cities surface (minutes), and quantifies the reachable-frontier
trade-off a practical tool faces.
"""
import glob
import json
import sys

import numpy as np
import pandas as pd
import rasterio

TT = "cluster_results/bc_travel_time.tif"     # Weiss 2018, cached (minutes to nearest city)


def spearman(x, y):
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 5 or np.std(x) == 0 or np.std(y) == 0:
        return np.nan, 0
    rx = pd.Series(x).rank().values; ry = pd.Series(y).rank().values
    return float(np.corrcoef(rx, ry)[0, 1]), len(x)


def sample_travel_time(cells):
    with rasterio.open(TT) as ds:
        vals = [v[0] for v in ds.sample(list(zip(cells.clon, cells.clat)))]
        nodata = ds.nodata
    tt = np.array(vals, float)
    if nodata is not None:
        tt[tt == nodata] = np.nan
    tt[tt < 0] = np.nan
    return tt


def analyse(name):
    cells = pd.read_csv(f"cluster_results/backtest_cells_{name}.csv")
    cells["travel_min"] = sample_travel_time(cells)
    rev = cells[(cells.n_test > 0) & np.isfinite(cells.travel_min)].copy()
    rk = cells.dropna(subset=["rare_newK"]).copy()
    rk = rk[np.isfinite(rk.travel_min)]
    out = {"taxon": name, "n_cells": int(np.isfinite(cells.travel_min).sum())}

    # 1. accessibility drives sampling: travel-time vs existing effort (n_train).
    #    expect NEGATIVE (reachable cells = low time = more obs already).
    r, _ = spearman(cells.travel_min.values, cells.n_train.values)
    out["traveltime_vs_effort"] = r
    # 2. the tension: travel-time vs PRIORITY. priority rewards under-sampling, so
    #    expect POSITIVE (priority sends you to less-reachable cells).
    r, _ = spearman(cells.travel_min.values, cells.priority.values)
    out["traveltime_vs_priority"] = r
    # 3. do remote cells actually out-discover at equal effort? travel-time vs rarefied new@K
    r, _n = spearman(rk.travel_min.values, rk.rare_newK.values)
    out["traveltime_vs_discovery_rarefied"] = r
    # 4. reachable-frontier trade-off: among revisited cells, if we keep only those
    #    within a travel budget, how much discovery do we retain vs how much closer?
    rev = rev.sort_values("priority", ascending=False)
    frontier = {}
    for thr in [60, 120, 240, 480]:           # 1h, 2h, 4h, 8h to a city
        reach = rev[rev.travel_min <= thr]
        if len(rev):
            frontier[f"<= {thr}min"] = {
                "cells_kept_%": round(100 * len(reach) / len(rev), 1),
                "new_species_kept_%": round(100 * reach.new_species.sum() / max(rev.new_species.sum(), 1), 1),
                "median_travel_min_kept": round(float(reach.travel_min.median()), 1) if len(reach) else None,
            }
    out["reachable_frontier"] = frontier
    out["median_travel_all_min"] = round(float(rev.travel_min.median()), 1) if len(rev) else None
    # ROI re-rank: priority / (1 + travel_hours) — does it keep discovery while cutting travel?
    rev["roi"] = rev.priority / (1 + rev.travel_min / 60.0)
    top_pri = rev.nlargest(max(1, len(rev) // 5), "priority")
    top_roi = rev.nlargest(max(1, len(rev) // 5), "roi")
    out["top20_priority_median_travel_min"] = round(float(top_pri.travel_min.median()), 1)
    out["top20_roi_median_travel_min"] = round(float(top_roi.travel_min.median()), 1)
    out["top20_priority_newspecies"] = int(top_pri.new_species.sum())
    out["top20_roi_newspecies"] = int(top_roi.new_species.sum())
    return out


if __name__ == "__main__":
    names = sys.argv[1:] or [f.split("backtest_cells_")[-1].replace(".csv", "")
                             for f in sorted(glob.glob("cluster_results/backtest_cells_*.csv"))]
    results = []
    for name in names:
        try:
            r = analyse(name)
        except FileNotFoundError:
            print(f"{name}: no backtest cells; run voi_backtest first"); continue
        results.append(r)
        print(f"\n=== {name} === ({r['n_cells']} cells w/ travel-time)")
        print(f"  travel-time vs effort(n_train):  rho={r['traveltime_vs_effort']:+.3f}  (neg = reachable cells already over-sampled)")
        print(f"  travel-time vs PRIORITY:         rho={r['traveltime_vs_priority']:+.3f}  (pos = priority sends you to less-reachable cells = the tension)")
        print(f"  travel-time vs discovery@K:      rho={r['traveltime_vs_discovery_rarefied']:+.3f}")
        print(f"  median travel of top-20% PRIORITY cells: {r['top20_priority_median_travel_min']} min"
              f"  | of top-20% ROI(priority/cost) cells: {r['top20_roi_median_travel_min']} min"
              f"  (new species: {r['top20_priority_newspecies']} vs {r['top20_roi_newspecies']})")
        f = r["reachable_frontier"]
        for k, v in f.items():
            print(f"    keep {k}: {v['cells_kept_%']}% of cells, {v['new_species_kept_%']}% of new species")
    with open("cluster_results/voi_accessibility_results.json", "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nwrote cluster_results/voi_accessibility_results.json ({len(results)} taxa)")
