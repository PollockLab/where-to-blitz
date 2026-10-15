"""Bounded, resumable BC plant climate and land-cover sampling, never a species model."""

import argparse
import hashlib
import io
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import Window

from backtest_appscore import AppGrid

CLASSES = (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)
ROW_FORMAT = ["row", "col", "lat", "lon", "bio1", "bio4", "bio12"] + [f"cover_{n}" for n in CLASSES]
BASE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
CLIMATE_URL = "https://os.zhdk.cloud.switch.ch/chelsav2/GLOBAL/climatologies/1981-2010/bio/CHELSA_bio{}_1981-2010_V.2.1.tif"
BBOX = (-139.1, 48.3, -114.0, 60.0)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tile_name(lon, lat):
    """WorldCover's southwest corner, including correct negative-coordinate flooring."""
    if not np.isfinite([lon, lat]).all() or not -180 <= lon < 180 or not -90 <= lat < 90:
        raise ValueError("invalid WorldCover point")
    west, south = 3 * math.floor(lon / 3), 3 * math.floor(lat / 3)
    return f"ESA_WorldCover_10m_2021_v200_{'N' if south >= 0 else 'S'}{abs(south):02d}{'E' if west >= 0 else 'W'}{abs(west):03d}_Map.tif"


def cell_points(grid, key, side=15):
    """One centre per equal-area stratum, strictly inside the exact indexed LAEA cell."""
    if isinstance(side, bool) or not isinstance(side, int) or side < 1:
        raise ValueError("positive integer sampling side required")
    row, col = key
    if not 0 <= row < grid.lattice.nrow or not 0 <= col < grid.lattice.ncol:
        raise ValueError("cell outside lattice")
    offsets = (np.arange(side) + 0.5) / side
    xx, yy = np.meshgrid(
        grid.lattice.x0 + (col + offsets) * grid.lattice.res,
        grid.lattice.y1 - (row + offsets) * grid.lattice.res,
    )
    lon, lat = grid.inverse.transform(xx.ravel(), yy.ravel())
    return np.column_stack([lon, lat])


def climate_summary(values):
    """Reject masks/nonfinite and encoded CHELSA extrema lost by the legacy clip."""
    values = np.ma.asarray(values, dtype=float)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("expected samples by three climate bands")
    raw = values.filled(np.nan)
    # Legacy clips applied source scale/offset, but discarded nodata metadata.
    # Encoded 0/65535 and physically impossible values must not enter means.
    good = np.isfinite(raw) & ~np.ma.getmaskarray(values)
    good[:, 0] &= (raw[:, 0] > -100) & (raw[:, 0] < 70)
    good[:, 1] &= (raw[:, 1] > 0) & (raw[:, 1] < 6553.4)
    good[:, 2] &= (raw[:, 2] > 0) & (raw[:, 2] < 6553.4)
    counts = good.sum(axis=0).astype(int).tolist()
    return [float(raw[good[:, i], i].mean()) if counts[i] else None for i in range(3)], counts


def cover_summary(values):
    """Class fractions conditional on valid samples, with a separate completeness count."""
    values = np.ma.asarray(values).reshape(-1)
    raw = values.filled(0)
    good = ~np.ma.getmaskarray(values) & np.isin(raw, CLASSES)
    count = int(good.sum())
    return (
        [float(np.sum(raw[good] == code) / count) for code in CLASSES] if count else [None] * len(CLASSES)
    ), count


def sample_climate(ds, points):
    """One bounded local raster read, explicitly masking points outside its extent."""
    if ds.count != 3 or ds.crs is None:
        raise ValueError("expected georeferenced three-band BIO1/4/12 climate raster")
    points = np.asarray(points, dtype=float)
    if ds.crs != rasterio.crs.CRS.from_epsg(4326):
        x, y = Transformer.from_crs(4326, ds.crs, always_xy=True).transform(points[:, 0], points[:, 1])
        points = np.column_stack([x, y])
    rr, cc = rasterio.transform.rowcol(ds.transform, points[:, 0], points[:, 1])
    rr, cc = np.asarray(rr), np.asarray(cc)
    good = (rr >= 0) & (cc >= 0) & (rr < ds.height) & (cc < ds.width)
    result = np.ma.masked_all((len(points), 3), dtype=float)
    if good.any():
        row0, col0 = int(rr[good].min()), int(cc[good].min())
        window = Window(col0, row0, int(cc[good].max()) - col0 + 1, int(rr[good].max()) - row0 + 1)
        data = ds.read(masked=True, window=window)
        result[good] = data[:, rr[good] - row0, cc[good] - col0].T
    return result


class ByteBudget:
    def __init__(self, maximum):
        self.maximum = maximum
        self.downloaded = 0

    def reserve(self, size):
        if size < 0 or self.downloaded + size > self.maximum:
            raise ValueError("WorldCover HTTP range budget exceeded")
        self.downloaded += size


