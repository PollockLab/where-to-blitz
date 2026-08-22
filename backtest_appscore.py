"""Does the APP's actual composite score — not a generic proxy — predict where
post-split discovery happens?  ("directed beats opportunistic", the real claim.)

`voi_backtest.py` proved a scarcity+staleness PROXY predicts leakage-free
post-T new-species. This goes one step further and tests the scores the app
actually ships, read straight from goal_presets.PRESETS so this file cannot
drift away from the dropdown. It scored a hardcoded
`0.8*discover + 0.7*env + 0.3*urgency` for months after the presets moved on,
which is a blend no shipped preset has ever used.

Leakage discipline (the crux):
- The app's shipped `discover`/`staleness` axes are built from ALL-TIME iNat
  density, so feeding them post-T outcomes would leak. `env` (CHELSA climate)
  and `urgency` (Hansen forest loss) are leakage-free BY SOURCE (not derived
  from observation timing).
- DEFENSIBLE HEADLINE = `app_leakfree`: rebuild the discover axis from
  TRAIN-ONLY scarcity (what the app would have known at T), keep the real
  leakage-free env+urgency, combine with the app's own preset weights.
- CONSISTENCY CHECK = `app_shipped`: the literally-joined axes, reported with
  the leak caveat, to show the shipped score points the same way.
- Those two carry the DEFAULT preset. Every other shipped preset is scored
  alongside them under its own slug, so a preset the app ships can no longer go
  unmeasured. A preset that weights `staleness` gets a `_shipped` key only:
  staleness is built from observation timing and has no train-only stand-in the
  way discover has `scarcity`.

Outcome + controls (rarefied new-species@K, permutation null) are reused from
voi_backtest so the two analyses are directly comparable.
"""
import json
import sys

import numpy as np
import pandas as pd

import voi_backtest as vb
from goal_presets import AXES, DEFAULT, PRESETS

RES = vb.RES                                  # 0.25 deg — SHARED grid with the app
CA = "cluster_results/ca"
# app row_format: ['lat','lon','discover','conservation','env','staleness','urgency','travel_min','n_train']
AX = {"discover": 2, "conservation": 3, "env": 4, "staleness": 5, "urgency": 6}
# Axes the app derives from observation timing: scoring them against post-T outcomes leaks
# unless a train-only stand-in exists. discover has one (voi_backtest's scarcity); staleness
# does not, so presets weighting it are reported as-shipped only.
LEAKY_WITHOUT_STANDIN = {"staleness"}
# backtest taxon (iconic) -> app group file stem
GROUP = {"Amphibia": "Amphibia", "Aves": "Aves", "Insecta": "Insecta",
         "Mammalia": "Mammalia", "Reptilia": "Reptilia"}


def load_app_axes(group):
    """(gi,gj) -> dict of the app's per-cell axis values, on the shared grid."""
    d = json.load(open(f"{CA}/webapp_data_{group}.json"))
    rows = d[next(iter(d.keys()))]
    out = {}
    for r in rows:
        gi = int(np.floor(r[0] / RES)); gj = int(np.floor(r[1] / RES))
        out[(gi, gj)] = {k: float(r[i]) for k, i in AX.items()}
    return out


def join_axes(cells, app):
    """Attach app env/urgency/discover/conservation to backtest cells by (gi,gj).
    Cells off the app land grid (e.g. just outside the CA bbox) are dropped."""
    keep, env, urg, disc, cons, stal = [], [], [], [], [], []
    for _, row in cells.iterrows():
        a = app.get((int(row.gi), int(row.gj)))
        keep.append(a is not None)
        env.append(a["env"] if a else np.nan)
        urg.append(a["urgency"] if a else np.nan)
        disc.append(a["discover"] if a else np.nan)
        cons.append(a["conservation"] if a else np.nan)
        stal.append(a["staleness"] if a else np.nan)
    cells = cells.assign(app_env=env, app_urgency=urg, app_discover=disc,
                         app_conservation=cons, app_staleness=stal)
    return cells[pd.Series(keep, index=cells.index)].copy()


def composite(cells, weights, discover_col):
    """One preset's blend over the app's own axes, with a choice of discover source."""
    col = {"discover": cells[discover_col], "conservation": cells.app_conservation,
           "env": cells.app_env, "staleness": cells.app_staleness,
           "urgency": cells.app_urgency}
    total = 0.0
    for ax, w in zip(AXES, weights, strict=True):
        if w:
            total = total + w * vb.norm(col[ax])
    return total


