"""Build discovery events per 10,000 observations from explicitly matched sources.

Inputs are RFC 7946 Point GeoJSON (properties.event_id and observed_on, YYYY-MM-DD)
and a 5 km CSV with row,col,observation_count. Export shapefiles to RFC 7946 first.
Every CSV row declares known support, including explicit zero counts; missing rows
are unavailable, never zero. Grid edges use [west,east) and (south,north].

Each input needs a JSON sidecar with schema_version=1, source_id, sha256, unit,
crs, and scope={period:{start,end},filters:{...},support_id:...}. Dates are inclusive.
Units are discovery_events and observations, respectively. The numerator CRS must
be EPSG:4326; the denominator CRS must be the existing WGS84 LAEA lattice CRS.
The numerator event_definition must be one_discovery_event_per_observation.
The denominator also needs grid={resolution_m:5000,x0:-2150000,y1:4300000,
width:1100,height:905}. Both scopes must match exactly. A source hash is of the
original input bytes, not its sidecar. No density or training-count proxy is used.
"""

import argparse
import csv
import hashlib
import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform

from grid_lattice import Lattice

LAEA = CRS.from_string("+proj=laea +lat_0=45 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")
GRID = Lattice(5000, LAEA)
GRID_CONTRACT = {"resolution_m": 5000, "x0": GRID.x0, "y1": GRID.y1, "width": GRID.ncol, "height": GRID.nrow}
FIELDS = [
    "row",
    "col",
    "discovery_count",
    "observation_count",
    "rate",
    "support_children",
    "expected_children",
]
BANDS = FIELDS[2:]


def _date(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label}: expected an ISO date")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}: expected YYYY-MM-DD") from exc
    if result.isoformat() != value:
        raise ValueError(f"{label}: expected YYYY-MM-DD")
    return result


def validate_contract(contract, unit, metadata="source metadata"):
    if (
        not isinstance(contract, dict)
        or type(contract.get("schema_version")) is not int
        or contract["schema_version"] != 1
    ):
        raise ValueError(f"{metadata}: expected schema_version 1")
    json.dumps(contract, allow_nan=False)
    if not isinstance(contract.get("source_id"), str) or not contract["source_id"].strip():
        raise ValueError(f"{metadata}: source_id is required")
    digest = contract.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError(f"{metadata}: expected a lowercase sha256 digest")
    if contract.get("unit") != unit:
        raise ValueError(f"{metadata}: expected unit {unit}")
    if (
        unit == "discovery_events"
        and contract.get("event_definition") != "one_discovery_event_per_observation"
    ):
        raise ValueError(f"{metadata}: event_definition must be one_discovery_event_per_observation")
    scope = contract.get("scope")
    if not isinstance(scope, dict) or not isinstance(scope.get("filters"), dict) or not scope["filters"]:
        raise ValueError(f"{metadata}: scope.filters must explicitly describe the source filters")
    if not isinstance(scope.get("support_id"), str) or not scope["support_id"].strip():
        raise ValueError(f"{metadata}: scope.support_id is required")
    period = scope.get("period", {})
    if not isinstance(period, dict) or set(period) != {"start", "end"}:
        raise ValueError(f"{metadata}: scope.period needs start and end")
    if _date(period["start"], "period.start") > _date(period["end"], "period.end"):
        raise ValueError(f"{metadata}: period.start must not follow period.end")
    try:
        crs = CRS.from_user_input(contract.get("crs"))
    except Exception as exc:
        raise ValueError(f"{metadata}: invalid crs") from exc
    expected = CRS.from_epsg(4326) if unit == "discovery_events" else LAEA
    if crs != expected:
        raise ValueError(f"{metadata}: incorrect {unit} CRS, expected {expected}")
    if unit == "observations" and contract.get("grid") != GRID_CONTRACT:
        raise ValueError(f"{metadata}: grid must match the existing 5 km LAEA lattice")


