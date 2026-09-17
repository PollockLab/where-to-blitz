"""Self-contained VOI / sampling-priority backtest on public iNaturalist data.

Closes north-star bar #4 ("VOI is validated, not asserted") WITHOUT Larocque's
private rasters: a leakage-free temporal split of iNat project 228908 (BC, 2025
pilot season). Compute each grid cell's priority from observations up to T, then
test on observations after T whether high-priority cells actually yielded more
NEW-to-cell species.

Method grounded in: Di Cecco et al. 2021 (BioScience, effort/observer-bias
confound -> use rate-per-observation on revisited cells); Chao 1984 / Colwell &
Coddington 1994 (richness extrapolation); Mondain-Monval et al. 2024 MEE
(adaptive sampling improves SDMs -- motivation, a simulation study not a
temporal-split backtest, so this is a novel composite). Lift + Spearman +
permutation null are standard.

Honest scope: retrospective simulation over *collected* observations, not
prospective field guidance; "new species" = new to that cell's iNat record, not
new-to-science; inherits iNat observer bias (controlled, not eliminated).
"""
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SPLIT = pd.Timestamp("2025-07-01")     # train < SPLIT <= test
RES = 0.25                              # grid degrees
SEED = 0
N_PERM = 2000
TOPK_FRAC = 0.20                        # "top 20% priority cells"
DOUBLE_M = (10, 20, 40)                 # seen-set sizes for double rarefaction
DOUBLE_REPS = 200                       # draws per cell per M
MIN_DOUBLE_CELLS = 20                   # below this the doubly-rarefied rho is not reported
TRAVEL_INDEX = "cluster_results/ca/index.json"
TRAVEL_GROUP = "All biodiversity"       # travel time is not taxon-specific


def norm(s):
    s = s.astype(float)
    rng = s.max() - s.min()
    return (s - s.min()) / rng if rng else s * 0.0


def chao1(counts):
    """Chao1 estimated richness from a vector of per-species observation counts."""
    counts = np.asarray(counts)
    S_obs = (counts > 0).sum()
    f1 = (counts == 1).sum()
    f2 = (counts == 2).sum()
    if f2 > 0:
        return S_obs + f1 * f1 / (2.0 * f2)
    return S_obs + f1 * (f1 - 1) / 2.0      # bias-corrected when f2 == 0


def build_cells(df):
    """Split, grid, compute train-only priority and test-only discovery outcome."""
    df = df.dropna(subset=["taxon_id", "observed_on"]).copy()
    df["date"] = pd.to_datetime(df["observed_on"], errors="coerce")
    df = df.dropna(subset=["date"])
    # species-level only (rank species/subspecies); drop coarse IDs that can't be "new species"
    df = df[df["rank"].isin(["species", "subspecies", "variety", "form", "hybrid"])]
    df["gi"] = np.floor(df.lat / RES).astype(int)
    df["gj"] = np.floor(df.lon / RES).astype(int)
    train = df[df.date < SPLIT]
    test = df[df.date >= SPLIT]
    if len(train) == 0 or len(test) == 0:
        return None

    # --- train: priority inputs, computed ONLY on pre-T data (leakage-free) ---
    rows = []
    train_sp = {k: set(v) for k, v in train.groupby(["gi", "gj"]).taxon_id}
    last_seen = train.groupby(["gi", "gj"]).date.max()
    n_train = train.groupby(["gi", "gj"]).size()
    # Chao1 undiscovered estimate per cell (estimated species still unseen at T)
    chao_unseen = {}
    for key, g in train.groupby(["gi", "gj"]):
        c = g.taxon_id.value_counts().values
        chao_unseen[key] = max(chao1(c) - (c > 0).sum(), 0.0)

    # --- test: outcome = new-to-cell species, and effort (n test obs) ---
    test_sp = {k: set(v) for k, v in test.groupby(["gi", "gj"]).taxon_id}
    n_test = test.groupby(["gi", "gj"]).size()

    # per-cell ordered lists of observation species (kept for rarefaction, both sides)
    test_list = {k: list(v) for k, v in test.groupby(["gi", "gj"]).taxon_id}
    train_list = {k: list(v) for k, v in train.groupby(["gi", "gj"]).taxon_id}
    aux = {}
    for key in train_sp:                      # only cells with >=1 train obs (priority defined)
        gi, gj = key
        seen = train_sp[key]
        new_sp = test_sp.get(key, set()) - seen
        nt = int(n_test.get(key, 0))
        aux[key] = (seen, test_list.get(key, []), train_list.get(key, []))
        rows.append({
            "gi": gi, "gj": gj, "clat": (gi + 0.5) * RES, "clon": (gj + 0.5) * RES,
            "n_train": int(n_train[key]),
            "days_since": (SPLIT - last_seen[key]).days,
            "chao_unseen": chao_unseen[key],
            "n_test": nt,
            "new_species": len(new_sp),
            "revisited": nt > 0,
        })
    cells = pd.DataFrame(rows)
    cells.attrs["aux"] = aux

    # --- priority (train-only), matches notebook proxy: scarcity + staleness ---
    cells["scarcity"] = norm(1.0 / cells.n_train)
    cells["staleness"] = norm(cells.days_since)
    cells["priority"] = (cells.scarcity + cells.staleness) / 2.0
    # density = anti-priority (where people already sampled heavily)
    cells["density"] = norm(cells.n_train)
    # effort-controlled discovery outcome
    cells["new_rate"] = cells.new_species / cells.n_test.where(cells.n_test > 0)
    return cells


