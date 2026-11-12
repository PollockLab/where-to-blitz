"""Degree-days below 0 C and climate moisture deficit for Canada from CHELSA V2.1 monthly climatologies.

The repo's CHELSA clip (clip_chelsa_ca.py) holds only bio1, bio4 and bio12, none of which is either
variable, so this reads the 12 monthly mean temperatures (tas) and 12 monthly climate moisture
indices (cmi = precipitation minus Penman PET, kg m-2 per month), 1981-2010, and derives

    DD<0  sum over months of days_in_month * max(0, -tas)   (C-days; monthly means, so it misses
          frost days in months whose mean is above 0, the usual monthly-normal approximation)
    CMD   sum over months of max(0, -cmi)                   (mm; ClimateNA's form, Penman PET
          instead of ClimateNA's Hargreaves reference evaporation)

CHELSA files are global strip-per-row GeoTIFFs with no overviews, so only every STEP-th row and
column over the Canada box is read (30 arc-sec pixels, so about 2.8 km north-south). Output: an
npz with the lon/lat of each sampled pixel and both variables, NaN where CHELSA has no data.

    uv run python research/fetch_chelsa_ddcmd.py cluster_results/lightup/chelsa_ddcmd_ca.npz
"""
import calendar
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import rasterio
from rasterio.windows import Window, from_bounds

BASE = "/vsicurl/https://os.zhdk.cloud.switch.ch/chelsav2/GLOBAL/climatologies/1981-2010"
URL = {"tas": BASE + "/tas/CHELSA_tas_{:02d}_1981-2010_V.2.1.tif",
       "cmi": BASE + "/cmi/CHELSA_cmi_{:02d}_1981-2010_V.2.1.tif"}
BBOX = (-141.0, 41.0, -52.0, 84.0)  # as clip_chelsa_ca.py
STEP = 3
DAYS = [calendar.monthrange(2001, m)[1] for m in range(1, 13)]


def read_layer(url):
    with rasterio.open(url) as ds:
        w = from_bounds(*BBOX, ds.transform).round_offsets().round_lengths()
        rows = range(int(w.row_off), int(w.row_off + w.height), STEP)
        a = np.stack([ds.read(1, window=Window(w.col_off, r, w.width, 1))[0, ::STEP] for r in rows])
        bad = a == ds.nodata if ds.nodata is not None else np.zeros(a.shape, bool)
        a = np.where(bad, np.nan, a * ds.scales[0] + ds.offsets[0]).astype(np.float32)
        lon = ds.transform.c + (w.col_off + np.arange(0, w.width, STEP) + 0.5) * ds.transform.a
        lat = ds.transform.f + (np.asarray(rows) + 0.5) * ds.transform.e
    print("read", url.rsplit("/", 1)[1], a.shape, flush=True)
    return a, lon, lat


def main(out):
    jobs = [URL[v].format(m) for v in ("tas", "cmi") for m in range(1, 13)]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(read_layer, jobs))
    tas, cmi = np.stack([r[0] for r in res[:12]]), np.stack([r[0] for r in res[12:]])
    dd0 = (np.asarray(DAYS, np.float32)[:, None, None] * np.maximum(0, -tas)).sum(0)
    cmd = np.maximum(0, -cmi).sum(0)
    dd0[np.isnan(tas).any(0)] = np.nan
    cmd[np.isnan(cmi).any(0)] = np.nan
    lon, lat = np.meshgrid(res[0][1], res[0][2])
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    np.savez_compressed(out, lon=lon.astype(np.float32), lat=lat.astype(np.float32), dd0=dd0, cmd=cmd)
    print("wrote", out, dd0.shape, flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
