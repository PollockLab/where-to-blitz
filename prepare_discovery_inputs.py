"""Prepare private annual discovery inputs from the supplied lab shapefile and Parquet.

Requires GDAL's ogr2ogr and optional pyarrow. No raw observer fields leave this
process. Every supplied point must have a distinct compatible research-grade row
in the declared year. Individual observation dates/IDs are not reconstructed.
The output describes this supplied point set, not every new taxon in the Parquet.
Nothing here publishes the private inputs or activates a production layer.
"""

import argparse
import csv
import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np
from rasterio.crs import CRS
from rasterio.warp import transform

from build_discovery_layer import GRID, GRID_CONTRACT, LAEA, build

PROVINCES = {
    "Alberta",
    "British Columbia",
    "Manitoba",
    "New Brunswick",
    "Newfoundland and Labrador",
    "Northwest Territories",
    "Nova Scotia",
    "Nunavut",
    "Ontario",
    "Prince Edward Island",
    "Québec",
    "Quebec",
    "Saskatchewan",
    "Yukon",
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def point_checksums(path):
    return {suffix: sha256(Path(path).with_suffix(suffix)) for suffix in (".shp", ".shx", ".dbf", ".prj")}


def read_lab_points(path):
    path = Path(path)
    components = point_checksums(path)
    if CRS.from_wkt(path.with_suffix(".prj").read_text()) != CRS.from_epsg(3347):
        raise ValueError("lab points must use the supplied EPSG:3347 source CRS")
    # The source's original projection used the numeric geographic coordinates
    # without a NAD83/WGS84 offset. Inverting only that projection permits an
    # exact identity match to the frozen raw rows. Actual output geometry comes
    # from those matched raw coordinates, not a guessed datum transformation.
    process = subprocess.run(
        ["ogr2ogr", "-f", "GeoJSON", "/vsistdout/", str(path), "-t_srs", "EPSG:4269"],
        capture_output=True,
        text=True,
        check=True,
    )
    features = json.loads(process.stdout)["features"]
    if not features:
        raise ValueError("lab point set is empty")
    points = []
    for feature in features:
        geometry, properties = feature.get("geometry"), feature.get("properties", {})
        if not geometry or geometry.get("type") != "Point":
            raise ValueError("every supplied feature must be a point")
        key = properties.get("scntfc_"), properties.get("usr_lgn")
        coords = geometry.get("coordinates")
        if (
            any(not isinstance(value, str) or not value for value in key)
            or not isinstance(coords, list)
            or len(coords) != 2
        ):
            raise ValueError("lab points need scientific name, observer and two coordinates")
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in coords):
            raise ValueError("lab point has invalid coordinates")
        points.append({"key": key, "longitude": coords[0], "latitude": coords[1]})
    return points, components


def distance_metres(first, second):
    a, b, c, d = map(math.radians, (*first, *second))
    return (
        6371008.8
        * 2
        * math.asin(
            min(
                1,
                math.sqrt(
                    math.sin((d - b) / 2) ** 2 + math.cos(b) * math.cos(d) * math.sin((c - a) / 2) ** 2
                ),
            )
        )
    )


def assign_points(points, candidates, year):
    """Prove a full distinct-row matching; reject ambiguity in year, grade or cell."""
    by_key = defaultdict(list)
    for row in candidates:
        by_key[row["scientific_name"], row["user_login"]].append(row)
    compatible = {}
    for index, point in enumerate(points):
        rows = [
            row
            for row in by_key[point["key"]]
            if distance_metres((point["longitude"], point["latitude"]), (row["longitude"], row["latitude"]))
            <= 0.001
        ]
        if not rows:
            raise ValueError(f"lab feature {index}: no sub-millimetre source-row match")
        if any(row["year"] != str(year) or row["quality_grade"] != "research" for row in rows):
            raise ValueError(f"lab feature {index}: incompatible annual observation cohort")
        cells = {(row["row"], row["col"]) for row in rows}
        if len(cells) != 1:
            raise ValueError(f"lab feature {index}: ambiguous raw coordinates cross a lattice edge")
        compatible[index] = sorted(rows, key=lambda row: row["source_row_index"])
    assigned = {}

    def visit(index, seen):
        for row in compatible[index]:
            identity = row["source_row_index"]
            if identity in seen:
                continue
            seen.add(identity)
            if identity not in assigned or visit(assigned[identity], seen):
                assigned[identity] = index
                return True
        return False

    if not all(visit(index, set()) for index in sorted(compatible, key=lambda index: len(compatible[index]))):
        raise ValueError("supplied points cannot match distinct raw observation rows")
    result = {}
    for identity, index in assigned.items():
        result[index] = next(row for row in compatible[index] if row["source_row_index"] == identity)
    return result, sum(len(rows) > 1 for rows in compatible.values())


