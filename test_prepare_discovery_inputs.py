"""Annual identity proof, known zero support, and removal of private source fields."""

import json

import numpy as np
import pytest
from rasterio.warp import transform

import prepare_discovery_inputs as prepare


def point():
    return {"key": ("Species alpha", "private-observer"), "longitude": -75.0, "latitude": 45.0}


def row(identity=1, **changes):
    return {
        "scientific_name": "Species alpha",
        "user_login": "private-observer",
        "longitude": -75.0,
        "latitude": 45.0,
        "year": "2025",
        "quality_grade": "research",
        "row": 400,
        "col": 400,
        "source_row_index": identity,
        **changes,
    }


def test_repeated_lab_points_require_distinct_compatible_raw_rows():
    assigned, ambiguous = prepare.assign_points([point(), point()], [row(1), row(2)], 2025)
    assert {entry["source_row_index"] for entry in assigned.values()} == {1, 2}
    assert ambiguous == 2
    with pytest.raises(ValueError, match="distinct raw observation rows"):
        prepare.assign_points([point(), point()], [row(1)], 2025)


@pytest.mark.parametrize("changes", [{"year": "2024"}, {"quality_grade": "needs_id"}])
def test_annual_match_must_prove_the_actual_year_and_quality(changes):
    with pytest.raises(ValueError, match="incompatible annual observation cohort"):
        prepare.assign_points([point()], [row(**changes)], 2025)


def test_unmatched_positions_and_spatially_meaningful_ambiguity_fail():
    with pytest.raises(ValueError, match="no sub-millimetre"):
        prepare.assign_points([point()], [row(longitude=-74.999)], 2025)
    with pytest.raises(ValueError, match="cross a lattice edge"):
        prepare.assign_points([point()], [row(1), row(2, col=401)], 2025)


def test_selected_parent_support_contains_all_known_zero_children():
    counts = np.zeros(prepare.GRID.shape, dtype=np.int64)
    counts[400, 400], counts[401, 401], counts[405, 405] = 3, 7, 11
    rows = list(prepare.supported_counts(counts))
    assert len(rows) == 50
    assert sum(row[2] for row in rows) == 21
    assert (404, 404, 0) in rows and (409, 409, 0) in rows
    assert (410, 410, 0) not in rows


@pytest.mark.parametrize("mutation", [None, "observations", "points"])
def test_prepared_artifact_preserves_points_and_requires_frozen_sources(tmp_path, monkeypatch, mutation):
    x, y = prepare.GRID.centres(400, 400)
    lon, lat = transform(prepare.LAEA, "EPSG:4326", [float(x)], [float(y)])
    fixture_point = {**point(), "longitude": lon[0], "latitude": lat[0]}
    fixture_rows = [row(i, longitude=lon[0], latitude=lat[0]) for i in (1, 2)]
    points_path = tmp_path / "private.shp"
    components = {}
    for suffix in (".shp", ".shx", ".dbf", ".prj"):
        path = points_path.with_suffix(suffix)
        path.write_bytes(b"frozen lab point source")
        components[suffix] = prepare.sha256(path)
    monkeypatch.setattr(prepare, "read_lab_points", lambda _: ([fixture_point, fixture_point], components))
    counts = np.zeros(prepare.GRID.shape, dtype=np.int64)
    counts[400, 400] = 10  # Exposure includes observations outside the supplied discovery list.
    raw = tmp_path / "private.parquet"
    raw.write_bytes(b"private frozen raw source")

    def scan(*_):
        if mutation == "observations":
            raw.write_bytes(b"a different observation export arrived during the scan")
        elif mutation == "points":
            points_path.with_suffix(".dbf").write_bytes(b"a different source point table")
        return counts, fixture_rows, {"eligible_observations": 10}

    monkeypatch.setattr(prepare, "scan_observations", scan)
    output = tmp_path / "prepared"
    if mutation:
        with pytest.raises(ValueError, match="source changed"):
            prepare.prepare(points_path, raw, output, 2025)
        assert not output.exists()
        return
    audit = prepare.prepare(points_path, raw, output, 2025)
    assert audit["source_features"] == audit["matched_distinct_rows"] == 2
    assert audit["ambiguous_daily_identities"] == 2
    for path in output.iterdir():
        assert "private-observer" not in path.read_text()
        assert "user_login" not in path.read_text()
    events = json.loads((output / "discovery-events.geojson").read_text())
    assert all(feature["properties"]["observed_year"] == 2025 for feature in events["features"])
    assert all("observed_on" not in feature["properties"] for feature in events["features"])
    assert len({feature["properties"]["event_id"] for feature in events["features"]}) == 2
    product = json.loads((output / "discovery_grid.json").read_text())
    assert product["tiers"]["25000"]["cells"] == [[80, 80, 2, 10, 2000, 25, 25]]
