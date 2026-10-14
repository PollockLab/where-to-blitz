"""Crosswalk count gate: listed names whose record count changes more than 10x between two at-risk downloads.

Reads the committed crosswalk (cluster_results/ca/atrisk_species_keys.csv: name, col_key, backbone_key, ...)
and two finished GBIF SQL downloads of the at-risk query, one keyed on the GBIF Backbone and one on Catalogue
of Life XR. Per listed name it sums the Canadian records of its Backbone key and of its COL key, and writes
every name whose count moves by more than FACTOR in either direction (or from or to zero) to
cluster_results/ca/atrisk_taxonomy_count_changes.csv, sorted by the size of the change. A row is either a
real taxonomy change (a split, a lump, a genus move) or a wrong match; the table is for reading, not a stop.

Usage:
  .venv/bin/python compare_atrisk_taxonomy.py BACKBONE_DOWNLOAD_KEY COL_DOWNLOAD_KEY
"""

import argparse
import csv
import io
import math
import urllib.request
import zipfile
from collections import defaultdict

from build_atrisk_layer import CROSSWALK, GBIF, OUT_DIR, STATUS, gget

FACTOR = 10
OUT = f"{OUT_DIR}/atrisk_taxonomy_count_changes.csv"
COLS = [
    "name",
    "status",
    "in_layer",
    "backbone_key",
    "backbone_name",
    "backbone_records",
    "col_key",
    "col_name",
    "col_matchType",
    "col_records",
    "ratio",
]


def download_totals(key):
    """Finished GBIF SQL download -> {speciesKey: records}."""
    with urllib.request.urlopen(f"{GBIF}/occurrence/download/request/{key}.zip", timeout=600) as r:
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        member = next(n for n in z.namelist() if not n.endswith("/"))
        with z.open(member) as f:
            tot = defaultdict(int)
            for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t"):
                tot[r["specieskey"]] += int(r["n"])
            return tot


def count_changes(rows, bb_totals, col_totals, factor=FACTOR):
    """Crosswalk dict rows + per-key totals -> rows whose COL/Backbone record ratio is > factor or < 1/factor
    (ratio inf when the Backbone count is 0). Names with no key on either side, or 0 on both, are skipped."""
    out = []
    for r in rows:
        if not (r["col_key"] or r["backbone_key"]):
            continue
        b, c = bb_totals.get(r["backbone_key"], 0), col_totals.get(r["col_key"], 0)
        if not (b or c):
            continue
        ratio = c / b if b else math.inf
        if ratio > factor or ratio < 1 / factor:
            out.append(
                {
                    "name": r["name"],
                    "status": STATUS[int(r["status_weight"])],
                    "in_layer": bool(r["col_key"]),
                    "backbone_key": r["backbone_key"],
                    "backbone_records": b,
                    "col_key": r["col_key"],
                    "col_name": r["col_accepted_name"],
                    "col_matchType": r["col_matchType"],
                    "col_records": c,
                    "ratio": ratio,
                }
            )
    return sorted(out, key=lambda d: -abs(math.log(d["ratio"])) if d["ratio"] else -math.inf)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("backbone_key", help="GBIF download key of the Backbone-keyed at-risk query")
    ap.add_argument("col_key", help="GBIF download key of the COL XR-keyed at-risk query")
    a = ap.parse_args()
    with open(CROSSWALK) as f:
        rows = list(csv.DictReader(f))
    changes = count_changes(rows, download_totals(a.backbone_key), download_totals(a.col_key))
    for d in changes:
        bk = d["backbone_key"]
        d["backbone_name"] = gget(f"{GBIF}/species/{bk}?", {}).get("canonicalName", "") if bk else ""
        d["ratio"] = "inf" if d["ratio"] == math.inf else f"{d['ratio']:.3g}"
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(changes)
    print(f"{len(changes)} names change more than {FACTOR}x; wrote {OUT}")


if __name__ == "__main__":
    main()
