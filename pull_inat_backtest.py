"""Pull research-grade iNaturalist observations (project 228908) for the VOI
backtest, cached to CSV so the analysis is reproducible without re-hitting the API.

Pulled per time-window (train: <SPLIT, test: >=SPLIT) with a per-window cap, so
both sides of the temporal split are represented even when we cap a large taxon
(an id-desc sample would skew toward later-observed records). Region: British
Columbia bbox (the 2025 BTG pilot concentrated here). Saves incrementally.
"""
import sys
import time

import pandas as pd
import requests

INAT = "https://api.inaturalist.org/v1/observations"
PROJECT = 228908
BC = {"swlat": 48.3, "swlng": -139.1, "nelat": 60.0, "nelng": -114.0}
SPLIT = "2025-07-01"
SEASON = ("2025-04-01", "2025-09-30")
PER_PAGE = 200
SLEEP = 0.5
CAP_PAGES = 32                                # ~6400 obs per window per taxon


def pull_window(iconic, d1, d2, cap=CAP_PAGES):
    params = dict(project_id=PROJECT, quality_grade="research", iconic_taxa=iconic,
                  d1=d1, d2=d2, per_page=PER_PAGE, order_by="id", order="desc", **BC)
    rows, id_below, pages = [], None, 0
    while pages < cap:
        p = dict(params)
        if id_below:
            p["id_below"] = id_below
        r = requests.get(INAT, params=p, timeout=60)
        r.raise_for_status()
        res = r.json().get("results", [])
        if not res:
            break
        for o in res:
            g = o.get("geojson"); t = o.get("taxon") or {}
            if not g or not o.get("observed_on"):
                continue
            rows.append({"id": o["id"], "lon": g["coordinates"][0], "lat": g["coordinates"][1],
                             "observed_on": o["observed_on"], "taxon_id": t.get("id"),
                             "taxon_name": t.get("name"), "rank": t.get("rank")})
        id_below = res[-1]["id"]; pages += 1
        if len(res) < PER_PAGE:
            break
        time.sleep(SLEEP)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    # small taxa first so real data lands fast for pipeline validation
    taxa = sys.argv[1:] or ["Amphibia", "Reptilia", "Mammalia", "Aves", "Insecta"]
    for tx in taxa:
        t0 = time.time()
        tr = pull_window(tx, SEASON[0], SPLIT)
        te = pull_window(tx, SPLIT, SEASON[1])
        df = pd.concat([tr, te], ignore_index=True).drop_duplicates("id")
        out = f"cluster_results/inat_{tx}.csv"
        df.to_csv(out, index=False)
        print(f"{tx:12s} train={len(tr):>5} test={len(te):>5} total={len(df):>5} -> {out} ({time.time()-t0:.0f}s)",
              flush=True)
