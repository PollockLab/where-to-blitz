"""Geometry, missing-data and bounded-range contracts of the habitat grid builder."""

import io
from types import SimpleNamespace

import numpy as np
import pytest
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from build_plant_habitat import (
    CLASSES,
    ByteBudget,
    RangeFile,
    cell_points,
    climate_summary,
    cover_summary,
    sample_climate,
    tile_name,
)
from grid_lattice import Lattice


@pytest.fixture
def grid():
    crs = "+proj=laea +lat_0=45 +lon_0=-100 +datum=WGS84 +units=m +no_defs"
    return SimpleNamespace(
        lattice=Lattice(25000, crs),
        forward=Transformer.from_crs(4326, crs, always_xy=True),
        inverse=Transformer.from_crs(crs, 4326, always_xy=True),
    )


def test_stratified_points_have_exact_keys_and_cover_equal_area_strata(grid):
    key = (122, 55)
    points = cell_points(grid, key)
    assert points.shape == (225, 2)
    x, y = grid.forward.transform(points[:, 0], points[:, 1])
    assert np.all(np.floor((grid.lattice.y1 - y) / 25000) == key[0])
    assert np.all(np.floor((x - grid.lattice.x0) / 25000) == key[1])
    expected_x = grid.lattice.x0 + (key[1] + (np.arange(15) + 0.5) / 15) * 25000
    expected_y = grid.lattice.y1 - (key[0] + (np.arange(15) + 0.5) / 15) * 25000
    assert np.allclose(x.reshape(15, 15), expected_x[None, :], rtol=0, atol=0.001)
    assert np.allclose(y.reshape(15, 15), expected_y[:, None], rtol=0, atol=0.001)
    assert np.allclose(points, cell_points(grid, key))
    with pytest.raises(ValueError):
        cell_points(grid, (-1, 0))
    with pytest.raises(ValueError):
        cell_points(grid, key, 0)


@pytest.mark.parametrize(
    "lon,lat,corner",
    [(-123.1, 49, "N48W126"), (-123, 51, "N51W123"), (-0.1, -0.1, "S03W003"), (0, 0, "N00E000")],
)
def test_tile_names_floor_southwest_degrees(lon, lat, corner):
    assert corner in tile_name(lon, lat)


def test_invalid_worldcover_coordinates_fail():
    for lon, lat in [(float("nan"), 50), (180, 50), (-124, 90)]:
        with pytest.raises(ValueError):
            tile_name(lon, lat)


def test_fractions_exclude_missing_and_unknown_values_without_inventing_zero_cover():
    fractions, count = cover_summary(np.ma.array([10, 10, 80, 0, 255, 30], mask=[0, 0, 0, 0, 0, 1]))
    assert count == 3
    assert sum(fractions) == pytest.approx(1)
    assert fractions[CLASSES.index(10)] == pytest.approx(2 / 3)
    assert fractions[CLASSES.index(80)] == pytest.approx(1 / 3)
    assert cover_summary([0, 255]) == ([None] * 11, 0)


def test_climate_extrema_and_masks_are_excluded_per_band():
    samples = np.ma.array(
        [[12, 300, 1200], [-273.15, 0, 6553.5], [16, 400, 1400]], mask=[[0, 0, 0], [0, 0, 0], [0, 1, 0]]
    )
    means, counts = climate_summary(samples)
    assert means == pytest.approx([14, 300, 1300])
    assert counts == [2, 1, 2]
    assert climate_summary([[-273.15, 0, 6553.5]]) == ([None] * 3, [0] * 3)
    with pytest.raises(ValueError):
        climate_summary([[10]])


def test_georeferenced_climate_sampling_respects_nodata_and_outside_extent():
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=2,
            height=2,
            count=3,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_origin(-124, 50, 1, 1),
            nodata=-9999,
        ) as ds:
            data = np.array(
                [[[10, 20], [30, -9999]], [[100, 200], [300, -9999]], [[1000, 2000], [3000, -9999]]],
                dtype="float32",
            )
            ds.write(data)
            samples = sample_climate(ds, [[-123.5, 49.5], [-122.5, 48.5], [-125, 49]])
    assert samples[0].tolist() == [10, 100, 1000]
    assert np.ma.getmaskarray(samples)[1:].all()


def fake_source(monkeypatch, content=b"0123456789", status=206):
    headers = {"Content-Length": str(len(content)), "ETag": '"fixed"', "Last-Modified": "fixed"}
    monkeypatch.setattr(
        "build_plant_habitat.requests.head",
        lambda *a, **k: SimpleNamespace(headers=headers, raise_for_status=lambda: None),
    )
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def get(url, *, headers, **kwargs):
        calls.append(headers)
        start, end = map(int, headers["Range"].removeprefix("bytes=").split("-"))
        response = Response()
        response.status_code = status
        response.headers = {"Content-Range": f"bytes {start}-{end}/{len(content)}"}
        response.raw = io.BytesIO(content[start : end + 1])
        return response

    monkeypatch.setattr("build_plant_habitat.requests.get", get)
    return calls


def test_range_reader_caches_resumes_and_never_downloads_full_source(monkeypatch, tmp_path):
    calls = fake_source(monkeypatch)
    budget = ByteBudget(8)
    source = RangeFile("https://example.test/map.tif", tmp_path, budget, chunk=4)
    source.seek(1)
    assert source.read(5) == b"12345"
    assert budget.downloaded == 8
    assert all(call["If-Match"] == '"fixed"' for call in calls)
    second = RangeFile("https://example.test/map.tif", tmp_path, ByteBudget(0), chunk=4)
    assert second.read(8) == b"01234567"
    assert len(calls) == 2
    source.seek(8)
    with pytest.raises(ValueError, match="budget"):
        source.read(2)
    assert len(calls) == 2


def test_full_http_response_is_rejected_before_reading(monkeypatch, tmp_path):
    fake_source(monkeypatch, status=200)
    with pytest.raises(ValueError, match="range request refused"):
        RangeFile("https://example.test/map.tif", tmp_path, ByteBudget(20), chunk=4).read(1)


def test_cached_source_cannot_silently_change(monkeypatch, tmp_path):
    fake_source(monkeypatch)
    RangeFile("https://example.test/map.tif", tmp_path, ByteBudget(20))
    with pytest.raises(ValueError, match="source changed"):
        RangeFile("https://other.test/map.tif", tmp_path, ByteBudget(20))