class RangeFile(io.RawIOBase):
    """Seekable HTTP ranges. Refuse full responses and cap bytes before reading bodies."""

    def __init__(self, url, cache, budget, chunk=65536):
        super().__init__()
        self.url, self.cache, self.budget, self.chunk = url, Path(cache), budget, chunk
        self.cache.mkdir(parents=True, exist_ok=True)
        response = requests.head(url, timeout=30)
        response.raise_for_status()
        self.metadata = {
            "url": url,
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "bytes": int(response.headers["Content-Length"]),
        }
        if not self.metadata["etag"]:
            raise ValueError("WorldCover source must supply ETag")
        meta = self.cache / "source.json"
        if meta.exists():
            if json.loads(meta.read_text()) != self.metadata:
                raise ValueError("WorldCover source changed; use a fresh cache directory")
        else:
            with meta.open("x") as stream:
                json.dump(self.metadata, stream)
        self.position = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset + (self.position if whence == 1 else self.metadata["bytes"] if whence == 2 else 0)
        if whence not in (0, 1, 2) or position < 0:
            raise ValueError("invalid range seek")
        self.position = position
        return position

    def read(self, size=-1):
        stop = self.metadata["bytes"] if size < 0 else min(self.metadata["bytes"], self.position + size)
        result = bytearray()
        while self.position < stop:
            start = self.position // self.chunk * self.chunk
            end = min(start + self.chunk, self.metadata["bytes"])
            path = self.cache / f"{start}-{end}.bin"
            if path.exists():
                block = path.read_bytes()
                if len(block) != end - start:
                    raise ValueError("truncated range cache; remove invalid chunk explicitly")
            else:
                self.budget.reserve(end - start)
                headers = {"Range": f"bytes={start}-{end - 1}", "If-Match": self.metadata["etag"]}
                with requests.get(self.url, headers=headers, timeout=45, stream=True) as response:
                    if response.status_code != 206:
                        raise ValueError(f"range request refused: HTTP {response.status_code}")
                    if (
                        response.headers.get("Content-Range")
                        != f"bytes {start}-{end - 1}/{self.metadata['bytes']}"
                    ):
                        raise ValueError("unexpected HTTP range")
                    block = response.raw.read(end - start + 1)
                if len(block) != end - start:
                    raise ValueError("invalid range body length")
                with path.open("xb") as stream:
                    stream.write(block)
            length = min(stop - self.position, end - self.position)
            result.extend(block[self.position - start : self.position - start + length])
            self.position += length
        return bytes(result)


def read_worldcover(url, cache, budget, factor=16):
    """Read a complete coarse overview once per tile, never sample full-resolution pixels."""
    source = RangeFile(url, cache, budget)

    def opener(path, mode="rb"):
        if str(path) != url:
            raise FileNotFoundError(path)
        source.seek(0)
        return source

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open(url, opener=opener) as ds:
            if ds.crs != rasterio.crs.CRS.from_epsg(4326) or ds.count != 1:
                raise ValueError("unexpected WorldCover CRS or bands")
            if factor not in ds.overviews(1):
                raise ValueError("required WorldCover overview absent")
            shape = (math.ceil(ds.height / factor), math.ceil(ds.width / factor))
            array = ds.read(1, out_shape=shape, resampling=Resampling.nearest, masked=True)
            transform = ds.transform * ds.transform.scale(ds.width / shape[1], ds.height / shape[0])
            metadata = {
                **source.metadata,
                "overview_factor": factor,
                "overview_shape": list(shape),
                "transform": list(transform)[:6],
                "overview_resampling": "nearest read from existing categorical overview; original overview aggregation unspecified",
                "cached_range_bytes": sum(path.stat().st_size for path in Path(cache).glob("*.bin")),
            }
    return array, transform, metadata


def choose_cells(grid, raw_path):
    west, south, east, north = BBOX
    keys = set()
    for key in grid.axes:
        lat, lon = (float(a[0]) for a in grid.centres([key[0]], [key[1]]))
        if west <= lon <= east and south <= lat <= north:
            keys.add(key)
    raw = pd.read_csv(raw_path)
    dates = pd.to_datetime(raw.observed_on, errors="coerce")
    train = raw.loc[dates < pd.Timestamp("2025-07-01")]
    rows, cols = grid.locate(train.lat.to_numpy(), train.lon.to_numpy())
    keys.update((int(row), int(col)) for row, col in zip(rows, cols, strict=True) if row >= 0 and col >= 0)
    return sorted(keys)


