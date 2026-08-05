"""Build cluster_results/ca_forestloss.tif — a Canada-wide per-0.05-deg
forest-loss FRACTION raster from Hansen Global Forest Change (GFC-2023-v1.11),
mirroring the BC bc_forestloss.tif layer that the urgency axis consumes.

Method: for each Hansen 10x10-deg lossyear tile covering Canada, read it at full
30 m resolution in horizontal strips, binarize (lossyear>0 -> 1), and average-pool
to 0.05 deg, accumulating a national mosaic. Output is a single GTiff covering the
Canada BBOX at 0.05 deg, values in [0,1] = fraction of 30 m pixels that lost forest.

This is the REAL urgency layer. It is intentionally NOT run inside the MVP
build_fullgrid_ca.py because over ~40 tiles at 30 m it is bandwidth/CPU heavy
(~15-40 min on a laptop over /vsicurl). Run it once, then build_fullgrid_ca.py
auto-detects ca_forestloss.tif and flips urgency from DEFERRED to REAL.

Usage:  .venv/bin/python build_hansen_ca.py
"""
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

BBOX = (-141.0, 41.0, -52.0, 84.0)   # minlon,minlat,maxlon,maxlat
OUT = "cluster_results/ca_forestloss.tif"
OUTRES = 0.05
GFC = ("https://storage.googleapis.com/earthenginepartners-hansen/"
       "GFC-2023-v1.11/Hansen_GFC-2023-v1.11_lossyear_{lat:02d}N_{lon:03d}W.tif")

# Hansen tiles are named by their TOP-LEFT corner. Canada land: lon -140..-50, lat 40..80.
LAT_TOPS = [50, 60, 70, 80]              # each covers [top-10, top]
LON_LEFTS = list(range(140, 40, -10))    # 140W..50W, each covers [-left, -left+10]

# Output grid aligned to BBOX at 0.05 deg.
W = round((BBOX[2] - BBOX[0]) / OUTRES)
Hh = round((BBOX[3] - BBOX[1]) / OUTRES)
mosaic = np.full((Hh, W), np.nan, np.float32)
out_transform = from_origin(BBOX[0], BBOX[3], OUTRES, OUTRES)

PIX = 0.00025                            # Hansen pixel size (deg, ~30 m)
POOL = round(OUTRES / PIX)          # 30 m pixels per 0.05-deg cell (=200)


def place(sub_frac, lon_left, lat_top):
    """Write a tile's 0.05-deg fraction block into the national mosaic by geography."""
    # tile spans lon [lon_left_deg, lon_left_deg+10], lat [lat_top-10, lat_top]
    lon0 = -lon_left
    col0 = round((lon0 - BBOX[0]) / OUTRES)
    row0 = round((BBOX[3] - lat_top) / OUTRES)
    r1, c1 = row0 + sub_frac.shape[0], col0 + sub_frac.shape[1]
    # clip to mosaic bounds
    rr0, cc0 = max(0, row0), max(0, col0)
    rr1, cc1 = min(Hh, r1), min(W, c1)
    if rr1 <= rr0 or cc1 <= cc0:
        return
    sr0, sc0 = rr0 - row0, cc0 - col0
    block = sub_frac[sr0:sr0 + (rr1 - rr0), sc0:sc0 + (cc1 - cc0)]
    dst = mosaic[rr0:rr1, cc0:cc1]
    m = np.isnan(dst)
    dst[m] = block[m]
    dst[~m] = np.nanmax(np.stack([dst[~m], block[~m]]), 0)


for lat_top in LAT_TOPS:
    for lon_left in LON_LEFTS:
        url = "/vsicurl/" + GFC.format(lat=lat_top, lon=lon_left)
        try:
            with rasterio.open(url) as ds:
                Hn, Wn = ds.height, ds.width
                out_h, out_w = Hn // POOL, Wn // POOL
                frac = np.zeros((out_h, out_w), np.float32)
                # read in strips of POOL rows to bound memory
                for orow in range(out_h):
                    win = Window(0, orow * POOL, out_w * POOL, POOL)
                    a = ds.read(1, window=win)
                    b = (a > 0).astype(np.float32)
                    frac[orow] = b.reshape(POOL, out_w, POOL).mean((0, 2))
                place(frac, lon_left, lat_top)
            print(f"  {lat_top:02d}N_{lon_left:03d}W ok  mean_frac={np.nanmean(frac):.4f}", flush=True)
        except Exception as e:
            print(f"  {lat_top:02d}N_{lon_left:03d}W SKIP ({type(e).__name__}: {e})", flush=True)

prof = {"driver": "GTiff", "height": Hh, "width": W, "count": 1, "dtype": "float32",
            "crs": "EPSG:4326", "transform": out_transform, "nodata": np.nan,
            "compress": "deflate", "tiled": True}
with rasterio.open(OUT, "w", **prof) as dst:
    dst.write(np.nan_to_num(mosaic, nan=0.0), 1)
print(f"wrote {OUT}  ({Hh}x{W} @ {OUTRES} deg)  valid_mean={np.nanmean(mosaic):.4f}")
