"""Check the range-clipped at-risk layer against research/GATE-range-clip.md.

Reads the downloaded GBIF SQL zip (the one build_atrisk_layer.py --fetch used), the committed listing table,
range polygons, crosswalk, clip table and layer, and a copy of the live layer for the reported comparison.

  .venv/bin/python research/range_clip_gate.py DOWNLOAD.zip LIVE_LAYER.csv
"""

import argparse
import csv
import zipfile

import _rootpath  # puts the repo root on sys.path
import numpy as np

import atrisk_ranges as ar
import build_atrisk_layer as bal

C1 = ["121"]  # laingi
C2 = ["336", "179", "500", "501"]  # fannini, pealei, painted turtle Pacific Coast, Intermountain-Rocky Mountain
C4 = {"piping plover": ["454", "483"], "spotted owl": ["507"], "greater sage-grouse": ["204"]}
LONG_POINT = (170, -322)


def load_layer(path):
    with open(path) as f:
        return {(int(r["gi"]), int(r["gj"])): float(r["score_raw"]) for r in csv.DictReader(f)}


def spearman(a, b):
    ra, rb = (np.argsort(np.argsort(v, kind="stable")).astype(float) for v in (a, b))
    for r, v in ((ra, a), (rb, b)):  # average ranks over ties
        for u in np.unique(v):
            m = v == u
            r[m] = r[m].mean()
    return float(np.corrcoef(ra, rb)[0, 1])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("download")
    ap.add_argument("live")
    a = ap.parse_args()
    with zipfile.ZipFile(a.download) as z, z.open(next(n for n in z.namelist() if not n.endswith("/"))) as f:
        cols = ar.read_columns(f)
    listings = ar.read_listings()
    by_id = {r["listing_id"]: r for r in listings}
    entries = ar.listing_entries(listings, bal.read_crosswalk(), ar.load_range_geoms())
    ratio = round(bal.RES / bal.FINE)

    def cell(i):
        return int(cols["yi"][i] // ratio), int(cols["xi"][i] // ratio)

    acc_of = {}
    for e, idx, acc, inside in ar.accepted_rows(cols, entries, ar.transformer(), bal.FINE):
        acc_of[e["id"]] = (idx, acc, inside)

    print("C1/C2: accepted fine cells outside the listing's buffered range (subspecies-identified records)")
    for lid in C1 + C2:
        idx, acc, inside = acc_of[lid]
        out = acc[~inside]
        before = {cell(i) for i in idx}
        after = {cell(i) for i in acc}
        print(
            f"  {lid} {by_id[lid]['common_name']}: outside={len(out)} rows ({int(cols['n'][out].sum())} records) "
            f"{'PASS' if not len(out) else 'FAIL'}; 0.25 deg cells {len(before)} -> {len(after)}, "
            f"records {int(cols['n'][idx].sum())} -> {int(cols['n'][acc].sum())}"
        )
        for i in out[:10]:
            print(f"    outside: {(cols['yi'][i] + 0.5) * bal.FINE:.3f},{(cols['xi'][i] + 0.5) * bal.FINE:.3f}")

    layer = load_layer(bal.OUT_CSV)
    rank = 1 + sum(v > layer.get(LONG_POINT, -1) for v in layer.values())
    top = sorted(layer.items(), key=lambda kv: -kv[1])[:5]
    print(f"C3: Long Point {LONG_POINT} score {layer.get(LONG_POINT)} rank {rank} {'PASS' if rank <= 5 else 'FAIL'}")
    print(f"  top 5: {top}")

    with open(ar.CLIP_CSV) as f:
        clip = {r["listing_id"]: r for r in csv.DictReader(f)}
    for sp, ids in C4.items():
        kept = {i: clip[i]["records_kept"] for i in ids}
        ok = any(v and int(v) > 0 for v in kept.values())
        print(f"C4: {sp} records kept {kept} {'PASS' if ok else 'FAIL'}")

    rows = [r for r in clip.values() if r["records_species"] and int(r["records_species"]) > 0]
    loss = [r for r in rows if int(r["records_kept"]) < 0.1 * int(r["records_species"])]
    print(f"C5: listings losing >90% of records: {len(loss)} (hand review)")
    for r in sorted(loss, key=lambda r: int(r["records_kept"]) / int(r["records_species"])):
        print(
            f"  {r['listing_id']} {r['common_name']} [{r['range_source']}: {' '.join(by_id[r['listing_id']]['range_ids'])}] "
            f"kept {r['records_kept']}/{r['records_species']}"
        )

    live = load_layer(a.live)
    union = sorted(set(live) | set(layer))
    inter = sorted(set(live) & set(layer))
    u = spearman(np.array([live.get(k, 0) for k in union]), np.array([layer.get(k, 0) for k in union]))
    i = spearman(np.array([live[k] for k in inter]), np.array([layer[k] for k in inter]))
    print(f"Reported: cells live {len(live)} new {len(layer)} gained {len(set(layer) - set(live))} "
          f"lost {len(set(live) - set(layer))}; Spearman union(0) {u:.4f} intersection {i:.4f}")
    print(f"  live top {max(live.items(), key=lambda kv: kv[1])}, new top {max(layer.items(), key=lambda kv: kv[1])}")
    top100 = [{k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:100]} for d in (live, layer)]
    print(f"  top-100 overlap {len(top100[0] & top100[1])}")
    print("  20 listings with the largest record losses:")
    for r in sorted(rows, key=lambda r: int(r["records_kept"]) - int(r["records_species"]))[:20]:
        print(f"    {r['listing_id']} {r['common_name']} [{r['range_source']}] kept {r['records_kept']}/{r['records_species']}")


if __name__ == "__main__":
    main()