def rarefy_new_at_k(cells, aux, rng, K=5, reps=200):
    """Effort-EQUALIZED discovery: for every cell with >=K test obs, subsample K
    test observations and count distinct species new to that cell (vs its train
    set), averaged over `reps` draws. This removes the species-accumulation
    saturation confound (low-effort cells otherwise sit on the steep curve) -- the
    decisive control the rate-on-revisited test alone does not provide."""
    out = []
    for _, row in cells.iterrows():
        key = (row.gi, row.gj)
        seen, tlist, _trlist = aux.get(key, (set(), [], []))
        if len(tlist) < K:
            out.append(np.nan); continue
        arr = np.array(tlist)
        acc = 0.0
        for _ in range(reps):
            samp = rng.choice(arr, size=K, replace=False)
            acc += len(set(samp.tolist()) - seen)
        out.append(acc / reps)
    return np.array(out, dtype=float)


def double_rarefy_new_at_k(cells, aux, rng, M=20, K=5, reps=DOUBLE_REPS):
    """Effort-equalized on BOTH sides: subsample M train ("already seen") and K test
    observations per cell, then count test species absent from the M-sample.

    `rarefy_new_at_k` equalizes test effort only. scarcity = norm(1/n_train) is a
    strictly monotone transform of n_train, so a cell with few train records
    necessarily carries a smaller seen set and therefore finds more species "new to
    the cell" at any fixed K -- part of the single-rarefied signal is that
    mechanical identity rather than discovery. Holding the seen set at M removes it.
    Cells with fewer than M train or K test observations return NaN.
    """
    out = []
    for _, row in cells.iterrows():
        key = (row.gi, row.gj)
        _seen, tlist, trlist = aux.get(key, (set(), [], []))
        if len(trlist) < M or len(tlist) < K:
            out.append(np.nan); continue
        tra, tea = np.array(trlist), np.array(tlist)
        acc = 0.0
        for _ in range(reps):
            samp = set(rng.choice(tea, size=K, replace=False).tolist())
            sub = set(rng.choice(tra, size=M, replace=False).tolist())
            acc += len(samp - sub)
        out.append(acc / reps)
    return np.array(out, dtype=float)


def double_rarefied_records(cells, aux, K=5, Ms=DOUBLE_M, reps=DOUBLE_REPS, seed=SEED):
    """Doubly-rarefied rho + permutation p for scarcity and priority, at several M."""
    recs = []
    for M in Ms:
        y = double_rarefy_new_at_k(cells, aux, np.random.default_rng(seed + 2), M=M, K=K, reps=reps)
        sub = cells.assign(double_newK=y).dropna(subset=["double_newK"])
        rec = {"M": int(M), "K": int(K), "reps": int(reps), "n_cells": int(len(sub))}
        if len(sub) >= MIN_DOUBLE_CELLS:
            for col in ("scarcity", "priority"):
                rho, pv, mu, sd = perm_test(sub[col].values, sub.double_newK.values,
                                            np.random.default_rng(seed + 3))
                rec[col] = {"spearman": rho, "perm_p": pv, "null_mean": mu, "null_sd": sd}
        else:
            rec["note"] = (f"fewer than {MIN_DOUBLE_CELLS} cells have >={M} train and "
                           f">={K} test observations; not reported")
        recs.append(rec)
    return recs


