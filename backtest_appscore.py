"""Does the APP's actual composite score — not a generic proxy — predict where
post-split discovery happens?  ("directed beats opportunistic", the real claim.)

`voi_backtest.py` proved a scarcity+staleness PROXY predicts leakage-free
post-T new-species. This goes one step further and tests the score the app
actually ships: the default "Biodiversity impact" preset
`0.8*discover + 0.7*env + 0.3*urgency` (build_webapp.py PRESETS[0]).

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

Outcome + controls (rarefied new-species@K, permutation null) are reused from
voi_backtest so the two analyses are directly comparable.
"""
import sys, json, glob
import numpy as np
import pandas as pd
import voi_backtest as vb

RES = vb.RES                                  # 0.25 deg — SHARED grid with the app
CA = "cluster_results/ca"
# app row_format: ['lat','lon','discover','conservation','env','staleness','urgency','travel_min','n_train']
AX = {"discover": 2, "conservation": 3, "env": 4, "staleness": 5, "urgency": 6}
# default preset "Biodiversity impact" weights, order [discover, conservation, env, staleness, urgency]
PRESET_W = {"discover": 0.8, "conservation": 0.0, "env": 0.7, "staleness": 0.0, "urgency": 0.3}
# backtest taxon (iconic) -> app group file stem
GROUP = {"Amphibia": "Amphibia", "Aves": "Aves", "Insecta": "Insecta",
         "Mammalia": "Mammalia", "Reptilia": "Reptilia"}


def load_app_axes(group):
    """(gi,gj) -> dict of the app's per-cell axis values, on the shared grid."""
    d = json.load(open(f"{CA}/webapp_data_{group}.json"))
    rows = d[list(d.keys())[0]]
    out = {}
    for r in rows:
        gi = int(np.floor(r[0] / RES)); gj = int(np.floor(r[1] / RES))
        out[(gi, gj)] = {k: float(r[i]) for k, i in AX.items()}
    return out


def join_axes(cells, app):
    """Attach app env/urgency/discover/conservation to backtest cells by (gi,gj).
    Cells off the app land grid (e.g. just outside the CA bbox) are dropped."""
    keep, env, urg, disc, cons = [], [], [], [], []
    for _, row in cells.iterrows():
        a = app.get((int(row.gi), int(row.gj)))
        keep.append(a is not None)
        env.append(a["env"] if a else np.nan)
        urg.append(a["urgency"] if a else np.nan)
        disc.append(a["discover"] if a else np.nan)
        cons.append(a["conservation"] if a else np.nan)
    cells = cells.assign(app_env=env, app_urgency=urg,
                         app_discover=disc, app_conservation=cons)
    return cells[pd.Series(keep, index=cells.index)].copy()


def composite(cells, discover_col):
    """App default-preset composite with a choice of discover source."""
    w = PRESET_W
    return (w["discover"] * vb.norm(cells[discover_col])
            + w["env"] * vb.norm(cells.app_env)
            + w["urgency"] * vb.norm(cells.app_urgency)
            + w["conservation"] * vb.norm(cells.app_conservation))


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
    rk["app_leakfree"] = composite(rk, "scarcity")      # train-only discover proxy
    rk["app_shipped"] = composite(rk, "app_discover")   # all-time discover (LEAKY)

    out = {"taxon": name, "n_cells": int(len(cells)), "n_rarefied": int(len(rk)),
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
    res = {}
    for key, sc in scores.items():
        rho, p, mu, sd = vb.perm_test(sc.values, rk.rare_newK.values,
                                      np.random.default_rng(vb.SEED + 7))
        ratio, topm, botm = eff_ratio(rk, sc)
        res[key] = dict(spearman=rho, perm_p=p, eff_ratio_top_bottom=ratio,
                        new_at_K_top=topm, new_at_K_bottom=botm)
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
        for key in ["app_leakfree", "app_shipped", "discover_leakfree", "env", "urgency", "opportunistic_density"]:
            d = s[key]
            er = d["eff_ratio_top_bottom"]
            print(f"  {key:22s} rho={d['spearman']:+.3f} perm_p={fp(d['perm_p']):>8} "
                  f"eff_top/bot={('%.2fx' % er) if er else '  n/a'}")
    json.dump(results, open("cluster_results/voi_appscore_results.json", "w"), indent=2)
    print(f"\nwrote cluster_results/voi_appscore_results.json ({len(results)} taxa)")
