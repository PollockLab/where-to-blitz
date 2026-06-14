"""The identification backlog as a candidate priority axis the official tool lacks.

The binding constraint on iNaturalist is not photos, it's expert IDs: ~75% of
identifications come from the top ~1% of users (Oxford BioScience 2023). So a
photo only "counts" (reaches Research Grade) if an identifier verifies it. This
measures, per 0.25-deg cell, how badly verifiable records pile up unverified —
and, crucially, whether that backlog is a DISTINCT signal from under-sampling
(if it just mirrors density, it adds nothing new).

Per cell, from the needs-ID pull (pull_inat_idlatency.py) + the research-grade
pull (pull_inat_backtest.py):
  - n_needsid, n_research, backlog_frac = needsid / (needsid + research)
  - frac_unengaged = share of needs-ID records with <=1 identification (nobody
    has weighed in) — cap-robust, doesn't depend on the research/needsid ratio.
  - median_wait_days = REF_DATE - created_at over needs-ID records.

Distinctness test: Spearman(backlog_frac, app_discover) and
Spearman(backlog_frac, density). Both near zero => the backlog is a genuinely
new axis, not a restatement of under-sampling or busy-ness.

Honest caveat: both pulls are page-capped (~6400/taxon/window), so in the
densest cells both sides saturate and backlog_frac is biased there; frac_unengaged
and median_wait are the robust headline, backlog_frac is directional.
"""
import sys, json, glob
import numpy as np
import pandas as pd
import voi_backtest as vb
import backtest_appscore as bas

RES = vb.RES
REF_DATE = pd.Timestamp("2026-06-14", tz="UTC")     # pull date; fixed for determinism
MIN_CELL = 5                                          # min records to score a cell


def grid(df):
    df = df.dropna(subset=["lat", "lon"]).copy()
    df["gi"] = np.floor(df.lat / RES).astype(int)
    df["gj"] = np.floor(df.lon / RES).astype(int)
    return df


def analyse(name):
    nid = f"cluster_results/needsid_{name}.csv"
    rg = f"cluster_results/inat_{name}.csv"
    ni = grid(pd.read_csv(nid))
    rs = grid(pd.read_csv(rg))
    if len(ni) == 0 or len(rs) == 0:
        return None
    # waiting time + engagement on the needs-ID pile
    ni["created"] = pd.to_datetime(ni["created_at"], errors="coerce", utc=True)
    ni["wait_days"] = (REF_DATE - ni["created"]).dt.total_seconds() / 86400.0
    ni["unengaged"] = ni["ident_count"].fillna(0) <= 1

    n_needsid = ni.groupby(["gi", "gj"]).size().rename("n_needsid")
    n_research = rs.groupby(["gi", "gj"]).size().rename("n_research")
    wait = ni.groupby(["gi", "gj"]).wait_days.median().rename("median_wait_days")
    uneng = ni.groupby(["gi", "gj"]).unengaged.mean().rename("frac_unengaged")
    cells = pd.concat([n_needsid, n_research, wait, uneng], axis=1).fillna(
        {"n_needsid": 0, "n_research": 0})
    cells["n_total"] = cells.n_needsid + cells.n_research
    cells["backlog_frac"] = cells.n_needsid / cells.n_total.where(cells.n_total > 0)

    scored = cells[cells.n_total >= MIN_CELL].copy()

    # distinctness: join the app's discover axis + a density proxy, by (gi,gj)
    app = bas.load_app_axes(bas.GROUP[name]) if name in bas.GROUP else {}
    disc, dens = [], []
    for (gi, gj), row in scored.iterrows():
        a = app.get((gi, gj))
        disc.append(a["discover"] if a else np.nan)
        dens.append(row.n_total)                      # local record volume = busy-ness
    scored["app_discover"] = disc
    scored["density"] = dens

    def sp(a, b):
        m = (~pd.isna(a)) & (~pd.isna(b))
        return vb.spearman(a[m].values, b[m].values) if m.sum() >= 8 else float("nan")

    out = {
        "taxon": name,
        "n_needsid_obs": int(len(ni)),
        "n_research_obs": int(len(rs)),
        "n_cells_scored": int(len(scored)),
        "overall_backlog_frac": float(len(ni) / (len(ni) + len(rs))),
        "median_wait_days_all": float(np.nanmedian(ni.wait_days)),
        "frac_unengaged_all": float(ni.unengaged.mean()),
        "cell_backlog_frac_median": float(scored.backlog_frac.median()),
        # distinctness (near 0 => new axis, not a density/under-sampling restatement)
        "spearman_backlog_vs_discover": sp(scored.backlog_frac, scored.app_discover),
        "spearman_backlog_vs_density": sp(scored.backlog_frac, scored.density),
    }
    return out


if __name__ == "__main__":
    taxa = sys.argv[1:] or [f.split("needsid_")[-1].replace(".csv", "")
                            for f in sorted(glob.glob("cluster_results/needsid_*.csv"))]
    results = []
    for name in taxa:
        r = analyse(name)
        if r is None:
            print(f"{name}: insufficient data"); continue
        results.append(r)
        print(f"\n=== {name} ===  needs_id={r['n_needsid_obs']} research={r['n_research_obs']} "
              f"cells={r['n_cells_scored']}")
        print(f"  backlog: {r['overall_backlog_frac']*100:.0f}% of verifiable records await ID | "
              f"median wait {r['median_wait_days_all']:.0f}d | "
              f"{r['frac_unengaged_all']*100:.0f}% have <=1 ID (nobody engaged)")
        print(f"  DISTINCTNESS rho(backlog, under-sampling)={r['spearman_backlog_vs_discover']:+.3f} "
              f"rho(backlog, density)={r['spearman_backlog_vs_density']:+.3f}  (near 0 => new axis)")
    json.dump(results, open("cluster_results/idlatency_results.json", "w"), indent=2)
    print(f"\nwrote cluster_results/idlatency_results.json ({len(results)} taxa)")
