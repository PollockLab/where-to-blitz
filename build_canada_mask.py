"""Tag grid cells that fall outside Canada, for the app's optional 'Canada only' view.

The 0.25-deg grid is identical across taxa, so we read one webapp_data_*.json, test each
cell centre against a simplified Canada boundary (Natural Earth 1:110m, committed alongside),
and write the out-of-Canada cell keys to us_cells.json. Approximate at the ~25 km border scale;
it cleanly removes the deep-US band (the 49N false-gap seam) without needing the heavy rasters.
Keys match the app's gekey: lat.toFixed(3)+','+lon.toFixed(3).
"""
import json, glob, os
from shapely.geometry import shape, Point
from shapely.prepared import prep

HERE = "cluster_results/ca"
geo = json.load(open(os.path.join(HERE, "canada_boundary.geojson")))
geom = geo.get("geometry") or geo["features"][0]["geometry"]
canada = prep(shape(geom))

src = next(f for f in sorted(glob.glob(f"{HERE}/webapp_data_*.json")) if "gettingeven" not in f)
d = json.load(open(src)); rows = d[next(k for k, v in d.items() if isinstance(v, list))]

# Only flag below 49.5N. The grid's genuine US cells are all south of there (Alaska is west
# of the -141 bbox edge), while the simplified boundary drops Canadian Arctic-archipelago and
# coastal islands (Baffin, Ellesmere, Haida Gwaii) -- capping the test keeps those as Canada.
US_LAT_MAX = 49.5
us = []
for r in rows:
    lat, lon = r[0], r[1]
    if lat < US_LAT_MAX and not canada.contains(Point(lon, lat)):
        us.append(f"{lat:.3f},{lon:.3f}")
json.dump({"us_cells": us}, open(f"{HERE}/us_cells.json", "w"), separators=(",", ":"))
print(f"{len(us)} / {len(rows)} cells outside Canada -> us_cells.json")