def load_travel_minutes(index_path=TRAVEL_INDEX, group=TRAVEL_GROUP):
    """Per-cell Weiss 2018 travel time from the committed national build.

    Returns a lat/lon/travel_min frame on the 25 km equal-area lattice, or None when
    the national build is absent. Travel time is a baseline that can lose: it is not
    a rank transform of scarcity, unlike density.
    """
    idx_path = Path(index_path)
    if not idx_path.exists():
        return None
    idx = json.loads(idx_path.read_text())
    fname = idx.get("files", {}).get(group)
    cols = idx.get("row_format")
    if not fname or not cols or "travel_min" not in cols:
        return None
    data_path = idx_path.parent / fname
    if not data_path.exists():
        return None
    rows = json.loads(data_path.read_text()).get(group)
    if not rows:
        return None
    t = pd.DataFrame(rows, columns=cols)[["lat", "lon", "travel_min"]]
    return t.dropna()


def cell_travel_minutes(cells, travel):
    """Median travel time of the national cells falling inside each backtest cell."""
    t = travel.copy()
    t["gi"] = np.floor(t.lat / RES).astype(int)
    t["gj"] = np.floor(t.lon / RES).astype(int)
    med = t.groupby(["gi", "gj"]).travel_min.median()
    keys = pd.MultiIndex.from_arrays([cells.gi.values, cells.gj.values])
    return np.asarray(keys.map(med), dtype=float)


def spearman(x, y):
    rx = pd.Series(x).rank().values
    ry = pd.Series(y).rank().values
    if np.std(rx) == 0 or np.std(ry) == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def perm_test(score, outcome, rng, n=N_PERM):
    """Permutation null: shuffle the priority score across cells, recompute
    Spearman vs the (fixed) outcome. p = P(|rho_null| >= |rho_obs|)."""
    obs = spearman(score, outcome)
    score = np.asarray(score, float)
    null = np.array([spearman(rng.permutation(score), outcome) for _ in range(n)])
    null = null[~np.isnan(null)]
    if np.isnan(obs) or null.size == 0:
        return obs, float("nan"), float("nan"), float("nan")
    p = float((np.abs(null) >= abs(obs)).mean())
    return obs, p, float(null.mean()), float(null.std())


def lift(cells, score_col, topk_frac=TOPK_FRAC):
    """Share of all post-T raw new-species VOLUME captured by the top-K% cells vs
    the area baseline (K%). For priority this is EXPECTED BELOW 1: high-priority
    cells are under-visited, so they hold little raw volume -- the honest
    'efficiency, not volume' point. (An effort-matched baseline was dropped: with
    priority cells' tiny effort it collapsed to a single mega-cell, a strawman.)"""
    c = cells.sort_values(score_col, ascending=False).reset_index(drop=True)
    k = max(1, round(len(c) * topk_frac))
    total_new = c.new_species.sum()
    if total_new == 0:
        return None
    captured = c.head(k).new_species.sum() / total_new
    area_base = k / len(c)
    return {"topk": k, "n_cells": len(c), "captured": float(captured),
                "area_baseline": float(area_base), "lift_vs_area": float(captured / area_base)}


