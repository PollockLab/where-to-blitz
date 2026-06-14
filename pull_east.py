"""Generalization pull: same backtest method, Eastern-Canada bbox (out-of-BC)."""
import pull_inat_backtest as p
import pandas as pd

p.BC = dict(swlat=42.0, swlng=-95.0, nelat=53.0, nelng=-57.0)  # ON+QC+Maritimes
for tx in ["Aves", "Insecta", "Mammalia"]:
    tr = p.pull_window(tx, p.SEASON[0], p.SPLIT)
    te = p.pull_window(tx, p.SPLIT, p.SEASON[1])
    df = pd.concat([tr, te], ignore_index=True).drop_duplicates("id")
    out = f"cluster_results/inat_east_{tx}.csv"
    df.to_csv(out, index=False)
    print(f"{tx} train={len(tr)} test={len(te)} total={len(df)} -> {out}", flush=True)
print("DONE_EAST", flush=True)
