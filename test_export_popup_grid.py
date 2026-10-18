"""The published row inventory and score precision come from the final colour stacks."""

import json
from pathlib import Path

import matplotlib.image
import numpy as np
import pytest
import rasterio

from build_grid_values import _LUT, render_goal_rgba
from export_popup_grid import export_popup_grid
from goal_presets import AXES, PRESETS
from grid_lattice import Lattice
from grid_schema import BANDS

LAEA = "+proj=laea +lat_0=45 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"


def make_build(directory, *, inconsistent=False):
    lat = Lattice(25000, rasterio.CRS.from_string(LAEA))
    path = directory / "grid_25000m"
    path.mkdir()
    index = {
        "groups": ["All biodiversity", "Aves"],
        "files": {"All biodiversity": "webapp_data_All_biodiversity.json", "Aves": "webapp_data_Aves.json"},
        "row_format": ["lat", "lon"] + BANDS,
        "n_cells": 1,
        "res_m": 25000,
        "crs": lat.crs.to_wkt(),
        "lattice": {"x0": lat.x0, "y1": lat.y1, "nrow": lat.nrow, "ncol": lat.ncol, "cell_km2": lat.cell_km2},
    }
    (directory / "index.json").write_text(json.dumps(index))
    (path / "index.json").write_text(json.dumps(index))
    # Two inland cells. Native JSON had only the first cell and the wrong score.
    for group, name in index["files"].items():
        (directory / name).write_text(json.dumps({group: [[54, -100, 0.99, 0, 0, 0, 0, 999, 100]]}))
        data = np.full((len(BANDS), *lat.shape), np.nan, dtype=np.float32)
        data[:, 135, 85:87] = 0
        # Three-decimal rounding crosses a colour boundary for this float32.
        data[0, 135, 85:87] = [0.49951, 0.4996]
        data[5, 135, 85:87] = [12.75, 4.25]
        data[6, 135, 85:87] = [25, 50]
        if inconsistent and group == "Aves":
            data[:, 135, 86] = np.nan
        with rasterio.open(
            path / (group.replace(" ", "_") + ".tif"),
            "w",
            driver="GTiff",
            count=len(BANDS),
            dtype="float32",
            crs=lat.crs,
            height=lat.nrow,
            width=lat.ncol,
            transform=lat.transform,
            nodata=np.nan,
        ) as dst:
            dst.write(data)
            dst.descriptions = tuple(BANDS)
    return lat


def test_export_replaces_native_geometry_and_preserves_float32_blend(tmp_path):
    lat = make_build(tmp_path)
    export_popup_grid(tmp_path)
    index = json.loads((tmp_path / "index.json").read_text())
    assert index["n_cells"] == 2
    assert json.loads((tmp_path / "grid_25000m/index.json").read_text())["n_cells"] == 2
    rows = json.loads((tmp_path / index["files"]["Aves"]).read_text())["Aves"]
    lon, latitude = lat.centres_lonlat([135, 135], [85, 86])
    assert [r[:2] for r in rows] == [[round(y, 3), round(x, 3)] for y, x in zip(latitude, lon, strict=True)]
    assert rows[0][2] == float(np.float32(0.49951))
    assert rows[0][2] < rows[1][2]  # rounded native JSON would create a false rank tie
    assert rows[0][-2:] == [12.75, 25]
    png = render_goal_rgba(tmp_path / "grid_25000m/Aves.tif", PRESETS[0], 25000)
    for row, col in zip(rows, [85, 86], strict=True):
        pixel = int(np.rint(row[2] * 255))
        assert np.array_equal(png[135, col, :3], _LUT[pixel])
    assert not np.array_equal(_LUT[int(np.rint(round(rows[0][2], 3) * 255))], png[135, 85, :3])


def test_mixed_support_fails_before_overwriting_any_published_rows(tmp_path):
    make_build(tmp_path, inconsistent=True)
    before = {p: p.read_bytes() for p in tmp_path.glob("*.json")}
    with pytest.raises(ValueError, match="support"):
        export_popup_grid(tmp_path)
    assert all(p.read_bytes() == content for p, content in before.items())


def test_shipped_rows_match_final_stacks_exactly():
    directory = Path(__file__).parent / "cluster_results/ca"
    index = json.loads((directory / "index.json").read_text())
    if not (directory / "grid_25000m/All_biodiversity.tif").exists():
        pytest.skip("final raster stacks arrive with the build")
    coordinates = None
    for group, filename in index["files"].items():
        rows = json.loads((directory / filename).read_text())[group]
        with rasterio.open(directory / "grid_25000m" / (group.replace(" ", "_") + ".tif")) as src:
            data = src.read()
            row, col = np.where(np.isfinite(data[0]))
            assert len(rows) == len(row) == index["n_cells"]
            lat = Lattice(25000, src.crs)
            lon, latitude = lat.centres_lonlat(row, col)
            expected_coords = [[round(y, 3), round(x, 3)] for y, x in zip(latitude, lon, strict=True)]
            assert [r[:2] for r in rows] == expected_coords
            if coordinates is not None:
                assert expected_coords == coordinates
            coordinates = expected_coords
            actual = np.asarray(rows)[:, 2:]
            expected = data[:, row, col].T.astype(float)
            expected[:, :5] = np.nan_to_num(expected[:, :5])
            expected[:, 5] = np.where(np.isfinite(expected[:, 5]), expected[:, 5], -1)
            assert np.array_equal(actual, expected), group


def test_shipped_png_bins_and_support_match_published_unrounded_scores():
    directory = Path(__file__).parent / "cluster_results/ca"
    if not (directory / "values").exists():
        pytest.skip("colour PNGs arrive with the build")
    index = json.loads((directory / "index.json").read_text())
    hidden = set(json.loads((directory / "us_cells.json").read_text())["us_cells"])
    lat = Lattice(25000, rasterio.CRS.from_string(index["crs"]))
    for group, filename in index["files"].items():
        records = json.loads((directory / filename).read_text())[group]
        x, y = rasterio.warp.transform("EPSG:4326", lat.crs,
                                      [r[1] for r in records], [r[0] for r in records])
        row, col = rasterio.transform.rowcol(lat.transform, x, y)
        visible = np.array([f"{r[0]:.3f},{r[1]:.3f}" not in hidden for r in records])
        expected_alpha = np.zeros(lat.shape, dtype=bool)
        expected_alpha[np.asarray(row)[visible], np.asarray(col)[visible]] = True
        axes = np.asarray(records)[:, 2:2 + len(AXES)]
        for preset in PRESETS:
            name = f"{group.replace(' ', '_')}_{preset['name'].lower().replace(' ', '_')}_grid_25000m.png"
            pixels = (matplotlib.image.imread(directory / "values" / name) * 255).round().astype(np.uint8)
            assert np.array_equal(pixels[:, :, 3] == 255, expected_alpha), name
            blend = sum(axes[:, i] * weight for i, weight in enumerate(preset["w"]) if weight)
            bins = np.rint(np.clip(blend / preset["scale"], 0, 1) * 255).astype(np.uint8)
            assert np.array_equal(pixels[np.asarray(row)[visible], np.asarray(col)[visible], :3],
                                  _LUT[bins[visible]]), name


def test_categorical_gettingeven_stack_is_not_a_priority_stack(tmp_path):
    from validate_artifacts import find_tif_groups

    for name in ("Aves", "gettingeven", "index"):
        (tmp_path / f"{name}.tif").touch()
    assert set(find_tif_groups(tmp_path)) == {"Aves"}
