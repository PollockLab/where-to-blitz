"""Geographic generalization check: rerun the app-score backtest on a region
DISJOINT from BC (Eastern Canada). Same method, same app axes, same outcome —
does directed>opportunistic survive out-of-sample geography?"""
import json
import pandas as pd
import backtest_appscore as bas

results = []
for tx in ["Aves", "Insecta", "Mammalia"]:
    df = pd.read_csv(f"cluster_results/inat_east_{tx}.csv")
    r = bas.analyse(tx, df)
    if r is None:
        print(f"{tx}: insufficient"); continue
    out, _ = r
    results.append(out)
    s = out["scores"]
    fp = lambda p: f"<{out['perm_p_floor']:.4f}" if p is not None and p < out["perm_p_floor"] else (f"{p:.4f}" if p is not None else "nan")
    print(f"\n=== EAST {tx} === cells={out['n_cells']} rarefied={out['n_rarefied']} new_sp={out['total_new_species']}")
    for k in ["discover_leakfree", "app_leakfree", "opportunistic_density"]:
        d = s[k]; er = d["eff_ratio_top_bottom"]
        print(f"  {k:22s} rho={d['spearman']:+.3f} perm_p={fp(d['perm_p']):>8} "
              f"eff_top/bot={('%.2fx' % er) if er else ' n/a'}")
json.dump(results, open("cluster_results/voi_appscore_east_results.json", "w"), indent=2)
print(f"\nwrote cluster_results/voi_appscore_east_results.json ({len(results)} taxa)")