def read_contract(path, metadata, unit):
    """Validate an explicit source declaration against the source's actual bytes."""
    data = Path(path).read_bytes()
    contract = json.loads(Path(metadata).read_text())
    validate_contract(contract, unit, metadata)
    if contract["sha256"] != hashlib.sha256(data).hexdigest():
        raise ValueError(f"{path}: sha256 mismatch")
    return data, contract


def _integer(value, label):
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{label}: expected a nonnegative integer") from exc
    if isinstance(value, bool) or str(result) != str(value) or not 0 <= result <= 2**53:
        raise ValueError(f"{label}: expected a nonnegative integer no larger than 2**53")
    return result


def read_counts(data):
    """Return known-support cells; absent rows remain unavailable."""
    reader = csv.DictReader(data.decode("utf-8-sig").splitlines())
    if not {"row", "col", "observation_count"}.issubset(reader.fieldnames or []):
        raise ValueError("count CSV requires row,col,observation_count")
    cells = {}
    for line, entry in enumerate(reader, 2):
        row, col, count = (
            _integer(entry[k], f"CSV line {line} {k}") for k in ("row", "col", "observation_count")
        )
        if row >= GRID.nrow or col >= GRID.ncol:
            raise ValueError(f"CSV line {line}: cell is outside the lattice")
        if (row, col) in cells:
            raise ValueError(f"CSV line {line}: duplicate support cell {(row, col)}")
        cells[row, col] = count
    if not cells:
        raise ValueError("count CSV declares no support")
    return cells


def point_cell(lon, lat):
    """Project a validated RFC 7946 point onto the half-open existing lattice."""
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (lon, lat)
    ) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("discovery point must have finite WGS84 longitude/latitude")
    x, y = transform("EPSG:4326", LAEA, [lon], [lat])
    if not math.isfinite(x[0]) or not math.isfinite(y[0]):
        raise ValueError("discovery point has no finite LAEA position")

    def cell_index(value):
        # PROJ round trips move exact edges by nanometres. Snap only within
        # 1e-10 cell widths (0.5 micrometres), then apply the half-open rule.
        nearest = round(value)
        return math.floor(nearest if abs(value - nearest) <= 1e-10 else value)

    row = cell_index((GRID.y1 - y[0]) / GRID.res)
    col = cell_index((x[0] - GRID.x0) / GRID.res)
    if not (0 <= row < GRID.nrow and 0 <= col < GRID.ncol):
        raise ValueError("discovery point is outside the lattice")
    return row, col


def count_discoveries(data, scope, support):
    geo = json.loads(data)
    if not isinstance(geo, dict) or geo.get("type") != "FeatureCollection" or "crs" in geo:
        raise ValueError("discoveries must be an RFC 7946 FeatureCollection without a crs member")
    if not isinstance(geo.get("features"), list):
        raise ValueError("discoveries.features must be a list")
    start, end = (_date(scope["period"][key], key) for key in ("start", "end"))
    seen, counts, duplicates = {}, {}, 0
    for index, feature in enumerate(geo["features"]):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError(f"discovery {index}: expected a Feature")
        props, geom = feature.get("properties"), feature.get("geometry")
        if not isinstance(props, dict) or not isinstance(geom, dict) or geom.get("type") != "Point":
            raise ValueError(f"discovery {index}: expected Point geometry and properties")
        identity = props.get("event_id")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError(f"discovery {index}: nonempty string event_id is required")
        observed = _date(props.get("observed_on"), f"discovery {identity}.observed_on")
        if not start <= observed <= end:
            raise ValueError(f"discovery {identity}: observed_on is outside the declared period")
        coordinates = geom.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ValueError(f"discovery {identity}: expected exactly longitude,latitude")
        cell = point_cell(*coordinates)
        if cell not in support:
            raise ValueError(f"discovery {identity}: outside declared observation support")
        signature = json.dumps([props, geom], sort_keys=True, allow_nan=False)
        if identity in seen:
            if seen[identity] != signature:
                raise ValueError(f"discovery {identity}: conflicting duplicate event_id")
            duplicates += 1
            continue
        seen[identity] = signature
        counts[cell] = counts.get(cell, 0) + 1
    for cell, count in counts.items():
        if count > support[cell]:
            raise ValueError(f"cell {cell}: discovery events exceed observation exposure")
    return counts, len(seen), duplicates