def analyse(name, df, K=5, travel=None, Ms=DOUBLE_M, double_reps=DOUBLE_REPS):
    cells = build_cells(df)
    if cells is None or len(cells) < 10:
        return None
    aux = cells.attrs["aux"]
    # travel time per cell: the baseline that can lose (see load_travel_minutes)
    cells["travel_min"] = (cell_travel_minutes(cells, travel) if travel is not None
                           else np.nan)
    rng = np.random.default_rng(SEED)
    rev = cells[cells.revisited].copy()        # effort-controlled subset
    out = {"taxon": name, "n_cells": len(cells), "n_revisited": len(rev),
           "n_train": int(df_train_count(df)), "n_test": int(df_test_count(df)),
           "total_new_species": int(cells.new_species.sum()),
           "perm_p_floor": 1.0 / N_PERM}

    # (1) raw VOLUME association (all cells): priority vs raw new-species count.
    # Expected NEGATIVE -- high-priority cells are under-visited, so contribute
    # little total volume. The volume-vs-efficiency distinction, stated honestly.
    out["spearman_priority_newcount"] = spearman(cells.priority, cells.new_species)

    # (1b) DECISIVE control: rarefy every cell to K test obs, count new-to-cell
    # species. Removes the saturation confound that the rate-on-revisited test
    # leaves in. cells with >=K test obs only. This is the defensible headline.
    cells["rare_newK"] = rarefy_new_at_k(cells, aux, np.random.default_rng(SEED), K=K)
    rk = cells.dropna(subset=["rare_newK"])
    out["K"] = K
    out["n_cells_rarefied"] = len(rk)
    rho_r, p_r, mu_r, sd_r = perm_test(rk.priority.values, rk.rare_newK.values,
                                       np.random.default_rng(SEED + 1))
    out["rarefied_priority"] = {"spearman": rho_r, "perm_p": p_r, "null_mean": mu_r, "null_sd": sd_r}
    out["rarefied_staleness"] = {"spearman": spearman(rk.staleness.values, rk.rare_newK.values)}

    # (1d) DOUBLE rarefaction: equalize the SEEN set to M as well as test effort to K.
    # (1b) leaves scarcity's mechanical edge intact -- fewer prior records means a
    # smaller seen set, so more of anything counts as new. This is the statistic the
    # verdict should rest on; it is reported alongside, under its own key, so readers
    # of the existing fields are untouched.
    out["double_rarefied"] = double_rarefied_records(
        cells, aux, K=K, Ms=Ms, reps=double_reps, seed=SEED)
    # rarefied top/bottom-tercile efficiency ratio (stable: equal effort K per cell)
    if len(rk) >= 6:
        q = rk.priority.quantile([1/3, 2/3])
        top = rk[rk.priority >= q.iloc[1]].rare_newK
        bot = rk[rk.priority <= q.iloc[0]].rare_newK
        out["rarefied_ratio_top_vs_bottom"] = (
            float(top.mean() / bot.mean()) if len(bot) and bot.mean() > 0 else None)
        out["rare_newK_top"] = float(top.mean()) if len(top) else None
        out["rare_newK_bottom"] = float(bot.mean()) if len(bot) else None

    # (1c) raw per-visit rate ratio (kept for context; UNSTABLE magnitude --
    # tercile-median denominator can collapse to 0, so report direction only)
    q2 = rev.priority.quantile([1/3, 2/3])
    out["rate_median_top"] = float(rev[rev.priority >= q2.iloc[1]].new_rate.median())
    out["rate_median_bottom"] = float(rev[rev.priority <= q2.iloc[0]].new_rate.median())

    # (2) EFFORT-CONTROLLED: priority vs new-species RATE on revisited cells (the honest test)
    for col in ["priority", "scarcity", "staleness", "density", "chao_unseen"]:
        rho, p, mu, sd = perm_test(rev[col].values, rev.new_rate.values, rng)
        out[f"rate_{col}"] = {"spearman": rho, "perm_p": p, "null_mean": mu, "null_sd": sd}

    # (2b) TRAVEL TIME baseline: unlike density (the exact rank-inverse of scarcity,
    # so not a test at all), travel time is an independent ranking that can lose.
    out["rate_travel_min"] = None
    out["rarefied_travel_min"] = None
    rev_t = rev.dropna(subset=["travel_min"])
    if len(rev_t) >= 10:
        rho, pv, mu, sd = perm_test(rev_t.travel_min.values, rev_t.new_rate.values, rng)
        out["rate_travel_min"] = {"spearman": rho, "perm_p": pv, "null_mean": mu,
                                  "null_sd": sd, "n_cells": int(len(rev_t))}
    rk_t = rk.dropna(subset=["travel_min"])
    if len(rk_t) >= 10:
        rho, pv, mu, sd = perm_test(rk_t.travel_min.values, rk_t.rare_newK.values, rng)
        out["rarefied_travel_min"] = {"spearman": rho, "perm_p": pv, "null_mean": mu,
                                      "null_sd": sd, "n_cells": int(len(rk_t))}

    # (3) lift over baselines (all cells, raw discoveries)
    out["lift_priority"] = lift(cells, "priority")
    out["lift_random"] = 1.0   # by construction the area baseline
    # baseline rankings for comparison
    out["lift_scarcity"] = lift(cells, "scarcity")
    out["lift_density_antipriority"] = lift(cells, "density")
    return out, cells


