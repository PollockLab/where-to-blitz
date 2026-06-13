"""Real staleness ("Revisit the Past") layer from public GBIF density tiles — no account.

For each 0.25-deg cell: compare all-time vs recent (last 5y) iNaturalist/GBIF occurrence density.
A cell well-sampled historically but quiet lately is a "revisit" priority. Honest, and genuinely
DIFFERENT from the discover axis (which is inverse all-density): staleness flags formerly-active,
now-overlooked cells, not never-sampled ones.

  staleness_raw = all>=MIN ? (1 - recent/all) * log1p(all)  : 0     (proportion stale, weighted by
                                                                     historical effort to suppress noise)
Then normalized 0-1.

Source: GBIF maps v2 density MVT tiles (api.gbif.org/v2/map/occurrence/density), country=CA,
EPSG:4326, squareSize=8. Output: cluster_results/ca/ca_staleness.csv (gi,gj,lat,lon,all,recent,staleness_norm)
"""
import urllib.request, csv, math
import mapbox_vector_tile as mvt
from collections import defaultdict

RES = 0.25
Z = 3
EXTENT = 4096
COLS, ROWS = 2 ** (Z + 1), 2 ** Z          # EPSG:4326 tile grid
TLON, TLAT = 360.0 / COLS, 180.0 / ROWS    # tile span (deg)
RECENT = "&year=2021,2026"
# Canada land tiles at z3: x in 1..5 (lon -157.5..-45), y in 0..2 (lat 90..22.5)
TILES = [(Z, x, y) for x in range(1, 6) for y in range(0, 3)]


def fetch_bins(z, x, y, extra=""):
    url = (f"https://api.gbif.org/v2/map/occurrence/density/{z}/{x}/{y}.mvt"
           f"?country=CA&bin=square&squareSize=8&srs=EPSG:4326{extra}")
    for a in range(4):
        try:
            d = urllib.request.urlopen(url, timeout=40).read()
            break
        except Exception:
            d = b""
    if not d:
        return []
    lon_left, lat_top = -180 + x * TLON, 90 - y * TLAT
    out = []
    for layer in mvt.decode(d).values():
        for f in layer.get("features", []):
            ring = f["geometry"]["coordinates"][0]
            px = sum(p[0] for p in ring) / len(ring)
            py = sum(p[1] for p in ring) / len(ring)
            # MVT y is up from tile bottom (0..extent); top of tile = extent
            lon = lon_left + (px / EXTENT) * TLON
            lat = lat_top - ((EXTENT - py) / EXTENT) * TLAT
            out.append((lat, lon, f["properties"].get("total", 0)))
    return out


def grid(extra=""):
    g = defaultdict(int)
    for (z, x, y) in TILES:
        for lat, lon, n in fetch_bins(z, x, y, extra):
            g[(int(lat // RES), int(lon // RES))] += n
    return g


print("fetching all-time density tiles…")
allg = grid()
print(f"  all-time: {len(allg)} cells, {sum(allg.values()):,} records")
print("fetching recent (2021-2026) density tiles…")
recg = grid(RECENT)
print(f"  recent: {len(recg)} cells, {sum(recg.values()):,} records")

MIN = 20   # need a real historical footprint before calling a cell "stale"
raw = {}
for k, a in allg.items():
    if a >= MIN:
        r = recg.get(k, 0)
        raw[k] = (1 - min(r, a) / a) * math.log1p(a)
hi = max(raw.values()) if raw else 1.0

import json
d = json.load(open("cluster_results/ca/webapp_data_All_biodiversity.json"))
rows = next(v for v in d.values() if isinstance(v, list))
with open("cluster_results/ca/ca_staleness.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["gi", "gj", "lat", "lon", "all", "recent", "staleness_norm"])
    nz = 0
    for rr in rows:
        k = (int(rr[0] // RES), int(rr[1] // RES))
        v = raw.get(k, 0.0) / hi
        if v:
            nz += 1
        w.writerow([k[0], k[1], rr[0], rr[1], allg.get(k, 0), recg.get(k, 0), f"{v:.5f}"])
print(f"webapp cells with staleness: {nz}/{len(rows)} ({100*nz/len(rows):.0f}%)")
print("wrote cluster_results/ca/ca_staleness.csv")
