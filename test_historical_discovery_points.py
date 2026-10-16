"""Historical marker identities and positions must reproduce the published numerator."""

import copy
import hashlib
import json

import pytest

from historical_discovery_points import load_points, validate_points
from test_discovery_layer import event, fixture_product


@pytest.fixture
def inputs(tmp_path):
    product = fixture_product(tmp_path)
    points = []
    for index, col in enumerate((400, 401)):
        lon, lat = event("fixture", col=col)["geometry"]["coordinates"]
        points.append(
            {
                "event_id": f"lab:{'a' * 64}:{index}",
                "scientific_name": "Supplied name",
                "observed_year": 2024,
                "longitude": lon,
                "latitude": lat,
            }
        )
    features = [
        {
            "type": "Feature",
            "properties": {"event_id": point["event_id"], "observed_year": point["observed_year"]},
            "geometry": {"type": "Point", "coordinates": [point["longitude"], point["latitude"]]},
        }
        for point in points
    ]
    product["sources"]["discoveries"]["sha256"] = hashlib.sha256(
        json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")).encode()
    ).hexdigest()
    data = {
        "schema_version": 1,
        "discovery_sha256": "b" * 64,
        "source": {
            "events_sha256": product["sources"]["discoveries"]["sha256"],
            "shapefile_components": {ext: "c" * 64 for ext in (".shp", ".shx", ".dbf", ".prj")},
            "identity": "source feature; not iNaturalist observation ID",
            "date_precision": "year",
            "coordinates": "reported source positions, including source-obscured records",
            "selection": "lab-supplied point set; original selection unknown",
        },
        "points": points,
    }
    return data, product


def test_exact_points_preserve_both_tier_counts(inputs):
    data, product = inputs
    assert validate_points(data, product, "b" * 64) is data


@pytest.mark.parametrize(
    "failure", ["extra", "duplicate", "missing", "moved", "within_cell", "identity", "year", "name", "hash", "source"]
)
def test_rejects_privacy_and_count_or_provenance_mismatches(inputs, failure):
    data, product = copy.deepcopy(inputs)
    point = data["points"][0]
    if failure == "extra":
        point["observer"] = "must never be accepted"
    elif failure == "duplicate":
        data["points"][1]["event_id"] = point["event_id"]
    elif failure == "missing":
        data["points"].pop()
    elif failure == "moved":
        point.update(longitude=data["points"][1]["longitude"], latitude=data["points"][1]["latitude"])
    elif failure == "identity":
        point["event_id"] = f"lab:{'0' * 64}:999999"
    elif failure == "year":
        point["observed_year"] = 2025
    elif failure == "within_cell":
        point["longitude"] += 0.000001
    elif failure == "name":
        point["scientific_name"] = ""
    elif failure == "hash":
        data["discovery_sha256"] = "d" * 64
    elif failure == "source":
        data["source"]["events_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="historical points"):
        validate_points(data, product, "b" * 64)


def test_unconfigured_and_other_vintage_do_not_activate(tmp_path, inputs):
    data, _ = inputs
    path = tmp_path / "points.json"
    path.write_text(json.dumps(data))
    assert load_points(path, None, None) is None
    assert load_points(path, None, {"sha256": "d" * 64}) is None