def sensitivity(df, grids=(0.1, 0.25, 0.5, 1.0), splits=("2025-06-15", "2025-07-01", "2025-07-15", "2025-08-01"), K=5):
    """Direction-stability check: recompute the decisive rarefied rho(priority,
    new@K) across grid resolutions and split dates. Magnitude moves; the question
    is whether the SIGN and significance survive arbitrary analyst choices."""
    global RES, SPLIT
    res0, split0 = RES, SPLIT
    grid_rows, split_rows = [], []
    try:
        SPLIT = split0
        for g in grids:
            RES = g
            r = analyse("_", df, K=K, Ms=())
            out = r[0] if r else None
            grid_rows.append((g, out["rarefied_priority"]["spearman"] if out else np.nan,
                              out["rarefied_priority"]["perm_p"] if out else np.nan))
        RES = res0
        for s in splits:
            SPLIT = pd.Timestamp(s)
            r = analyse("_", df, K=K, Ms=())
            out = r[0] if r else None
            split_rows.append((s, out["rarefied_priority"]["spearman"] if out else np.nan,
                               out["rarefied_priority"]["perm_p"] if out else np.nan))
    finally:
        RES, SPLIT = res0, split0
    return grid_rows, split_rows


def df_train_count(df):
    d = pd.to_datetime(df["observed_on"], errors="coerce")
    return (d < SPLIT).sum()


def df_test_count(df):
    d = pd.to_datetime(df["observed_on"], errors="coerce")
    return (d >= SPLIT).sum()


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(glob.glob("cluster_results/inat_*.csv"))
    travel = load_travel_minutes()
    if travel is None:
        print(f"note: {TRAVEL_INDEX} absent -- the travel-time baseline is skipped")
    results = []
    for f in files:
        name = f.split("inat_")[-1].replace(".csv", "")
        df = pd.read_csv(f)
        r = analyse(name, df, travel=travel)
        if r is None:
            print(f"{name}: insufficient data"); continue
        res, cells = r
        results.append(res)
        cells.to_csv(f.replace("inat_", "backtest_cells_"), index=False)
        rp = res["rate_priority"]; rr = res["rarefied_priority"]
        pf = res["perm_p_floor"]
        fmtp = lambda p: f"<{pf:.4f}" if p < pf else f"{p:.4f}"
        print(f"\n=== {name} ===  cells={res['n_cells']} revisited={res['n_revisited']} "
              f"rarefied(K={res['K']})={res['n_cells_rarefied']} new_species={res['total_new_species']}")
        print(f"  [DECISIVE, effort-equalized] rarefied rho(priority,new@K)={rr['spearman']:.3f} "
              f"perm_p={fmtp(rr['perm_p'])} | rarefied efficiency ratio top/bottom="
              f"{res.get('rarefied_ratio_top_vs_bottom') or float('nan'):.1f}x")
        for rec in res["double_rarefied"]:
            if "scarcity" in rec:
                print(f"  [DOUBLY rarefied, seen=M={rec['M']} & test=K={rec['K']}] "
                      f"rho(scarcity,new)={rec['scarcity']['spearman']:+.3f} "
                      f"perm_p={fmtp(rec['scarcity']['perm_p'])} | "
                      f"rho(priority,new)={rec['priority']['spearman']:+.3f} "
                      f"perm_p={fmtp(rec['priority']['perm_p'])}  (n={rec['n_cells']})")
            else:
                print(f"  [DOUBLY rarefied, M={rec['M']}] {rec['note']} (n={rec['n_cells']})")
        tb = res.get("rarefied_travel_min")
        if tb:
            print(f"  travel-time baseline (rarefied outcome): rho={tb['spearman']:+.3f} "
                  f"perm_p={fmtp(tb['perm_p'])}  (n={tb['n_cells']})")
        print(f"  rate~priority (revisited): rho={rp['spearman']:.3f} perm_p={fmtp(rp['perm_p'])} | "
              f"scarcity={res['rate_scarcity']['spearman']:.3f} staleness={res['rate_staleness']['spearman']:.3f} "
              f"density(anti)={res['rate_density']['spearman']:.3f}")
        lp = res["lift_priority"]
        print(f"  raw-VOLUME (all cells): rho={res['spearman_priority_newcount']:.3f} | "
              f"top-20% priority captures {lp['captured']*100:.0f}% of volume vs {lp['area_baseline']*100:.0f}% area "
              f"(<1 = under-visited, as expected)")
    with open("cluster_results/voi_backtest_results.json", "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nwrote cluster_results/voi_backtest_results.json ({len(results)} taxa)")
