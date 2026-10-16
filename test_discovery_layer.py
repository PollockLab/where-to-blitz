"""Hand-counted discovery rates, source contracts, and executable artifact gates."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.warp import transform

import build_discovery_layer as layer

SCOPE = {
    "period": {"start": "2024-01-01", "end": "2024-12-31"},
    "filters": {"country": "CA", "quality_grade": "research", "taxon": "all"},
    "support_id": "hand-counted-fixture",
}


def event(identity, row=400, col=400, observed="2024-02-01"):
    x, y = layer.GRID.centres(row, col)
    lon, lat = transform(layer.LAEA, "EPSG:4326", [float(x)], [float(y)])
    return {
        "type": "Feature",
        "properties": {"event_id": identity, "observed_on": observed},
        "geometry": {"type": "Point", "coordinates": [lon[0], lat[0]]},
    }


def sources(tmp_path, events=None, counts=None):
    points, observations = tmp_path / "events.geojson", tmp_path / "observations.csv"
    points.write_text(json.dumps({"type": "FeatureCollection", "features": events or []}))
    counts = {(400 + row, 400 + col): 0 for row in range(5) for col in range(5)} if counts is None else counts
    observations.write_text(
        "row,col,observation_count\n" + "".join(f"{r},{c},{n}\n" for (r, c), n in counts.items())
    )
    declarations = []
    for path, unit in ((points, "discovery_events"), (observations, "observations")):
        metadata = {
            "schema_version": 1,
            "source_id": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "unit": unit,
            "scope": copy.deepcopy(SCOPE),
            "crs": "EPSG:4326" if unit == "discovery_events" else layer.LAEA.to_string(),
        }
        if unit == "observations":
            metadata["grid"] = layer.GRID_CONTRACT.copy()
        else:
            metadata["event_definition"] = "one_discovery_event_per_observation"
        sidecar = path.with_suffix(".metadata.json")
        sidecar.write_text(json.dumps(metadata))
        declarations.append(sidecar)
    return points, observations, *declarations


def edit_metadata(path, mutate):
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def fixture_product(tmp_path):
    counts = {(400 + row, 400 + col): 0 for row in range(5) for col in range(5)}
    counts[400, 400], counts[400, 401] = 1, 99
    return layer.build(*sources(tmp_path, [event("first"), event("second", col=401)], counts))


def test_hand_counted_ratio_of_sums_and_explicit_zero_support(tmp_path):
    product = fixture_product(tmp_path)
    rows = product["tiers"]["5000"]["cells"]
    assert rows[0] == [400, 400, 1, 1, 10000, 1, 1]
    assert rows[1] == [400, 401, 1, 99, 10000 / 99, 1, 1]
    assert rows[2] == [400, 402, 0, 0, None, 1, 1]
    assert product["tiers"]["25000"]["cells"] == [[80, 80, 2, 100, 200, 25, 25]]
    assert product["events"] == {"unique": 2, "duplicates_removed": 0}
    assert layer.validate_artifact(json.loads(json.dumps(product))) == product


def test_partial_parent_has_counts_but_no_rate_and_absent_child_is_unavailable(tmp_path):
    product = layer.build(*sources(tmp_path, [event("one")], {(400, 400): 10, (400, 401): 0}))
    assert product["tiers"]["25000"]["cells"] == [[80, 80, 1, 10, None, 2, 25]]
    assert len(product["tiers"]["5000"]["cells"]) == 2


def test_identical_event_ids_deduplicate_and_distinct_ids_at_same_point_count(tmp_path):
    one = event("one")
    product = layer.build(*sources(tmp_path, [one, copy.deepcopy(one), event("two")], {(400, 400): 3}))
    assert product["events"] == {"unique": 2, "duplicates_removed": 1}
    assert product["tiers"]["5000"]["cells"][0][2:5] == [2, 3, 20000 / 3]


def test_conflicting_duplicate_fails_even_within_same_cell(tmp_path):
    other = event("one", observed="2024-02-02")
    with pytest.raises(ValueError, match="conflicting duplicate"):
        layer.build(*sources(tmp_path, [event("one"), other], {(400, 400): 5}))


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda m: m["scope"]["filters"].update(taxon="plants"), "scope mismatch"),
        (lambda m: m["scope"]["period"].update(end="2024-06-01"), "scope mismatch"),
        (lambda m: m["scope"].update(support_id="another-footprint"), "scope mismatch"),
        (lambda m: m.update(unit="density"), "expected unit observations"),
        (lambda m: m.update(crs="EPSG:4326"), "incorrect observations CRS"),
        (lambda m: m["grid"].update(x0=0), "grid must match"),
        (lambda m: m["scope"].update(filters={}), "explicitly describe"),
        (lambda m: m["scope"]["period"].update(start="2025-01-01"), "must not follow"),
        (lambda m: m.update(sha256="0" * 64), "sha256 mismatch"),
    ],
)
def test_source_contract_mismatches_are_fatal(tmp_path, mutate, match):
    inputs = sources(tmp_path)
    edit_metadata(inputs[3], mutate)
    with pytest.raises(ValueError, match=match):
        layer.build(*inputs)


@pytest.mark.parametrize("observed", ["2023-12-31", "2025-01-01", "20240201", None])
def test_event_date_must_be_in_declared_inclusive_period(tmp_path, observed):
    with pytest.raises(ValueError, match="observed_on"):
        layer.build(*sources(tmp_path, [event("one", observed=observed)], {(400, 400): 1}))


def test_events_on_both_period_boundaries_are_retained(tmp_path):
    product = layer.build(
        *sources(
            tmp_path,
            [event("first", observed="2024-01-01"), event("last", observed="2024-12-31")],
            {(400, 400): 2},
        )
    )
    assert product["events"]["unique"] == 2


@pytest.mark.parametrize(
    "coordinates", [[None, 45], [float("nan"), 45], [True, 45], [181, 45], [0, 91], [0, 0]]
)
def test_bad_or_outside_lattice_points_fail(tmp_path, coordinates):
    feature = event("invalid")
    feature["geometry"]["coordinates"] = coordinates
    with pytest.raises(ValueError, match="point"):
        layer.build(*sources(tmp_path, [feature], {(400, 400): 1}))


def test_numerator_outside_known_support_or_above_exposure_fails(tmp_path):
    with pytest.raises(ValueError, match="outside declared observation support"):
        layer.build(*sources(tmp_path, [event("one", col=402)], {(400, 400): 1}))
    with pytest.raises(ValueError, match="exceed observation exposure"):
        layer.build(*sources(tmp_path, [event("one")], {(400, 400): 0}))


@pytest.mark.parametrize(
    "text", ["400,400,-1", "400,400,1.5", "400,400,nan", "400,400,", "905,400,1", "400,1100,1"]
)
def test_invalid_count_values_and_lattice_bounds(text):
    with pytest.raises(ValueError):
        layer.read_counts(("row,col,observation_count\n" + text + "\n").encode())


def test_empty_and_duplicate_denominator_support_fail():
    with pytest.raises(ValueError, match="no support"):
        layer.read_counts(b"row,col,observation_count\n")
    with pytest.raises(ValueError, match="duplicate support"):
        layer.read_counts(b"row,col,observation_count\n400,400,1\n400,400,1\n")


def test_exact_projected_boundaries_are_half_open(monkeypatch):
    x, y = layer.GRID.x0 + 400 * 5000, layer.GRID.y1 - 400 * 5000
    monkeypatch.setattr(layer, "transform", lambda *args: ([x], [y]))
    assert layer.point_cell(-100, 45) == (400, 400)
    x, y = layer.GRID.x0, layer.GRID.y1
    assert layer.point_cell(-100, 45) == (0, 0)
    x = layer.GRID.x0 + layer.GRID.ncol * 5000
    with pytest.raises(ValueError, match="outside the lattice"):
        layer.point_cell(-100, 45)
    x, y = layer.GRID.x0, layer.GRID.y1 - layer.GRID.nrow * 5000
    with pytest.raises(ValueError, match="outside the lattice"):
        layer.point_cell(-100, 45)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["tiers"]["5000"]["cells"][0].__setitem__(4, 300),
        lambda p: p["tiers"]["5000"]["cells"][0].__setitem__(2, 2),
        lambda p: p["tiers"]["5000"]["cells"].append(p["tiers"]["5000"]["cells"][0]),
        lambda p: p["tiers"]["25000"]["cells"][0].__setitem__(3, 200),
        lambda p: p["events"].update(unique=50),
        lambda p: p["sources"]["observations"]["scope"].update(support_id="changed"),
        lambda p: p.update(metric="proxy"),
    ],
)
def test_consumer_gate_rejects_corruption(tmp_path, mutate):
    product = fixture_product(tmp_path)
    mutate(product)
    with pytest.raises(ValueError):
        layer.validate_artifact(product)


def test_consumer_gate_rejects_painted_partial_parent(tmp_path):
    product = layer.build(*sources(tmp_path, [], {(400, 400): 10}))
    product["tiers"]["25000"]["cells"][0][4] = 0
    with pytest.raises(ValueError, match="unsupported rate must be null"):
        layer.validate_artifact(product)


def test_cli_writes_real_count_rasters_with_distinct_zero_and_nodata(tmp_path):
    inputs = sources(tmp_path, [event("one")], {(400, 400): 10, (400, 401): 0})
    output, rasters = tmp_path / "layer.json", tmp_path / "rasters"
    command = [
        sys.executable,
        str(Path(layer.__file__)),
        "--discoveries",
        str(inputs[0]),
        "--observations",
        str(inputs[1]),
        "--discoveries-metadata",
        str(inputs[2]),
        "--observations-metadata",
        str(inputs[3]),
        "--out",
        str(output),
        "--raster-dir",
        str(rasters),
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    product = json.loads(output.read_text())
    assert layer.validate_artifact(product)
    with rasterio.open(rasters / "discovery_5000m.tif") as ds:
        assert ds.crs == layer.LAEA and ds.transform == layer.GRID.transform
        assert ds.descriptions == tuple(layer.BANDS)
        assert ds.read(1)[400, 400] == 1
        assert ds.read(2)[400, 401] == 0
        assert np.isnan(ds.read(3)[400, 401])
        assert np.isnan(ds.read(1)[400, 402])
    with rasterio.open(rasters / "discovery_25000m.tif") as ds:
        assert np.isnan(ds.read(3)[80, 80])
        assert ds.read(4)[80, 80] == 2
        assert ds.read(5)[80, 80] == 25
    output.unlink()
    inputs[1].write_text(inputs[1].read_text() + "400,402,5\n")
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 2 and "sha256 mismatch" in result.stderr
    assert not output.exists()