def tier_rows(discoveries, observations, factor):
    """Aggregate extensive counts first; never average rates or invent missing support."""
    parents = {}
    for (row, col), exposure in observations.items():
        parent = (row // factor, col // factor)
        accum = parents.setdefault(parent, [0, 0, 0])
        accum[0] += discoveries.get((row, col), 0)
        accum[1] += exposure
        accum[2] += 1
        if accum[0] > 2**53 or accum[1] > 2**53:
            raise ValueError("aggregated counts exceed exact JSON/raster integer range")
    return [
        [
            row,
            col,
            n,
            exposure,
            10000 * n / exposure if exposure and n_support == factor * factor else None,
            n_support,
            factor * factor,
        ]
        for (row, col), (n, exposure, n_support) in sorted(parents.items())
    ]


def build(discoveries_path, observations_path, discoveries_metadata, observations_metadata):
    numerator, numerator_contract = read_contract(discoveries_path, discoveries_metadata, "discovery_events")
    denominator, denominator_contract = read_contract(
        observations_path, observations_metadata, "observations"
    )
    if numerator_contract["scope"] != denominator_contract["scope"]:
        raise ValueError("discovery/observation scope mismatch: periods, filters and support must match")
    observations = read_counts(denominator)
    discoveries, unique, duplicates = count_discoveries(numerator, numerator_contract["scope"], observations)
    product = {
        "schema_version": 1,
        "metric": "discovery_events_per_10000_observations",
        "scope": numerator_contract["scope"],
        "crs": LAEA.to_string(),
        "sources": {"discoveries": numerator_contract, "observations": denominator_contract},
        "events": {"unique": unique, "duplicates_removed": duplicates},
        "fields": FIELDS,
        "tiers": {
            str(res): {
                "resolution_m": res,
                "grid": {"x0": GRID.x0, "y1": GRID.y1},
                "cells": tier_rows(discoveries, observations, res // 5000),
            }
            for res in (5000, 25000)
        },
    }
    validate_artifact(product)
    return product


def validate_artifact(product):
    """Validate a consumer artifact, including exact nesting and unavailable support.

    A 25 km rate requires all 25 explicitly supported children. Raw counts retain
    their measured footprint, described by support_children and expected_children.
    """
    if (
        not isinstance(product, dict)
        or type(product.get("schema_version")) is not int
        or product["schema_version"] != 1
    ):
        raise ValueError("discovery artifact: expected schema_version 1")
    if product.get("metric") != "discovery_events_per_10000_observations" or product.get("fields") != FIELDS:
        raise ValueError("discovery artifact: incorrect metric or fields")
    if product.get("crs") != LAEA.to_string():
        raise ValueError("discovery artifact: incorrect lattice CRS")
    sources = product.get("sources", {})
    if not isinstance(sources, dict) or set(sources) != {"discoveries", "observations"}:
        raise ValueError("discovery artifact: both source declarations are required")
    for name, unit in (("discoveries", "discovery_events"), ("observations", "observations")):
        validate_contract(sources[name], unit)
        if sources[name]["scope"] != product.get("scope"):
            raise ValueError("discovery artifact: source scope mismatch")
    tiers = product.get("tiers")
    if not isinstance(tiers, dict) or set(tiers) != {"5000", "25000"}:
        raise ValueError("discovery artifact: both lattice tiers are required")
    observations, discoveries = {}, {}
    for key, tier in tiers.items():
        res, seen = int(key), set()
        if (
            not isinstance(tier, dict)
            or tier.get("resolution_m") != res
            or tier.get("grid") != {"x0": GRID.x0, "y1": GRID.y1}
            or not isinstance(tier.get("cells"), list)
            or not tier["cells"]
        ):
            raise ValueError("discovery artifact: invalid tier metadata or empty cells")
        lattice = Lattice(res, LAEA)
        for entry in tier["cells"]:
            if not isinstance(entry, list) or len(entry) != len(FIELDS):
                raise ValueError("discovery artifact: invalid cell row")
            row, col, n, exposure, rate, support, expected = entry
            for value in (row, col, n, exposure, support, expected):
                if type(value) is not int:
                    raise ValueError("discovery artifact: counts and lattice indices must be integers")
                _integer(value, "discovery artifact cell")
            if (
                row >= lattice.nrow
                or col >= lattice.ncol
                or (row, col) in seen
                or n > exposure
                or expected != (res // 5000) ** 2
                or not 1 <= support <= expected
            ):
                raise ValueError("discovery artifact: invalid bounds, counts or support")
            seen.add((row, col))
            reference = 10000 * n / exposure if exposure and support == expected else None
            if reference is None:
                if rate is not None:
                    raise ValueError("discovery artifact: unsupported rate must be null")
            elif (
                isinstance(rate, bool)
                or not isinstance(rate, (int, float))
                or not math.isfinite(rate)
                or not math.isclose(rate, reference, rel_tol=1e-12, abs_tol=1e-12)
            ):
                raise ValueError("discovery artifact: rate disagrees with counts")
            if res == 5000:
                observations[row, col], discoveries[row, col] = exposure, n
    if tiers["25000"]["cells"] != tier_rows(discoveries, observations, 5):
        raise ValueError("discovery artifact: 25 km counts do not nest exactly over 5 km")
    events = product.get("events", {})
    if not isinstance(events, dict) or set(events) != {"unique", "duplicates_removed"}:
        raise ValueError("discovery artifact: event totals are required")
    for value in events.values():
        if type(value) is not int:
            raise ValueError("discovery artifact: event totals must be integers")
        _integer(value, "discovery artifact event total")
    if events["unique"] != sum(discoveries.values()):
        raise ValueError("discovery artifact: unique event count disagrees with cells")
    return product


def write_rasters(product, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for tier in product["tiers"].values():
        lattice = Lattice(tier["resolution_m"], LAEA)
        data = np.full((len(BANDS), *lattice.shape), np.nan, dtype="float64")
        for row, col, *values in tier["cells"]:
            data[:, row, col] = [np.nan if value is None else value for value in values]
        path = directory / f"discovery_{lattice.res}m.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=lattice.ncol,
            height=lattice.nrow,
            count=len(BANDS),
            dtype="float64",
            crs=LAEA,
            transform=lattice.transform,
            nodata=np.nan,
            compress="deflate",
            tiled=True,
        ) as dst:
            dst.write(data)
            for band, name in enumerate(BANDS, 1):
                dst.set_band_description(band, name)
            dst.update_tags(metric=product["metric"], scope=json.dumps(product["scope"], sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--discoveries", required=True, help="RFC 7946 Point GeoJSON")
    parser.add_argument("--observations", required=True, help="5 km count CSV, never a density raster")
    parser.add_argument("--discoveries-metadata", required=True)
    parser.add_argument("--observations-metadata", required=True)
    parser.add_argument("--out", required=True, help="output JSON, no production default")
    parser.add_argument("--raster-dir", help="optional lattice count/rate GeoTIFF directory")
    args = parser.parse_args(argv)
    try:
        product = build(
            args.discoveries, args.observations, args.discoveries_metadata, args.observations_metadata
        )
        if args.raster_dir:
            write_rasters(product, args.raster_dir)
        Path(args.out).write_text(
            json.dumps(product, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        )
    except (ValueError, OSError) as exc:
        parser.exit(2, f"discovery layer: {exc}\n")


if __name__ == "__main__":
    main()