def build(grid, raw_path, climate_path, cache, max_bytes):
    keys = choose_cells(grid, raw_path)
    points = np.concatenate([cell_points(grid, key) for key in keys])
    with rasterio.open(climate_path) as ds:
        climate = sample_climate(ds, points).reshape(len(keys), 225, 3)
    covers = np.ma.masked_all(len(points), dtype=np.uint8)
    by_tile = defaultdict(list)
    for position, (lon, lat) in enumerate(points):
        by_tile[tile_name(lon, lat)].append(position)
    budget, sources = ByteBudget(max_bytes), []
    for number, (tile, positions) in enumerate(sorted(by_tile.items()), 1):
        url = BASE_URL + tile
        try:
            array, transform, metadata = read_worldcover(url, Path(cache) / tile, budget)
        except requests.HTTPError as error:
            if error.response is None or error.response.status_code != 404:
                raise
            sources.append({"url": url, "status": "HTTP404, samples remain missing"})
            print(f"WorldCover {number}/{len(by_tile)}: {tile}; absent tile, samples missing", flush=True)
            continue
        rr, cc = rasterio.transform.rowcol(transform, points[positions, 0], points[positions, 1])
        rr, cc = np.asarray(rr), np.asarray(cc)
        if ((rr < 0) | (cc < 0) | (rr >= array.shape[0]) | (cc >= array.shape[1])).any():
            raise ValueError("WorldCover tile-point mapping mismatch")
        covers[positions] = array[rr, cc]
        sources.append(metadata)
        print(f"WorldCover {number}/{len(by_tile)}: {tile}; {budget.downloaded} HTTP bytes", flush=True)
    covers = covers.reshape(len(keys), 225)
    cells, validity = [], []
    for position, key in enumerate(keys):
        climate_mean, climate_count = climate_summary(climate[position])
        fractions, cover_count = cover_summary(covers[position])
        lat, lon = (float(a[0]) for a in grid.centres([key[0]], [key[1]]))
        cells.append([*key, lat, lon, *climate_mean, *fractions])
        validity.append([*key, *climate_count, cover_count])
    return {
        "schema_version": 1,
        "row_format": ROW_FORMAT,
        "cells": cells,
        "validity_row_format": [
            "row",
            "col",
            "bio1_samples",
            "bio4_samples",
            "bio12_samples",
            "cover_samples",
        ],
        "validity": validity,
        "grid": grid.metadata,
        "selection": {
            "bbox": list(BBOX),
            "method": "eligible published cell centres in bbox union eligible pre-July2025 raw observation cells; BC polygon clipping is a downstream step",
            "raw_sha256": sha256(raw_path),
            "raw_file": Path(raw_path).name,
        },
        "sampling": {
            "side": 15,
            "points_per_cell": 225,
            "method": "equal-area LAEA stratum centres, same fixed points for climate and cover",
            "cover_fractions": "conditional on valid samples; zero/nodata/unknown classes excluded; all null if none valid; not exact 10m area fractions",
            "climate_validity": "source masks and nonfinite excluded; legacy encoded extrema/physically invalid values excluded: BIO1 outside(-100,70), BIO4 and BIO12 outside(0,6553.4); mean of valid samples, null if none",
        },
        "sources": {
            "climate": {
                "file": Path(climate_path).name,
                "sha256": sha256(climate_path),
                "bands": [1, 4, 12],
                "version": "CHELSA v2.1 climatology 1981-2010",
                "urls": [CLIMATE_URL.format(band) for band in (1, 4, 12)],
                "units": [
                    "degrees C",
                    "temperature seasonality",
                    "annual precipitation scaled as legacy source",
                ],
                "scaling": "local values already transformed by legacy clip_chelsa_ca.py; source scale/offset must not be applied twice",
            },
            "landcover": {
                "version": "ESA WorldCover 2021 v200",
                "access": "https://esa-worldcover.org/en/data-access",
                "doi": "https://doi.org/10.5281/zenodo.7254221",
                "license": "CC-BY-4.0",
                "attribution": "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium",
                "tiles": sources,
                "http_bytes_this_run": budget.downloaded,
                "http_byte_limit": max_bytes,
            },
        },
        "code_sha256": sha256(__file__),
        "versions": {"rasterio": rasterio.__version__, "numpy": np.__version__},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--climate", type=Path, required=True)
    parser.add_argument("--grid-dir", type=Path, default=Path("cluster_results/ca"))
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/wtb-plant-habitat-worldcover"))
    parser.add_argument("--output", type=Path, default=Path("research/data/bc_plant_habitat.json"))
    parser.add_argument("--max-http-mb", type=int, default=200)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new path")
    if args.max_http_mb <= 0 or args.max_http_mb > 300:
        parser.error("HTTP budget must be between 1 and 300 MB")
    result = build(
        AppGrid("All biodiversity", args.grid_dir),
        args.raw,
        args.climate,
        args.cache_dir,
        args.max_http_mb * 1_000_000,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, separators=(",", ":"), allow_nan=False)
        stream.write("\n")
    print(f"Wrote {len(result['cells'])} cells to {args.output}")


if __name__ == "__main__":
    main()
