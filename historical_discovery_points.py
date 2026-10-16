"""Validate the approved reported-position derivative against its exact rate grid."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from build_discovery_layer import point_cell


def validate_points(data, discovery, discovery_sha256):
    """Reject identity, privacy-field, annual-scope and cell-count mismatches."""
    if not isinstance(data, dict) or set(data) != {"schema_version", "discovery_sha256", "source", "points"}:
        raise ValueError("historical points: invalid top-level fields")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("historical points: expected schema_version 1")
    if data["discovery_sha256"] != discovery_sha256:
        raise ValueError("historical points: rate-grid checksum mismatch")
    source = data["source"]
    fields = {
        "events_sha256",
        "shapefile_components",
        "identity",
        "date_precision",
        "coordinates",
        "selection",
    }
    if not isinstance(source, dict) or set(source) != fields or source["date_precision"] != "year":
        raise ValueError("historical points: invalid source declaration")
    original = discovery["sources"]["discoveries"]
    if source["events_sha256"] != original["sha256"]:
        raise ValueError("historical points: source-event checksum mismatch")
    components = source["shapefile_components"]
    if (
        not isinstance(components, dict)
        or set(components) != {".shp", ".shx", ".dbf", ".prj"}
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in components.values()
        )
    ):
        raise ValueError("historical points: invalid source component checksums")
    declared = original.get("original_sources", {}).get("lab_shapefile_components")
    if declared is not None and components != declared:
        raise ValueError("historical points: source component mismatch")
    points = data["points"]
    if not isinstance(points, list) or len(points) != discovery["events"]["unique"]:
        raise ValueError("historical points: event count mismatch")
    fine, seen = Counter(), set()
    period = discovery["scope"]["period"]
    for point in points:
        if not isinstance(point, dict) or set(point) != {
            "event_id",
            "scientific_name",
            "observed_year",
            "longitude",
            "latitude",
        }:
            raise ValueError("historical points: unexpected point fields")
        identity, name, year = point["event_id"], point["scientific_name"], point["observed_year"]
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"lab:[a-f0-9]{64}:\d+", identity)
            or identity in seen
        ):
            raise ValueError("historical points: invalid or duplicate source identity")
        seen.add(identity)
        if not isinstance(name, str) or not name.strip() or len(name) > 300 or any(ord(c) < 32 for c in name):
            raise ValueError("historical points: invalid supplied scientific name")
        if type(year) is not int or not int(period["start"][:4]) <= year <= int(period["end"][:4]):
            raise ValueError("historical points: year outside source period")
        fine[point_cell(point["longitude"], point["latitude"])] += 1
    # Recreate the annual producer bytes to detect even movement within a cell.
    features = [
        {
            "type": "Feature",
            "properties": {"event_id": point["event_id"], "observed_year": point["observed_year"]},
            "geometry": {"type": "Point", "coordinates": [point["longitude"], point["latitude"]]},
        }
        for point in points
    ]
    encoded = json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != source["events_sha256"]:
        raise ValueError("historical points: positions/identities differ from source-event checksum")
    for resolution in (5000, 25000):
        factor = resolution // 5000
        actual = Counter()
        for (row, col), count in fine.items():
            actual[row // factor, col // factor] += count
        expected = Counter(
            {(row[0], row[1]): row[2] for row in discovery["tiers"][str(resolution)]["cells"] if row[2]}
        )
        if actual != expected:
            raise ValueError(f"historical points: {resolution} m cell counts disagree with rate grid")
    return data


def load_points(path, discovery_path, descriptor):
    """Unconfigured or a different source vintage leaves the optional markers off."""
    if not descriptor or not Path(path).exists():
        return None
    data = json.loads(Path(path).read_bytes())
    if data.get("discovery_sha256") != descriptor["sha256"]:
        return None
    payload = Path(discovery_path).read_bytes()
    if hashlib.sha256(payload).hexdigest() != descriptor["sha256"]:
        raise ValueError("historical points: discovery input changed during build")
    return validate_points(data, json.loads(payload), descriptor["sha256"])
