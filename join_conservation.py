"""Join the COSEWIC at-risk-richness layer into the webapp conservation axis.

Sets row element index 3 (conservation) in every cluster_results/ca/webapp_data_<GROUP>.json to the
cell's normalized at-risk score from ca_atrisk_richness.csv (built by build_atrisk_layer.py). The
0.25-deg grid is identical across taxa, so the same all-taxa at-risk layer is applied to every group:
"where are Canada's COSEWIC/SARA species at risk" is a cross-taxon conservation signal. The app
fetches these JSONs at runtime, so patching them updates the live data (no index.html rebuild needed
for the data itself; labeling changes are separate).
"""
import json, csv, glob, os

RES = 0.25
norm = {}
for r in csv.DictReader(open("cluster_results/ca/ca_atrisk_richness.csv")):
    norm[(int(r["gi"]), int(r["gj"]))] = float(r["conservation_norm"])

files = [f for f in glob.glob("cluster_results/ca/webapp_data_*.json") if "gettingeven" not in f]
for f in files:
    d = json.load(open(f))
    key = next(k for k, v in d.items() if isinstance(v, list))
    rows = d[key]
    patched = 0
    for row in rows:
        k = (int(row[0] // RES), int(row[1] // RES))
        v = norm.get(k, 0.0)
        if v:
            patched += 1
        row[3] = round(v, 5)   # conservation = element index 3
    json.dump(d, open(f, "w"), separators=(",", ":"))
    print(f"{os.path.basename(f):42s} {patched}/{len(rows)} cells with conservation>0")
print(f"patched {len(files)} group files")
