"""Evidence for the surfaced decision: what default preset maximizes discovery?

The app-score backtest found the current default `0.8*discover + 0.7*env +
0.3*urgency` DILUTES the discovery signal that the discover axis carries alone.
This quantifies the gap across all 8 taxon-region datasets (5 BC + 3 Eastern
Canada) so the re-tune is a measured recommendation, not an assertion.

Compares, by leakage-free rarefied Spearman(score, new-species@K):
  - current_default    [discover 0.8, env 0.7, urgency 0.3]   (shipped)
  - discover_only      [1, 0, 0]                              (the signal)
  - balanced_proposal  [1.0, 0.2, 0.1]                        (keep env/urgency
                        as minor tie-breakers for their OTHER objectives, but
                        let discovery dominate)

Honest scope: this optimizes ONLY the species-discovery objective. env (climate
coverage) and urgency (habitat loss) exist to serve DIFFERENT goals the app
offers as presets; this does not argue for deleting them, only that the DEFAULT
preset over-weights them for the discovery framing it advertises.
"""
import glob, json
import numpy as np
import pandas as pd
import voi_backtest as vb
import backtest_appscore as bas

CANDIDATES = {
    "current_default": {"discover": 0.8, "env": 0.7, "urgency": 0.3},
    "discover_only":   {"discover": 1.0, "env": 0.0, "urgency": 0.0},
    "balanced_proposal": {"discover": 1.0, "env": 0.2, "urgency": 0.1},
}


def score(rk, w):
    return (w["discover"] * vb.norm(rk.scarcity)        # leakage-free discover
            + w["env"] * vb.norm(rk.app_env)
            + w["urgency"] * vb.norm(rk.app_urgency))


def prep(name, path):
    df = pd.read_csv(path)
    cells = vb.build_cells(df)
    if cells is None or len(cells) < 10:
        return None
    aux = cells.attrs["aux"]
    cells = bas.join_axes(cells, bas.load_app_axes(bas.GROUP[name]))
    cells["rare_newK"] = vb.rarefy_new_at_k(cells, aux, np.random.default_rng(vb.SEED), K=5)
    rk = cells.dropna(subset=["rare_newK", "app_env", "app_urgency"])
    return rk if len(rk) >= 10 else None


def main():
    datasets = []
    for path in sorted(glob.glob("cluster_results/inat_*.csv")):
        stem = path.split("inat_")[-1].replace(".csv", "")
        region = "east" if stem.startswith("east_") else "bc"
        name = stem.replace("east_", "")
        if name in bas.GROUP:
            datasets.append((f"{region}:{name}", name, path))

    rows = []
    for label, name, path in datasets:
        rk = prep(name, path)
        if rk is None:
            continue
        row = {"dataset": label}
        for cand, w in CANDIDATES.items():
            rho, _, _, _ = vb.perm_test(score(rk, w).values, rk.rare_newK.values,
                                        np.random.default_rng(vb.SEED + 3), n=200)
            row[cand] = rho
        rows.append(row)

    df = pd.DataFrame(rows)
    summary = {c: float(df[c].mean()) for c in CANDIDATES}
    wins = {c: int((df[list(CANDIDATES)].idxmax(axis=1) == c).sum()) for c in CANDIDATES}
    out = {"per_dataset": rows, "mean_rho": summary, "n_datasets": len(rows),
           "wins": wins,
           "lift_discoveronly_vs_default": summary["discover_only"] - summary["current_default"],
           "lift_proposal_vs_default": summary["balanced_proposal"] - summary["current_default"]}
    json.dump(out, open("cluster_results/preset_tune_results.json", "w"), indent=2)

    print(f"datasets: {len(rows)} (5 BC + 3 East)")
    print(f"{'dataset':16s} " + " ".join(f"{c:>16s}" for c in CANDIDATES))
    for r in rows:
        print(f"{r['dataset']:16s} " + " ".join(f"{r[c]:>+16.3f}" for c in CANDIDATES))
    print("-" * 70)
    print(f"{'MEAN rho':16s} " + " ".join(f"{summary[c]:>+16.3f}" for c in CANDIDATES))
    print(f"{'wins (of '+str(len(rows))+')':16s} " + " ".join(f"{wins[c]:>16d}" for c in CANDIDATES))
    print(f"\ndiscover_only beats current_default by mean rho "
          f"{out['lift_discoveronly_vs_default']:+.3f}; "
          f"balanced_proposal by {out['lift_proposal_vs_default']:+.3f}")
    print("wrote cluster_results/preset_tune_results.json")


if __name__ == "__main__":
    main()
