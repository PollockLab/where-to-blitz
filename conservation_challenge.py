"""Complete public Conservation Challenge snapshots and extent-first diversity selection.

The combined, publication-approved species list must be supplied explicitly. Snapshot
coordinates come only from open public iNaturalist observations. No fixture list ships.
"""

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

API = "https://api.inaturalist.org/v1/observations"
PER_PAGE = 200
PLACE_ID = 6712  # Canada
MAX_PAGES = 200
OMISSION_REASONS = {
    "blank_scientific_name",
    "inactive_source_taxon",
    "no_unique_exact_match",
    "incomplete_search",
    "broader_than_species",
    "rank_mismatch",
    "missing_species_ancestry",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def positive_int(value):
    return type(value) is int and value > 0


def iso_date(value):
    if not isinstance(value, str):
        raise ValueError("date must be YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date must be YYYY-MM-DD")
    return parsed


def validate_coverage(coverage, target_ids):
    """Account for every source row, retaining each omission and duplicate mapping."""
    if not isinstance(coverage, dict):
        raise ValueError("invalid challenge coverage")
    required = {
        "source_rows",
        "resolved_rows",
        "unique_targets",
        "omitted_rows",
        "original_source_sha256",
        "resolved",
        "omissions",
    }
    if set(coverage) != required:
        raise ValueError("challenge coverage requires complete source-row accounting")
    for key in ("source_rows", "resolved_rows", "unique_targets", "omitted_rows"):
        if type(coverage[key]) is not int or coverage[key] < 0:
            raise ValueError("challenge coverage counts must be nonnegative integers")
    fingerprint = coverage["original_source_sha256"]
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("challenge coverage requires the original source SHA256")
    resolved, omitted = coverage["resolved"], coverage["omissions"]
    if (
        not isinstance(resolved, list)
        or not isinstance(omitted, list)
        or len(resolved) != coverage["resolved_rows"]
        or len(omitted) != coverage["omitted_rows"]
        or coverage["source_rows"] != len(resolved) + len(omitted)
    ):
        raise ValueError("challenge coverage counts disagree with source rows")
    seen_rows, resolved_ids = set(), set()
    for row in resolved:
        if (
            not isinstance(row, dict)
            or set(row) != {"csv_row", "target_id"}
            or not positive_int(row["csv_row"])
            or not positive_int(row["target_id"])
        ):
            raise ValueError("invalid resolved challenge source row")
        if row["csv_row"] in seen_rows:
            raise ValueError("duplicate challenge source row")
        seen_rows.add(row["csv_row"])
        resolved_ids.add(row["target_id"])
    for row in omitted:
        if (
            not isinstance(row, dict)
            or set(row) != {"csv_row", "raw_scientific_name", "common_name", "reason_code", "reason"}
            or not positive_int(row["csv_row"])
            or any(not isinstance(row[key], str) for key in ("raw_scientific_name", "common_name", "reason"))
            or not row["reason"].strip()
            or not isinstance(row["reason_code"], str)
            or row["reason_code"] not in OMISSION_REASONS
        ):
            raise ValueError("invalid omitted challenge source row")
        if row["csv_row"] in seen_rows:
            raise ValueError("duplicate challenge source row")
        seen_rows.add(row["csv_row"])
    if (
        seen_rows != set(range(2, coverage["source_rows"] + 2))
        or len(resolved_ids) != coverage["unique_targets"]
        or resolved_ids != set(target_ids)
    ):
        raise ValueError("challenge coverage does not match the exact approved targets or all source rows")
    return coverage


def validate_list(config):
    """Require a versioned, traceable, explicitly approved combined species list."""
    if (
        not isinstance(config, dict)
        or type(config.get("schema_version")) is not int
        or config["schema_version"] != 1
    ):
        raise ValueError("unsupported taxon-list schema")
    source = config.get("source", {})
    checksum = source.get("sha256", "") if isinstance(source, dict) else ""
    source_url = source.get("url", "") if isinstance(source, dict) else ""
    if (
        not isinstance(config.get("version"), str)
        or not config["version"].strip()
        or config.get("approved_for_publication") is not True
        or not isinstance(source_url, str)
        or urlparse(source_url).scheme != "https"
        or not urlparse(source_url).netloc
        or not isinstance(checksum, str)
        or len(checksum) != 64
        or any(c not in "0123456789abcdef" for c in checksum)
    ):
        raise ValueError("taxon list requires version, HTTPS source, source SHA256 and publication approval")
    taxa = config.get("taxa")
    if not isinstance(taxa, list) or not taxa:
        raise ValueError("combined species list must not be empty")
    species = {}
    for taxon in taxa:
        if (
            not isinstance(taxon, dict)
            or not positive_int(taxon.get("id"))
            or not isinstance(taxon.get("name"), str)
            or not taxon["name"].strip()
            or taxon.get("rank") not in ("species", "subspecies", "variety", "form")
            or taxon["id"] in species
        ):
            raise ValueError("taxon list has an invalid or duplicate species")
        species[taxon["id"]] = taxon["name"]
    species_groups(config)
    if "coverage" in config:
        validate_coverage(config["coverage"], species)
    return species


def species_groups(config):
    """Keep listed infraspecies as exact targets, with verified species grouping metadata."""
    groups, names = {}, {}
    for target in config["taxa"]:
        if target["rank"] == "species":
            group = {"id": target["id"], "name": target["name"]}
            if "species" in target and target["species"] != group:
                raise ValueError("species target has conflicting species grouping metadata")
        else:
            group = target.get("species")
            if (
                not isinstance(group, dict)
                or not positive_int(group.get("id"))
                or group["id"] == target["id"]
                or not isinstance(group.get("name"), str)
                or not group["name"].strip()
            ):
                raise ValueError("infraspecies target requires verified species ID and name")
        if group["id"] in names and names[group["id"]] != group["name"]:
            raise ValueError("conflicting scientific names for one species grouping")
        names[group["id"]] = group["name"]
        groups[target["id"]] = group
    return groups


def query_filters(config, as_of):
    species = validate_list(config)
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise ValueError("as_of must be timezone-aware UTC")
    end = as_of.date()
    start = end - timedelta(days=13)
    return {
        "place_id": PLACE_ID,
        "taxon_id": ",".join(map(str, sorted(species))),
        "d1": start.isoformat(),
        "d2": end.isoformat(),
        "created_d2": as_of.isoformat(),
        "geoprivacy": "open",
        "taxon_geoprivacy": "open",
        "geo": "true",
        "order_by": "id",
        "order": "asc",
        "per_page": PER_PAGE,
    }


def observation_time(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid observation timestamp")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("observation timestamp must include timezone")
    return timestamp.astimezone(timezone.utc).isoformat()


def public_observation(row, species, start, end, groups=None):
    """Validate eligibility again locally, retaining only public, open coordinates."""
    if not isinstance(row, dict) or not positive_int(row.get("id")):
        raise ValueError("invalid observation ID")
    observed = iso_date(row.get("observed_on"))
    if not start <= observed <= end:
        raise ValueError("observation outside requested window")
    taxon = row.get("taxon")
    if not isinstance(taxon, dict) or not positive_int(taxon.get("id")):
        raise ValueError("observation missing taxon")
    ancestors = taxon.get("ancestor_ids", [])
    if not isinstance(ancestors, list) or any(not positive_int(v) for v in ancestors):
        raise ValueError("invalid taxon ancestry")
    matches = set([taxon["id"], *ancestors]) & species.keys()
    if not matches:
        raise ValueError("observation identification does not match a listed target")
    groups = groups or {target: {"id": target, "name": name} for target, name in species.items()}
    species_ids = {groups[target]["id"] for target in matches}
    if len(species_ids) != 1:
        raise ValueError("listed targets disagree on the observation's species grouping")
    species_id = species_ids.pop()
    if species_id not in {taxon["id"], *ancestors}:
        raise ValueError("observation identification lacks the configured species ancestor")
    species_name = groups[min(matches)]["name"]
    places = row.get("place_ids")
    if not isinstance(places, list) or PLACE_ID not in places:
        raise ValueError("observation is not in Canada")
    # A public API coordinate can be randomized for obscured records. Never recover
    # private coordinates or expose records with either source privacy restriction.
    if (
        not {"obscured", "geoprivacy", "taxon_geoprivacy"}.issubset(row)
        or row.get("obscured") is not False
        or row.get("geoprivacy") not in (None, "open")
        or row.get("taxon_geoprivacy") not in (None, "open")
    ):
        return None
    point = row.get("geojson")
    coords = point.get("coordinates") if isinstance(point, dict) else None
    if (
        not isinstance(point, dict)
        or point.get("type") != "Point"
        or not isinstance(coords, list)
        or len(coords) != 2
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in coords)
        or not -180 <= coords[0] <= 180
        or not -90 <= coords[1] <= 90
    ):
        raise ValueError("invalid public point")
    return {
        "id": row["id"],
        "taxon_id": species_id,
        "name": species_name,
        "target_ids": sorted(matches),
        "identified_taxon_id": taxon["id"],
        "observed_on": observed.isoformat(),
        "observed_at": observation_time(row.get("time_observed_at")),
        "longitude": coords[0],
        "latitude": coords[1],
    }


def enumerate_observations(fetch, filters, species, max_pages=MAX_PAGES, groups=None):
    """Use ascending ID cursors, not the API's bounded offset pagination."""
    if not positive_int(max_pages) or max_pages > MAX_PAGES:
        raise ValueError("page budget must be 1..200")
    cursor, total, seen, rows, evidence = 0, None, {}, [], []
    start, end = iso_date(filters["d1"]), iso_date(filters["d2"])
    for _ in range(max_pages):
        url = requests.Request("GET", API, params={**filters, "id_above": cursor}).prepare().url
        data = fetch(url)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("invalid observation page")
        actual = data.get("total_results")
        if type(actual) is not int or actual < 0 or data.get("page") != 1 or data.get("per_page") != PER_PAGE:
            raise ValueError("invalid pagination metadata")
        if total is None:
            total = actual
        if actual != total - len(seen) or len(data["results"]) != min(PER_PAGE, actual):
            raise ValueError("incomplete or changing observation enumeration")
        evidence.append({"url": url, "sha256": digest(data), "total_results": actual})
        if actual == 0:
            return rows, seen, evidence
        for row in data["results"]:
            item = public_observation(row, species, start, end, groups)
            obs_id = row["id"]
            if obs_id <= cursor or obs_id in seen:
                raise ValueError("duplicate or unordered observation ID")
            cursor = obs_id
            seen[obs_id] = digest(row)
            if item is not None:
                rows.append(item)
    raise ValueError("complete observation enumeration exceeds page budget")


def acquire(config, fetch, as_of):
    """Publish only two agreeing complete enumerations, including valid empty windows."""
    species = validate_list(config)
    filters = query_filters(config, as_of)
    groups = species_groups(config)
    first, hashes, evidence = enumerate_observations(fetch, filters, species, groups=groups)
    second, repeated, confirmation = enumerate_observations(fetch, filters, species, groups=groups)
    if hashes != repeated or first != second:
        raise ValueError("observations changed between complete enumerations")
    snapshot = {
        "schema_version": 1,
        "as_of": as_of.isoformat(),
        "window": {"start": filters["d1"], "end": filters["d2"]},
        "source": {
            "url": config["source"]["url"],
            "sha256": config["source"]["sha256"],
            "version": config["version"],
            "config_sha256": digest(config),
        },
        "filters": filters,
        "complete": True,
        "observations": first,
        "manifest": {
            "passes": [evidence, confirmation],
            "source_count": len(hashes),
            "eligible_count": len(first),
        },
    }
    if "coverage" in config:
        snapshot["coverage"] = json.loads(json.dumps(config["coverage"]))
    return validate_snapshot(snapshot)


def validate_snapshot(snapshot):
    """Validate a downloaded compact feed before it can enter a public site build."""
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != 1
        or snapshot.get("complete") is not True
    ):
        raise ValueError("incomplete or unsupported snapshot")
    try:
        as_of = datetime.fromisoformat(snapshot["as_of"])
        start, end = iso_date(snapshot["window"]["start"]), iso_date(snapshot["window"]["end"])
        source = snapshot["source"]
        rows = snapshot["observations"]
        manifest = snapshot["manifest"]
        if (
            as_of.tzinfo is None
            or as_of.utcoffset() != timedelta(0)
            or end != as_of.date()
            or end - start != timedelta(days=13)
            or not isinstance(source["version"], str)
            or not source["version"]
            or not isinstance(rows, list)
            or type(manifest["eligible_count"]) is not int
            or manifest["eligible_count"] != len(rows)
            or type(manifest["source_count"]) is not int
            or manifest["source_count"] < len(rows)
            or len(manifest["passes"]) != 2
            or not all(manifest["passes"])
        ):
            raise ValueError("invalid snapshot metadata")
        for pages in manifest["passes"]:
            if not isinstance(pages, list) or not pages:
                raise ValueError("invalid enumeration manifest")
            remaining = manifest["source_count"]
            for index, page in enumerate(pages):
                checksum = page["sha256"]
                if (
                    page["total_results"] != remaining
                    or not isinstance(checksum, str)
                    or len(checksum) != 64
                    or any(c not in "0123456789abcdef" for c in checksum)
                    or not page["url"].startswith(API + "?")
                    or (remaining == 0 and index != len(pages) - 1)
                ):
                    raise ValueError("invalid enumeration manifest page")
                remaining -= min(PER_PAGE, remaining)
            if pages[-1]["total_results"] != 0:
                raise ValueError("enumeration manifest lacks a terminal empty page")
        for field in ("sha256", "config_sha256"):
            value = source[field]
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("invalid source hash")
        if urlparse(source["url"]).scheme != "https" or not urlparse(source["url"]).netloc:
            raise ValueError("invalid source URL")
        filters = snapshot["filters"]
        if "coverage" in snapshot:
            targets = filters.get("taxon_id", "").split(",")
            if not targets or any(not re.fullmatch(r"[1-9][0-9]*", value) for value in targets):
                raise ValueError("invalid covered challenge target filter")
            validate_coverage(snapshot["coverage"], [int(value) for value in targets])
        if (
            filters["place_id"] != PLACE_ID
            or filters["geoprivacy"] != "open"
            or filters["taxon_geoprivacy"] != "open"
            or filters["d1"] != start.isoformat()
            or filters["d2"] != end.isoformat()
            or filters["created_d2"] != snapshot["as_of"]
        ):
            raise ValueError("invalid snapshot filters")
        seen = set()
        for row in rows:
            if (
                not positive_int(row["id"])
                or row["id"] in seen
                or not positive_int(row["taxon_id"])
                or not isinstance(row["name"], str)
                or not row["name"]
                or not start <= iso_date(row["observed_on"]) <= end
                or any(
                    type(row[k]) not in (int, float) or not math.isfinite(row[k])
                    for k in ("longitude", "latitude")
                )
                or not -180 <= row["longitude"] <= 180
                or not -90 <= row["latitude"] <= 90
            ):
                raise ValueError("invalid snapshot observation")
            target_ids = row.get("target_ids", [row["taxon_id"]])
            if (
                not isinstance(target_ids, list)
                or not target_ids
                or any(not positive_int(target) for target in target_ids)
                or target_ids != sorted(set(target_ids))
                or not positive_int(row.get("identified_taxon_id", row["taxon_id"]))
            ):
                raise ValueError("invalid observation target identity")
            if row.get("observed_at") and datetime.fromisoformat(row["observed_at"]) > as_of:
                raise ValueError("observation timestamp is after snapshot as_of")
            if observation_time(row.get("observed_at")) != row.get("observed_at"):
                raise ValueError("snapshot timestamp must use UTC")
            seen.add(row["id"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("malformed snapshot") from exc
    return snapshot


def select_observations(snapshot, bounds=None, national=False):
    """Filter the full snapshot to [west,south,east,north], then diversity rounds."""
    if snapshot.get("schema_version") != 1 or snapshot.get("complete") is not True:
        raise ValueError("incomplete or unsupported snapshot")
    if bounds is not None:
        if (
            len(bounds) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds)
            or not -90 <= bounds[1] <= bounds[3] <= 90
            or any(not -180 <= bounds[i] <= 180 for i in (0, 2))
        ):
            raise ValueError("invalid viewport bounds")
        west, south, east, north = bounds
    groups = defaultdict(list)
    for row in snapshot["observations"]:
        if bounds is not None:
            lon, lat = row["longitude"], row["latitude"]
            inside = west <= lon <= east if west <= east else lon >= west or lon <= east
            if not inside or not south <= lat <= north:
                continue
        groups[row["taxon_id"]].append(row)

    def key(row):
        # Date-only records are ordered at UTC midnight of their stated date;
        # retain null in the artifact rather than inventing a recorded time.
        timestamp = row.get("observed_at") or row["observed_on"] + "T00:00:00+00:00"
        return (-datetime.fromisoformat(timestamp).timestamp(), -row["id"])

    for group in groups.values():
        group.sort(key=key)
    result, round_index, limit = [], 0, 500 if national else 150
    while len(result) < limit:
        candidates = sorted((g[round_index] for g in groups.values() if len(g) > round_index), key=key)
        if not candidates:
            break
        result.extend(candidates[: limit - len(result)])
        round_index += 1
    return result


class PublicFetcher:
    """Public requests only, bounded retries, timeouts and total request budget."""

    def __init__(self, session=None, sleep=time.sleep, max_requests=1200):
        self.session = session or requests.Session()
        self.sleep = sleep
        self.remaining = max_requests

    def __call__(self, url):
        for attempt in range(3):
            if self.remaining <= 0:
                raise ValueError("request budget exhausted")
            self.remaining -= 1
            self.sleep(1)
            try:
                response = self.session.get(
                    url,
                    timeout=(10, 30),
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "where-to-blitz-conservation-challenge/1.0",
                    },
                )
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    self.sleep(2**attempt)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException:
                if attempt == 2:
                    raise
                self.sleep(2**attempt)
        raise ValueError("request retries exhausted")


def write_snapshot(path, snapshot):
    """Replace the last good file only after acquisition and serialization succeed."""
    validate_snapshot(snapshot)
    path = Path(path)
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None, fetch=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxa", required=True, type=Path, help="approved combined species-list JSON")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--as-of", help="timezone-aware UTC timestamp, defaults to now")
    args = parser.parse_args(argv)
    config = json.loads(args.taxa.read_text())
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
    snapshot = acquire(config, fetch if fetch is not None else PublicFetcher(), as_of)
    write_snapshot(args.output, snapshot)
    return snapshot


if __name__ == "__main__":
    main()