def eff_ratio(rk, score):
    """Effort-equalized efficiency: mean new@K in top vs bottom score-tercile."""
    if len(rk) < 6:
        return None, None, None
    q = score.quantile([1/3, 2/3])
    top = rk.rare_newK[score >= q.iloc[1]]
    bot = rk.rare_newK[score <= q.iloc[0]]
    if not len(bot) or bot.mean() <= 0:
        return None, float(top.mean()) if len(top) else None, None
    return float(top.mean() / bot.mean()), float(top.mean()), float(bot.mean())


def analyse(name, df, K=5):
    cells = vb.build_cells(df)
    if cells is None or len(cells) < 10:
        return None
    aux = cells.attrs["aux"]
    app = load_app_axes(GROUP[name])
    cells = join_axes(cells, app)
    if len(cells) < 10:
        return None
    # leakage-free outcome (rarefied new-to-cell species at equal effort K)
    cells["rare_newK"] = vb.rarefy_new_at_k(cells, aux, np.random.default_rng(vb.SEED), K=K)
    rk = cells.dropna(subset=["rare_newK", "app_env", "app_urgency"]).copy()
    if len(rk) < 10:
        return None

    # the two app composites: leakage-free (headline) and as-shipped (consistency)
    rk["app_leakfree"] = composite(rk, DEFAULT, "scarcity")     # train-only discover proxy
    rk["app_shipped"] = composite(rk, DEFAULT, "app_discover")  # all-time discover (LEAKY)

    out = {"taxon": name, "n_cells": len(cells), "n_rarefied": len(rk),
           "K": K, "perm_p_floor": 1.0 / vb.N_PERM,
           "total_new_species": int(cells.new_species.sum())}

    # candidate scores: the two composites, each leakage-free axis alone, and the
    # opportunistic negative control (all-time density => where people ALREADY go).
    scores = {
        "app_leakfree": rk.app_leakfree,
        "app_shipped": rk.app_shipped,
        "discover_leakfree": vb.norm(rk.scarcity),
        "env": vb.norm(rk.app_env),
        "urgency": vb.norm(rk.app_urgency),
        "opportunistic_density": vb.norm(rk.density),   # NEGATIVE control
    }
    # ...plus every other preset the dropdown offers, under its own slug
    for p in PRESETS:
        if p["w"] == DEFAULT:
            continue
        slug = p["name"].lower().replace(" ", "_")
        weighted = {ax for ax, w in zip(AXES, p["w"], strict=True) if w}
        if "discover" not in weighted:
            # no all-time discover in the blend, so there is no shipped/leak-free split
            scores[slug] = composite(rk, p["w"], "app_discover")
            continue
        scores[f"{slug}_shipped"] = composite(rk, p["w"], "app_discover")
        if not weighted & LEAKY_WITHOUT_STANDIN:
            scores[f"{slug}_leakfree"] = composite(rk, p["w"], "scarcity")
    res = {}
    for key, sc in scores.items():
        rho, p, _mu, _sd = vb.perm_test(sc.values, rk.rare_newK.values,
                                      np.random.default_rng(vb.SEED + 7))
        ratio, topm, botm = eff_ratio(rk, sc)
        res[key] = {"spearman": rho, "perm_p": p, "eff_ratio_top_bottom": ratio,
                        "new_at_K_top": topm, "new_at_K_bottom": botm}
    out["scores"] = res
    return out, rk


if __name__ == "__main__":
    files = sys.argv[1:] or [f"cluster_results/inat_{t}.csv" for t in GROUP]
    results = []
    for f in files:
        name = f.split("inat_")[-1].replace(".csv", "")
        if name not in GROUP:
            print(f"{name}: no app group mapping, skip"); continue
        df = pd.read_csv(f)
        r = analyse(name, df)
        if r is None:
            print(f"{name}: insufficient data"); continue
        out, _ = r
        results.append(out)
        pf = out["perm_p_floor"]
        fp = lambda p: f"<{pf:.4f}" if p is not None and p < pf else (f"{p:.4f}" if p is not None else "nan")
        s = out["scores"]
        print(f"\n=== {name} === cells={out['n_cells']} rarefied={out['n_rarefied']} new_sp={out['total_new_species']}")
        for key, d in s.items():
            er = d["eff_ratio_top_bottom"]
            print(f"  {key:22s} rho={d['spearman']:+.3f} perm_p={fp(d['perm_p']):>8} "
                  f"eff_top/bot={(f'{er:.2f}x') if er else '  n/a'}")
    json.dump(results, open("cluster_results/voi_appscore_results.json", "w"), indent=2)
    print(f"\nwrote cluster_results/voi_appscore_results.json ({len(results)} taxa)")