def supported_counts(counts):
    """Whole occupied 25 km parents, including their explicitly known zero children."""
    occupied = np.argwhere(counts > 0)
    parents = sorted({(int(row) // 5, int(col) // 5) for row, col in occupied})
    for row, col in parents:
        for dr in range(5):
            for dc in range(5):
                r, c = 5 * row + dr, 5 * col + dc
                yield r, c, int(counts[r, c])


def scan_observations(path, points, year):
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ValueError("preparing private inputs requires optional pyarrow") from exc
    fields = [
        "scientific_name",
        "user_login",
        "longitude",
        "latitude",
        "year",
        "month",
        "day",
        "quality_grade",
        "coordinates_obscured",
        "place_state_name",
    ]
    parquet = pq.ParquetFile(path)
    if not set(fields).issubset(parquet.schema.names):
        raise ValueError("raw Parquet lacks the required observation fields")
    names = pa.array(sorted({point["key"][0] for point in points}))
    users = pa.array(sorted({point["key"][1] for point in points}))
    counts = np.zeros(GRID.shape, dtype=np.int64)
    candidates, states, privacy = [], Counter(), Counter()
    offset, annual, incomplete_days, outside_lattice = 0, 0, 0, 0
    for batch in parquet.iter_batches(batch_size=65536, columns=fields):
        table = pa.Table.from_batches([batch]).append_column(
            "source_row_index", pa.array(range(offset, offset + len(batch)))
        )
        offset += len(batch)
        research = table.filter(pc.equal(table["quality_grade"], "research"))
        years = research["year"].to_pylist()
        if any(
            not isinstance(value, str)
            or len(value) != 4
            or not value.isascii()
            or not value.isdigit()
            or value == "0000"
            for value in years
        ):
            raise ValueError("research rows with unparseable years prevent a complete annual exposure")
        selected = research.filter(pc.equal(research["year"], str(year)))
        if not len(selected):
            continue
        annual += len(selected)
        states.update(selected["place_state_name"].to_pylist())
        privacy.update(selected["coordinates_obscured"].to_pylist())
        if set(states) - PROVINCES - {None}:
            raise ValueError("Canada export contains a non-Canadian provincial label")
        if set(privacy) - {True, False}:
            raise ValueError("raw coordinate-obscuration evidence is missing")
        for month, day in zip(selected["month"].to_pylist(), selected["day"].to_pylist()):
            try:
                date(year, int(month), int(day))
            except (ValueError, TypeError):
                incomplete_days += 1  # Annual year is explicit, never fabricate a day.
        lon = np.asarray(selected["longitude"].to_numpy(), dtype=float)
        lat = np.asarray(selected["latitude"].to_numpy(), dtype=float)
        if not np.all(
            np.isfinite(lon) & np.isfinite(lat) & (lon >= -142) & (lon <= -52) & (lat >= 41) & (lat <= 84)
        ):
            raise ValueError(
                "research-year coordinates are missing or outside the Canada export's expected bounds"
            )
        x, y = transform("EPSG:4326", LAEA, lon.tolist(), lat.tolist())
        ri, ci = (GRID.y1 - np.asarray(y)) / 5000, (np.asarray(x) - GRID.x0) / 5000
        row = np.floor(np.where(np.abs(ri - np.rint(ri)) <= 1e-10, np.rint(ri), ri)).astype(int)
        col = np.floor(np.where(np.abs(ci - np.rint(ci)) <= 1e-10, np.rint(ci), ci)).astype(int)
        on_grid = (row >= 0) & (row < GRID.nrow) & (col >= 0) & (col < GRID.ncol)
        outside_lattice += int((~on_grid).sum())
        selected = selected.filter(pa.array(on_grid))
        row, col = row[on_grid], col[on_grid]
        np.add.at(counts, (row, col), 1)
        selected = selected.append_column("row", pa.array(row)).append_column("col", pa.array(col))
        matching = pc.and_(
            pc.is_in(selected["scientific_name"], value_set=names),
            pc.is_in(selected["user_login"], value_set=users),
        )
        candidates.extend(selected.filter(matching).to_pylist())
    if annual == 0:
        raise ValueError("raw source has no research-grade observations in the requested year")
    return (
        counts,
        candidates,
        {
            "raw_rows": offset,
            "research_year_observations": annual,
            "eligible_observations": int(counts.sum()),
            "outside_existing_lattice": outside_lattice,
            "incomplete_days_with_known_year": incomplete_days,
            "province_counts": {"unassigned" if key is None else key: value for key, value in states.items()},
            "coordinate_obscuration_counts": {str(key): value for key, value in privacy.items()},
        },
    )


def prepare(points_path, observations_path, directory, year):
    raw_hash = sha256(observations_path)
    points, components = read_lab_points(points_path)
    counts, candidates, audit = scan_observations(observations_path, points, year)
    assigned, ambiguous = assign_points(points, candidates, year)
    bundle_hash = hashlib.sha256(json.dumps(components, sort_keys=True).encode()).hexdigest()
    if sha256(observations_path) != raw_hash:
        raise ValueError("observations source changed during preparation")
    if point_checksums(points_path) != components:
        raise ValueError("lab point source changed during preparation")
    scope = {
        "period": {"start": f"{year}-01-01", "end": f"{year}-12-31"},
        "filters": {
            "country": "CA, lab Canada export",
            "quality_grade": "research",
            "taxon": "all",
            "taxon_group": "All biodiversity",
            "discovery_points": "lab-supplied point set; original selection unknown",
            "geographic_support": "existing national LAEA lattice footprint",
            "coordinates": "finite reported coordinates, including source-obscured records",
        },
        "support_id": f"occupied-25km-parents-with-all-25-children@1:{raw_hash}:{year}",
    }
    features = [
        {
            "type": "Feature",
            "properties": {"event_id": f"lab:{bundle_hash}:{index}", "observed_year": year},
            "geometry": {
                "type": "Point",
                "coordinates": [assigned[index]["longitude"], assigned[index]["latitude"]],
            },
        }
        for index in range(len(points))
    ]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    numerator, denominator = directory / "discovery-events.geojson", directory / "observation-counts.csv"
    numerator.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"))
    )
    with denominator.open("w") as stream:
        writer = csv.writer(stream)
        writer.writerow(["row", "col", "observation_count"])
        writer.writerows(supported_counts(counts))
    paths = []
    for source, unit in ((numerator, "discovery_events"), (denominator, "observations")):
        contract = {
            "schema_version": 1,
            "source_id": source.name,
            "sha256": sha256(source),
            "unit": unit,
            "scope": scope,
            "crs": "EPSG:4326" if unit == "discovery_events" else LAEA.to_string(),
            "original_sources": {
                "lab_shapefile_components": components,
                "observations_parquet_sha256": raw_hash,
            },
        }
        if unit == "discovery_events":
            contract.update(
                event_definition="one_discovery_event_per_observation",
                event_dates="annual",
                date_precision="year",
                year=year,
                estimand="the supplied lab discovery-point set, not every newly logged taxon",
                identity="source bundle checksum and feature index; not an iNaturalist observation ID",
            )
        else:
            contract["grid"] = GRID_CONTRACT
        metadata = source.with_suffix(".metadata.json")
        metadata.write_text(json.dumps(contract, sort_keys=True, indent=2))
        paths.append(metadata)
    product = build(numerator, denominator, *paths)
    artifact = directory / "discovery_grid.json"
    artifact.write_text(json.dumps(product, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
    audit.update(
        source_features=len(points),
        matched_distinct_rows=len(assigned),
        ambiguous_daily_identities=ambiguous,
        source_sha256=raw_hash,
        shapefile_components=components,
        source_event_definition="count supplied points; each has a distinct compatible research-grade row in "
        "the declared year; original point selection unknown",
        country_evidence="lab Canada export and Canadian non-null province labels; unassigned labels retained",
        artifact_sha256=sha256(artifact),
        artifact_bytes=artifact.stat().st_size,
        producer_sha256=sha256(__file__),
    )
    (directory / "preparation_audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True))
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=Path, required=True, help="private supplied EPSG:3347 .shp")
    parser.add_argument("--observations", type=Path, required=True, help="private frozen lab Canada Parquet")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument(
        "--out-dir", type=Path, required=True, help="private output directory, no production default"
    )
    args = parser.parse_args()
    if not 1000 <= args.year <= 9999:
        parser.error("year must have four digits")
    print(
        json.dumps(prepare(args.points, args.observations, args.out_dir, args.year), sort_keys=True, indent=2)
    )


if __name__ == "__main__":
    main()
