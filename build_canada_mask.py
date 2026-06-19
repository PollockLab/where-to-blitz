"""Tag grid cells that fall on the US side of the border, for the app's 'Canada only' view.

The 0.25-deg grid is identical across taxa, so we read one webapp_data_*.json and classify each
cell centre by NEAREST COUNTRY: a cell is hidden only if its centre is closer to the United States
than to Canada (Natural Earth 1:50m boundaries, simplified to ~0.01 deg and committed alongside as
na_boundaries.geojson). This is symmetric — it removes the deep-US band AND the coastal-Alaska
panhandle west of BC (issue #72) without ever hiding Canadian coastal-water cells (the previous
lat<49.5 + coarse-1:110m heuristic over-hid those, and missed Alaska entirely). Cells inside Canada
are kept instantly via a prepared contains() test; only the ~6k non-interior cells pay the
distance computation. Keys match the app's gekey: lat.toFixed(3)+','+lon.toFixed(3).
"""
import json, glob, os
from shapely.geometry import shape, Point
from shapely.prepared import prep

HERE = "cluster_results/ca"
bd = json.load(open(os.path.join(HERE, "na_boundaries.geojson")))
geoms = {f["properties"]["country"]: shape(f["geometry"]) for f in bd["features"]}
CA, US = geoms["CA"], geoms["US"]
CA_prep = prep(CA)

src = next(f for f in sorted(glob.glob(f"{HERE}/webapp_data_*.json")) if "gettingeven" not in f)
d = json.load(open(src)); rows = d[next(k for k, v in d.items() if isinstance(v, list))]

us = []
for r in rows:
    lat, lon = r[0], r[1]
    p = Point(lon, lat)
    if CA_prep.contains(p):
        continue                          # interior Canada — always shown
    if CA.distance(p) > US.distance(p):   # closer to the US than to Canada — hide
        us.append(f"{lat:.3f},{lon:.3f}")
json.dump({"us_cells": us}, open(f"{HERE}/us_cells.json", "w"), separators=(",", ":"))
print(f"{len(us)} / {len(rows)} cells nearer the US than Canada -> us_cells.json")
