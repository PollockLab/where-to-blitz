"""Does the validated sampling-priority discover species that MATTER?

Extends the leakage-free VOI backtest (voi_backtest.py): instead of counting all
new-to-cell species equally, weight each discovery by its conservation relevance.
The self-contained, open-data weight is RANGE-RESTRICTION (a species seen in few
grid cells is range-restricted -> higher concern; this is the scarcity axis of
"functional rarity", Violle et al. 2017, and the basis of IUCN Red List criterion
B). When an authoritative per-species weight is available (IUCN/COSEWIC threat,
EDGE evolutionary distinctiveness) it can be swapped in via --weights.

Two questions:
  (1) Does priority predict rarity-WEIGHTED discovery at equal effort (rarefied)?
  (2) THE sustainability question: are the species discovered in high-priority
      cells DISPROPORTIONATELY rare, i.e. does mean discovery-rarity rise with
      priority? If yes, "where to go" doesn't just find more species, it finds
      more conservation-relevant ones.

Leakage note: priority is computed from pre-T data only (unchanged). The rarity
weight is a per-SPECIES property (observed range size over the full pull), applied
uniformly wherever that species is discovered; it does not encode the per-cell
priority->outcome link, so it does not leak. Disclosed: range is measured from the
collected data (observer-biased), a proxy for true range.
"""
import glob
import json
import sys

import _rootpath  # noqa: F401
import numpy as np
import pandas as pd

import voi_backtest as vb


def species_rarity(df):
    """Per-species weight from observed range size (# distinct RES-grid cells).
    Rare (few cells) -> high weight. Normalised to mean 1 so weighted counts stay
    comparable to unweighted counts."""
    d = df.dropna(subset=["taxon_id"]).copy()
    d = d[d["rank"].isin(["species", "subspecies", "variety", "form", "hybrid"])]
    gi = np.floor(d.lat / vb.RES).astype(int)
    gj = np.floor(d.lon / vb.RES).astype(int)
    cell = list(zip(gi, gj))
    d = d.assign(cell=cell)
    range_cells = d.groupby("taxon_id").cell.nunique()
    w = 1.0 / range_cells.astype(float)          # inverse range-size
    w = w / w.mean()                             # mean-1 normalise
    return w.to_dict()


def rarefy_weighted(cells, aux, weights, rng, K=5, reps=200):
    """Effort-equalised, conservation-WEIGHTED discovery: rarefy each cell to K
    test obs, sum the rarity weights of the distinct new-to-cell species."""
    tot = []
    for _, row in cells.iterrows():
        seen, tlist = aux.get((row.gi, row.gj), (set(), []))
        if len(tlist) < K:
            tot.append(np.nan); continue
        arr = np.array(tlist)
        acc = 0.0
        for _ in range(reps):
            samp = set(rng.choice(arr, size=K, replace=False).tolist()) - seen
            acc += sum(weights.get(s, 1.0) for s in samp)
        tot.append(acc / reps)
    return np.array(tot)


def analyse_conservation(name, df, K=5, weights=None, tag="rarity"):
    cells = vb.build_cells(df)
    if cells is None or len(cells) < 10:
        return None
    aux = cells.attrs["aux"]
    if weights is None:
        weights = species_rarity(df)
    cells["rare_newK"] = vb.rarefy_new_at_k(cells, aux, np.random.default_rng(vb.SEED), K=K)
    cells["w_newK"] = rarefy_weighted(cells, aux, weights, np.random.default_rng(vb.SEED), K=K)
    # mean weight PER discovered species (artifact-free: NOT diluted by empty draws,
    # which would otherwise make it track discovery frequency under uniform weights)
    cells["w_meanrarity"] = cells.w_newK / cells.rare_newK.where(cells.rare_newK > 0)
    rk = cells.dropna(subset=["rare_newK"]).copy()

    out = {"taxon": name, "n_cells_rarefied": len(rk),
           "perm_p_floor": 1.0 / vb.N_PERM, "K": K,
           "n_species": int(df.dropna(subset=["taxon_id"]).taxon_id.nunique())}
    # (1) priority predicts rarity-weighted discovery (effort-equalized)
    rho_w, p_w, _, sd_w = vb.perm_test(rk.priority.values, rk.w_newK.values,
                                       np.random.default_rng(vb.SEED + 7))
    out["weighted_discovery"] = {"spearman": rho_w, "perm_p": p_w, "null_sd": sd_w}
    # baseline: unweighted (count) — for contrast
    rho_u, p_u, _, _ = vb.perm_test(rk.priority.values, rk.rare_newK.values,
                                    np.random.default_rng(vb.SEED + 8))
    out["unweighted_discovery"] = {"spearman": rho_u, "perm_p": p_u}
    # (2) THE sustainability test: do high-priority cells discover RARER species?
    valid = rk.dropna(subset=["w_meanrarity"])
    rho_r, p_r, _, sd_r = vb.perm_test(valid.priority.values, valid.w_meanrarity.values,
                                       np.random.default_rng(vb.SEED + 9))
    out["discovery_rarity_vs_priority"] = {"spearman": rho_r, "perm_p": p_r, "null_sd": sd_r}
    # top vs bottom priority: mean rarity of discoveries
    if len(valid) >= 6:
        q = valid.priority.quantile([1/3, 2/3])
        top = valid[valid.priority >= q.iloc[1]].w_meanrarity
        bot = valid[valid.priority <= q.iloc[0]].w_meanrarity
        out["mean_rarity_top"] = float(top.mean())
        out["mean_rarity_bottom"] = float(bot.mean())
        out["rarity_ratio_top_vs_bottom"] = float(top.mean() / bot.mean()) if bot.mean() else None
    out["weight_tag"] = tag
    cells.to_csv(f"cluster_results/conservation_cells_{name}_{tag}.csv", index=False)
    return out


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(glob.glob("cluster_results/inat_*.csv"))
    results = []
    for f in files:
        name = f.split("inat_")[-1].replace(".csv", "")
        r = analyse_conservation(name, pd.read_csv(f))
        if not r:
            print(f"{name}: insufficient"); continue
        results.append(r)
        pf = r["perm_p_floor"]; fp = lambda p: f"<{pf:.4f}" if p < pf else f"{p:.4f}"
        wd, dr = r["weighted_discovery"], r["discovery_rarity_vs_priority"]
        print(f"\n=== {name} === cells(rarefied)={r['n_cells_rarefied']} species={r['n_species']}")
        print(f"  priority -> rarity-WEIGHTED discovery: rho={wd['spearman']:+.3f} p={fp(wd['perm_p'])}"
              f"  (unweighted was {r['unweighted_discovery']['spearman']:+.3f})")
        print(f"  priority -> MEAN RARITY of discoveries: rho={dr['spearman']:+.3f} p={fp(dr['perm_p'])}"
              f"  | top/bottom rarity ratio={r.get('rarity_ratio_top_vs_bottom') or float('nan'):.2f}x")
    with open("cluster_results/voi_conservation_results.json", "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nwrote cluster_results/voi_conservation_results.json ({len(results)} taxa)")
