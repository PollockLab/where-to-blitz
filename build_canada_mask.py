"""Tag grid cells that fall outside Canada, for the app's always-on 'Canada only' view.

The 0.25-deg grid is identical across taxa, so we read one webapp_data_*.json and classify each
cell centre by NEAREST COUNTRY: a cell is hidden only if its centre is closer to a FOREIGN country
than to Canada (Natural Earth 1:50m boundaries, simplified to ~0.01 deg and committed alongside as
na_boundaries.geojson). The foreign set is the United States and Greenland:
  - US removes the deep-US band AND the coastal-Alaska panhandle west of BC (issue #72);
  - Greenland (GL) removes the western Greenland cells that the Weiss land mask admits — they are
    nearer Canada than the US, so the US-only test left ~1/4 of Greenland coloured (issue #86).
The nearest-country test is symmetric and never hides Canadian coastal-water cells (the previous
lat<49.5 + coarse-1:110m heuristic over-hid those, and missed Alaska entirely). Canadian Arctic
islands (Ellesmere, Devon, Baffin) sit inside the CA polygon, so contains() keeps them instantly;
only the non-interior cells pay the distance computation. The output file keeps its us_cells.json
name (the app fetches it by that path); its contents are all cells hidden from the Canada-only view.
Keys match the app's gekey: lat.toFixed(3)+','+lon.toFixed(3).
"""
import json, glob, os
from shapely.geometry import shape, Point
from shapely.prepared import prep

HERE = "cluster_results/ca"
bd = json.load(open(os.path.join(HERE, "na_boundaries.geojson")))
geoms = {f["properties"]["country"]: shape(f["geometry"]) for f in bd["features"]}
CA = geoms["CA"]
CA_prep = prep(CA)
FOREIGN = [geoms["US"], geoms["GL"]]       # hide cells nearer any of these than Canada (#72, #86)

src = next(f for f in sorted(glob.glob(f"{HERE}/webapp_data_*.json")) if "gettingeven" not in f)
d = json.load(open(src)); rows = d[next(k for k, v in d.items() if isinstance(v, list))]

hidden = []
for r in rows:
    lat, lon = r[0], r[1]
    p = Point(lon, lat)
    if CA_prep.contains(p):
        continue                          # interior Canada — always shown
    dca = CA.distance(p)
    if any(dca > f.distance(p) for f in FOREIGN):   # closer to a foreign country than to Canada — hide
        hidden.append(f"{lat:.3f},{lon:.3f}")
json.dump({"us_cells": hidden}, open(f"{HERE}/us_cells.json", "w"), separators=(",", ":"))
print(f"{len(hidden)} / {len(rows)} cells nearer a foreign country (US/Greenland) than Canada -> us_cells.json")
