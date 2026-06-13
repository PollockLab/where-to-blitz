"""Per-cell conservation-concern layer for where-to-blitz ("Canada's Most Wanted", done properly).
Authoritative Canadian source, fully public, no account / SharePoint / parquet.

Species at risk: CAN-SAR (COSEWIC/SARA assessments, OSF DOI 10.17605/OSF.IO/E4A58, CC-BY) — the
Canadian authority (IUCN diverges). Occurrences: public GBIF occurrence search (no account; at-risk
species are rare so each is pageable). Per cell, the conservation score is the STATUS-WEIGHTED sum of
COSEWIC at-risk species whose georeferenced Canadian records fall in the cell:
Endangered=3, Threatened=2, Special Concern=1. This is authoritative status x real occurrences, NOT
the range-restriction proxy (which failed IUCN validation, rho=0.054).

Honesty: reflects ASSESSED species (CAN-SAR ~561 spp, snapshot ~2021); occurrence-based so absence
in a cell = "not recorded there", not "absent". Label in-app as COSEWIC at-risk richness.

Output: cluster_results/ca/ca_atrisk_richness.csv (gi,gj,lat,lon,n_species,score_raw,conservation_norm)
Usage : .venv/bin/python build_atrisk_layer.py
"""
import json, csv, urllib.request, urllib.parse, time
from collections import defaultdict

RES = 0.25
CANSAR = "/tmp/wtb/can-sar.csv"
WT = {"Endangered": 3, "Threatened": 2, "Special Concern": 1}
PER_SPECIES_CAP = 900   # 3 pages: enough to map a rare species cells; bounds runtime


def gget(url, params):
    for a in range(4):
        try:
            with urllib.request.urlopen(url + urllib.parse.urlencode(params), timeout=40) as r:
                return json.load(r)
        except Exception:
            time.sleep(1.2 * (a + 1))
    return {}


def taxon_key(name):
    m = gget("https://api.gbif.org/v1/species/match?", {"name": name})
    return m.get("usageKey") if m.get("matchType") not in (None, "NONE") else None


def species_cells(key):
    cells, off = set(), 0
    while off < PER_SPECIES_CAP:
        d = gget("https://api.gbif.org/v1/occurrence/search?",
                 {"country": "CA", "taxonKey": key, "hasCoordinate": "true",
                  "hasGeospatialIssue": "false", "limit": 300, "offset": off})
        res = d.get("results", [])
        if not res:
            break
        for o in res:
            la, lo = o.get("decimalLatitude"), o.get("decimalLongitude")
            if la is not None and lo is not None:
                cells.add((int(la // RES), int(lo // RES)))
        if len(res) < 300 or d.get("endOfRecords"):
            break
        off += 300
    return cells


# CAN-SAR -> {species: best (most-severe) status weight}
sev = {}
for r in csv.DictReader(open(CANSAR, encoding="utf-8", errors="replace")):
    s = (r.get("species") or "").strip()
    st = (r.get("cosewic_status") or "").strip()
    if s and st in WT:
        sev[s] = max(sev.get(s, 0), WT[st])
print(f"CAN-SAR COSEWIC at-risk species: {len(sev)}")

score = defaultdict(float)
nsp = defaultdict(int)
matched = miss = 0
for i, (name, w) in enumerate(sev.items()):
    k = taxon_key(name)
    if not k:
        miss += 1
        continue
    cells = species_cells(k)
    if cells:
        matched += 1
    for c in cells:
        score[c] += w
        nsp[c] += 1
    if (i + 1) % 25 == 0:
        print(f"  {i+1}/{len(sev)} species ({matched} with CA records, {miss} no GBIF match); {len(score)} cells")
print(f"done: {matched} species mapped; {len(score)} cells host >=1 at-risk species")

d = json.load(open("cluster_results/ca/webapp_data_All_biodiversity.json"))
rows = next(v for v in d.values() if isinstance(v, list))
raw = [score.get((int(r[0] // RES), int(r[1] // RES)), 0.0) for r in rows]
hi = max(raw) or 1.0
with open("cluster_results/ca/ca_atrisk_richness.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["gi", "gj", "lat", "lon", "n_species", "score_raw", "conservation_norm"])
    for r, v in zip(rows, raw):
        key = (int(r[0] // RES), int(r[1] // RES))
        w.writerow([key[0], key[1], r[0], r[1], nsp.get(key, 0), f"{v:.2f}", f"{v/hi:.5f}"])
nz = sum(1 for v in raw if v)
print(f"webapp cells with at-risk score: {nz}/{len(rows)} ({100*nz/len(rows):.0f}%); max score={hi:.1f}")
print("wrote cluster_results/ca/ca_atrisk_richness.csv")
