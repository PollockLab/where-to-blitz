"""Authoritative confirmation of the conservation finding: does the validated
priority discover IUCN-THREATENED species? Same test as voi_conservation.py but
the weight is real IUCN Red List threat status (fetched token-free via GBIF),
not the range-restriction proxy. Run on vertebrates (good IUCN coverage).

GBIF endpoint (no auth, verified): /species/match -> usageKey ->
/species/{key}/iucnRedListCategory -> code (LC/NT/VU/EN/CR/...).
"""
import json
import os
import sys
import time

import pandas as pd
import requests
import voi_conservation as vc

CACHE = "cluster_results/iucn_cache.json"
# ordinal threat weight: threatened species weighted up (tests "find threatened")
THREAT = {"LC": 1, "NT": 2, "VU": 4, "EN": 8, "CR": 16, "EW": 16, "EX": 16,
          "DD": 1, None: 1, "NE": 1}


def load_cache():
    return json.load(open(CACHE)) if os.path.exists(CACHE) else {}


def iucn_code(name, cache):
    if name in cache:
        return cache[name]
    code = None
    try:
        k = requests.get("https://api.gbif.org/v1/species/match",
                         params={"name": name}, timeout=30).json().get("usageKey")
        if k:
            code = requests.get(f"https://api.gbif.org/v1/species/{k}/iucnRedListCategory",
                                timeout=30).json().get("code")
    except Exception:
        code = None
    cache[name] = code
    return code


def iucn_weights(df, cache):
    """taxon_id -> threat weight, via the species name."""
    d = df.dropna(subset=["taxon_id", "taxon_name"])
    pairs = d[["taxon_id", "taxon_name"]].drop_duplicates()
    w, cats = {}, {}
    for _, r in pairs.iterrows():
        code = iucn_code(r.taxon_name, cache)
        w[r.taxon_id] = THREAT.get(code, 1)
        cats[code] = cats.get(code, 0) + 1
    return w, cats


if __name__ == "__main__":
    files = sys.argv[1:] or ["cluster_results/inat_Amphibia.csv",
                             "cluster_results/inat_Reptilia.csv",
                             "cluster_results/inat_Mammalia.csv"]
    cache = load_cache()
    results = []
    for f in files:
        name = f.split("inat_")[-1].replace(".csv", "")
        df = pd.read_csv(f)
        t0 = time.time()
        w, cats = iucn_weights(df, cache)
        json.dump(cache, open(CACHE, "w"))
        n_threat = sum(v for k, v in cats.items() if k in ("VU", "EN", "CR"))
        r = vc.analyse_conservation(name, df, weights=w, tag="iucn")
        if not r:
            print(f"{name}: insufficient"); continue
        r["iucn_categories"] = cats
        results.append(r)
        pf = r["perm_p_floor"]; fp = lambda p: f"<{pf:.4f}" if p < pf else f"{p:.4f}"
        wd, dr = r["weighted_discovery"], r["discovery_rarity_vs_priority"]
        threatened = {k: v for k, v in cats.items() if k in ("NT", "VU", "EN", "CR")}
        print(f"\n=== {name} === ({time.time()-t0:.0f}s) categories={cats}")
        print(f"  threatened(NT+): {threatened}")
        print(f"  priority -> IUCN-threat-WEIGHTED discovery: rho={wd['spearman']:+.3f} p={fp(wd['perm_p'])}")
        print(f"  priority -> MEAN THREAT of discoveries:     rho={dr['spearman']:+.3f} p={fp(dr['perm_p'])}")
    json.dump(results, open("cluster_results/voi_iucn_results.json", "w"), indent=2)
    print(f"\nwrote cluster_results/voi_iucn_results.json ({len(results)} taxa)")
