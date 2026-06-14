"""Pull NEEDS-ID iNaturalist observations (the verification backlog) for the
ID-latency axis — the bottleneck the official tool doesn't surface.

Mirrors pull_inat_backtest.py (same BC bbox, season, project 228908) but
quality_grade=needs_id, and captures per-observation `created_at`,
`identifications_count`, and `num_identification_agreements` so we can measure,
per cell: what fraction of verifiable records are stuck unverified, and how long
they've waited. Pair with the research-grade pull (inat_<taxon>.csv) to get the
per-cell backlog fraction = needs_id / (needs_id + research).
"""
import sys, time, requests, pandas as pd

INAT = "https://api.inaturalist.org/v1/observations"
PROJECT = 228908
BC = dict(swlat=48.3, swlng=-139.1, nelat=60.0, nelng=-114.0)
SEASON = ("2025-04-01", "2025-09-30")
PER_PAGE = 200
SLEEP = 0.5
CAP_PAGES = 32


def pull(iconic, d1, d2, qgrade, cap=CAP_PAGES):
    params = dict(project_id=PROJECT, quality_grade=qgrade, iconic_taxa=iconic,
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
            g = o.get("geojson")
            if not g or not o.get("observed_on"):
                continue
            t = o.get("taxon") or {}
            rows.append(dict(
                id=o["id"], lon=g["coordinates"][0], lat=g["coordinates"][1],
                observed_on=o["observed_on"], created_at=o.get("created_at"),
                quality_grade=o.get("quality_grade"),
                ident_count=o.get("identifications_count"),
                agree=o.get("num_identification_agreements"),
                taxon_id=t.get("id"), rank=t.get("rank")))
        id_below = res[-1]["id"]; pages += 1
        if len(res) < PER_PAGE:
            break
        time.sleep(SLEEP)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    taxa = sys.argv[1:] or ["Amphibia", "Reptilia", "Mammalia", "Aves", "Insecta"]
    for tx in taxa:
        t0 = time.time()
        df = pull(tx, SEASON[0], SEASON[1], "needs_id")
        out = f"cluster_results/needsid_{tx}.csv"
        df.to_csv(out, index=False)
        print(f"{tx:12s} needs_id={len(df):>5} -> {out} ({time.time()-t0:.0f}s)", flush=True)
