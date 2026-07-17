"""#87 prototype — bake the cell grid to VECTOR PMTiles at two resolutions (25 km + 5 km).

Proves the tiling architecture W approved (vector PMTiles) can serve a 5 km Canada grid
(~950k cells) that the current whole-file-GeoJSON approach cannot (~520 MB across groups).

Tiers (each a square-polygon choropleth carrying a `gap` property = spatial gap 0..1):
  - 25 km: reuse the committed cluster_results/ca grid (discover axis = under-sampling rank).
  - 5 km : average-resample the public 1 km iNat density COG onto a 0.05-deg grid, gap =
           normalized inverse density (issue #89's "direct inverse", used here as the
           architecture fixture; the real per-taxon layer swaps in W's parquet later).

The 5 km grid is masked to the 25 km land cells so it matches the app's footprint.
Outputs vector PMTiles into ./density/ (served statically from Pages, like the raster ones).

Run:  .venv/bin/python build_vector_tiles.py
Needs: rasterio, numpy, tippecanoe on PATH.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

BBOX = (-141.0, 41.0, -52.0, 84.0)   # minlon, minlat, maxlon, maxlat
RES25, RES5 = 0.25, 0.05
COG = "/vsicurl/https://object-arbutus.cloud.computecanada.ca/bq-io/io/inat_canada_heatmaps/All_density_inat_1km.tif"
OUT = Path("density")
TMP = Path("cluster_results/_vtiles_tmp")
TMP.mkdir(parents=True, exist_ok=True)


def parent25(lat, lon):
    """0.25-deg grid index a point falls in (same convention as build_fullgrid_ca)."""
    return (np.round(lat / RES25 - 0.5).astype(int), np.round(lon / RES25 - 0.5).astype(int))


def square(lat, lon, half):
    return [[[lon - half, lat - half], [lon + half, lat - half],
             [lon + half, lat + half], [lon - half, lat + half], [lon - half, lat - half]]]


def write_geojsonseq(path, feats):
    with open(path, "w") as f:
        for ft in feats:
            f.write(json.dumps(ft, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------- 25 km tier (from committed grid)
rows = json.load(open("cluster_results/ca/webapp_data_All_biodiversity.json"))["All biodiversity"]
lat25 = np.array([r[0] for r in rows]); lon25 = np.array([r[1] for r in rows])
gap25 = np.array([r[2] for r in rows])                       # discover axis = under-sampling rank
keep25 = set(zip(*[a.tolist() for a in parent25(lat25, lon25)]))
f25 = [{"type": "Feature", "id": i,
        "properties": {"gap": round(float(gap25[i]), 3)},
        "geometry": {"type": "Polygon", "coordinates": square(lat25[i], lon25[i], RES25 / 2)}}
       for i in range(len(rows))]
write_geojsonseq(TMP / "cells25.geojsonl", f25)
print(f"25 km: {len(f25):,} cells")

# ---------------------------------------------------------------- 5 km tier (resampled from COG)
ncol = int(round((BBOX[2] - BBOX[0]) / RES5)); nrow = int(round((BBOX[3] - BBOX[1]) / RES5))
dst = np.full((nrow, ncol), np.nan, np.float32)
with rasterio.open(COG) as src:
    reproject(rasterio.band(src, 1), dst, src_transform=src.transform, src_crs=src.crs,
              dst_transform=from_origin(BBOX[0], BBOX[3], RES5, RES5), dst_crs="EPSG:4326",
              src_nodata=src.nodata, dst_nodata=np.nan, resampling=Resampling.average)
r, c = np.where(np.isfinite(dst))
lat5 = BBOX[3] - (r + 0.5) * RES5; lon5 = BBOX[0] + (c + 0.5) * RES5
# mask to the 25 km land footprint
gi, gj = parent25(lat5, lon5)
inland = np.fromiter(((int(a), int(b)) in keep25 for a, b in zip(gi, gj)), bool, len(gi))
lat5, lon5, dens5 = lat5[inland], lon5[inland], dst[r, c][inland]
dens0 = np.where(dens5 > 0, dens5, 0.0)
inv = 1.0 / (dens0 + 1e-3)                                    # #89 direct inverse
gap5 = (inv - inv.min()) / (inv.max() - inv.min())           # 0..1
f5 = [{"type": "Feature", "id": i,
       "properties": {"gap": round(float(gap5[i]), 3)},
       "geometry": {"type": "Polygon", "coordinates": square(lat5[i], lon5[i], RES5 / 2)}}
      for i in range(len(lat5))]
write_geojsonseq(TMP / "cells5.geojsonl", f5)
print(f"5 km : {len(f5):,} cells")

# ---------------------------------------------------------------- bake vector PMTiles
OUT.mkdir(exist_ok=True)
def tip(src, out, layer, zmin, zmax):
    subprocess.run(["tippecanoe", "-o", str(OUT / out), "-l", layer,
                    "-Z", str(zmin), "-z", str(zmax), "--no-tile-compression",
                    "--drop-densest-as-needed", "--extend-zooms-if-still-dropping",
                    "-f", str(TMP / src)], check=True)
    print(f"  wrote {out} ({(OUT / out).stat().st_size // 1024} KB)")

tip("cells25.geojsonl", "cells25.pmtiles", "cells25", 0, 6)
tip("cells5.geojsonl", "cells5.pmtiles", "cells5", 6, 11)
print("done")
