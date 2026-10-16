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
TAXON_API = "https://api.inaturalist.org/v1/taxa/"
PER_PAGE = 200
PLACE_ID = 6712  # Canada
MAX_PAGES = 200
PHOTO_HOSTS = {"static.inaturalist.org", "inaturalist-open-data.s3.amazonaws.com"}
PHOTO_LICENSES = {"cc0", "cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa", "cc-by-nd", "cc-by-nc-nd"}
OMISSION_REASONS = {
    "blank_scientific_name",
    "inactive_source_taxon",
    "no_unique_exact_match",
    "incomplete_search",
    "broader_than_species",
    "rank_mismatch",
    "missing_species_ancestry",
    "taxonomic_concept_conflict",
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
            or taxon.get("rank") not in ("species", "subspecies", "variety", "form", "genus")
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
        if target["rank"] == "genus":
            if "species" in target or len(target["name"].split()) != 1:
                raise ValueError("genus targets require one genus name and no species grouping")
            groups[target["id"]] = None
            continue
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


def context_text(value, field, limit=300):
    """Bound display text without interpreting source strings as markup."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"invalid observation {field}")
    return value


def validate_context(row):
    """Accept legacy records, but validate every supplied optional display field."""
    for field in ("common_name", "identified_name", "place_name"):
        if field in row:
            context_text(row[field], field)
    if "photo" not in row:
        return
    photo = row["photo"]
    if not isinstance(photo, dict) or set(photo) != {"url", "attribution", "license_code", "alt"}:
        raise ValueError("invalid observation photo metadata")
    context_text(photo["attribution"], "photo attribution", 1000)
    context_text(photo["alt"], "photo alt")
    if not isinstance(photo["license_code"], str) or photo["license_code"] not in PHOTO_LICENSES:
        raise ValueError("invalid observation photo license")
    context_text(photo["url"], "photo URL")
    parsed = urlparse(photo["url"])
    if (
        parsed.scheme != "https"
        or parsed.netloc not in PHOTO_HOSTS
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/photos/[1-9][0-9]*/(?:square|small|medium)\.(?i:jpg|jpeg|png)", parsed.path)
    ):
        raise ValueError("invalid observation photo URL")


def public_context(row, taxon):
    """Keep public display context only after coordinate privacy has been checked."""
    result = {}
    for field, value in (
        ("identified_name", taxon.get("name")),
        ("common_name", taxon.get("preferred_common_name")),
        ("place_name", row.get("place_guess")),
    ):
        if value is not None and value != "":
            result[field] = context_text(value, field).strip()
    photo = taxon.get("default_photo")
    # Unlicensed/all-rights-reserved photos are not copied into the public feed.
    if (
        isinstance(photo, dict)
        and isinstance(photo.get("license_code"), str)
        and photo["license_code"] in PHOTO_LICENSES
        and result.get("identified_name")
    ):
        result["photo"] = {
            "url": photo.get("medium_url") or photo.get("square_url") or photo.get("url"),
            "attribution": photo.get("attribution"),
            "license_code": photo["license_code"],
            "alt": "Reference photo of " + result["identified_name"],
        }
    validate_context(result)
    return result


def taxonomy_identity(value):
    """Allowlist public ranked taxonomy identities, never arbitrary API fields."""
    if not isinstance(value, dict) or not positive_int(value.get("id")) or value.get("is_active") is not True:
        raise ValueError("taxonomy evidence requires active ranked identities")
    return {
        "id": value["id"],
        "name": context_text(value.get("name"), "taxon name"),
        "rank": context_text(value.get("rank"), "taxon rank", 50),
        "is_active": True,
    }


def taxonomy_species(fact):
    """A species is itself; a finer identification needs one ranked species ancestor."""
    lineage = [fact, *fact["ancestors"]]
    species = [item for item in lineage if item["rank"] == "species"]
    finer = fact["rank"] in {"subspecies", "variety", "subvariety", "form", "subform"}
    if len(species) > 1 or finer and len(species) != 1 or species and fact["rank"] != "species" and not finer:
        raise ValueError("taxonomy evidence has conflicting species ancestors")
    if not species:
        return None
    return {key: species[0][key] for key in ("id", "name")}


def validate_taxonomy(facts):
    if not isinstance(facts, list):
        raise ValueError("snapshot taxonomy must be a list")
    result, identities = {}, {}
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != {
            "id",
            "name",
            "rank",
            "is_active",
            "ancestors",
            "lookup",
        }:
            raise ValueError("invalid snapshot taxonomy evidence")
        taxonomy_identity(fact)
        ancestors = fact["ancestors"]
        if not isinstance(ancestors, list) or len(ancestors) > 50:
            raise ValueError("invalid ranked taxonomy ancestors")
        ids = [fact["id"]]
        for ancestor in ancestors:
            if taxonomy_identity(ancestor) != ancestor:
                raise ValueError("taxonomy ancestors contain nonallowlisted fields")
            ids.append(ancestor["id"])
        for item in [fact, *ancestors]:
            identity = (item["name"], item["rank"])
            if item["id"] in identities and identities[item["id"]] != identity:
                raise ValueError("taxonomy evidence disagrees on one shared identity")
            identities[item["id"]] = identity
        lookup = fact["lookup"]
        if (
            len(set(ids)) != len(ids)
            or fact["id"] in result
            or not isinstance(lookup, dict)
            or set(lookup) != {"url", "sha256"}
            or lookup["url"] != TAXON_API + str(fact["id"])
            or not isinstance(lookup["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", lookup["sha256"])
        ):
            raise ValueError("invalid taxonomy identity or lookup provenance")
        taxonomy_species(fact)
        result[fact["id"]] = fact
    if list(result) != sorted(result):
        raise ValueError("snapshot taxonomy must use deterministic ID ordering")
    return result


def validate_target_mapping(snapshot, config):
    """Bind exact approved targets and dynamic genus descendants to species identities."""
    groups = species_groups(config)
    genera = {target["id"]: target for target in config["taxa"] if target["rank"] == "genus"}
    if genera and "taxonomy" not in snapshot:
        raise ValueError("genus-target snapshots require ranked taxonomy evidence")
    facts = validate_taxonomy(snapshot.get("taxonomy", []))
    used = set()
    for row in snapshot["observations"]:
        targets = row.get("target_ids", [row["taxon_id"]])
        group = {"id": row["taxon_id"], "name": row["name"]}
        if any(
            target not in groups or groups[target] is not None and groups[target] != group
            for target in targets
        ):
            raise ValueError(
                "challenge snapshot has observations outside the approved target/species mapping"
            )
        matched_genera = set(targets) & genera.keys()
        fact = facts.get(row.get("identified_taxon_id"))
        if genera or fact:
            if fact is None or taxonomy_species(fact) != group:
                raise ValueError("observation lacks its verified species taxonomy")
            lineage = {item["id"]: item for item in [fact, *fact["ancestors"]]}
            if set(targets) != lineage.keys() & groups.keys():
                raise ValueError("observation target matches differ from verified ancestry")
            for target in matched_genera:
                if any(lineage[target][key] != genera[target][key] for key in ("name", "rank")):
                    raise ValueError("genus target differs from verified taxonomy")
            used.add(fact["id"])
    if used != facts.keys():
        raise ValueError("snapshot taxonomy contains unused evidence")


def public_observation(row, species, start, end, groups=None, taxonomy=None):
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
    fixed = [groups[target] for target in matches if groups[target] is not None]
    dynamic = None
    if taxonomy is not None or any(groups[target] is None for target in matches):
        if taxonomy is None:
            raise ValueError("genus observations require ranked taxonomy lookup")
        fact = taxonomy(taxon["id"])
        lineage = {item["id"] for item in [fact, *fact["ancestors"]]}
        # /taxa/{id}.ancestors omits Life (48460), while observation ancestor_ids includes it.
        if lineage - {48460} != {taxon["id"], *ancestors} - {48460} or any(
            key in taxon and taxon[key] != fact[key] for key in ("name", "rank")
        ):
            raise ValueError("observation identification differs from verified taxonomy ancestry")
        dynamic = taxonomy_species(fact)
        if dynamic is None:
            return None  # Genus-only identifications do not invent a species diversity group.
    species_ids = {group["id"] for group in fixed + ([dynamic] if dynamic else [])}
    if len(species_ids) != 1:
        raise ValueError("listed targets disagree on the observation's species grouping")
    species_id = species_ids.pop()
    if species_id not in {taxon["id"], *ancestors}:
        raise ValueError("observation identification lacks the configured species ancestor")
    species_name = (dynamic or fixed[0])["name"]
    if any(group["name"] != species_name for group in fixed):
        raise ValueError("listed targets disagree on the observation's species name")
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
        **public_context(row, taxon),
    }


def enumerate_observations(fetch, filters, species, max_pages=MAX_PAGES, groups=None, taxonomy=None):
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
            item = public_observation(row, species, start, end, groups, taxonomy)
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
    facts = {}

    def taxonomy(taxon_id):
        if taxon_id not in facts:
            url = TAXON_API + str(taxon_id)
            data = fetch(url)
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("results"), list)
                or len(data["results"]) != 1
            ):
                raise ValueError("taxonomy lookup requires exactly one identified taxon")
            taxon = data["results"][0]
            fact = taxonomy_identity(taxon)
            if fact["id"] != taxon_id or not isinstance(taxon.get("ancestors"), list):
                raise ValueError("taxonomy lookup returned a different identity or missing ancestors")
            fact["ancestors"] = [taxonomy_identity(ancestor) for ancestor in taxon["ancestors"]]
            fact["lookup"] = {"url": url, "sha256": digest(data)}
            validate_taxonomy([fact])
            facts[taxon_id] = fact
        return facts[taxon_id]

    lookup = taxonomy if any(group is None for group in groups.values()) else None
    first, hashes, evidence = enumerate_observations(fetch, filters, species, groups=groups, taxonomy=lookup)
    second, repeated, confirmation = enumerate_observations(
        fetch, filters, species, groups=groups, taxonomy=lookup
    )
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
    if any(group is None for group in groups.values()):
        used = {row["identified_taxon_id"] for row in first}
        snapshot["taxonomy"] = [facts[key] for key in sorted(facts) if key in used]
    validate_target_mapping(snapshot, config)
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
        facts = validate_taxonomy(snapshot.get("taxonomy", []))
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
            validate_context(row)
            fact = facts.get(row.get("identified_taxon_id"))
            if "taxonomy" in snapshot and fact is None:
                raise ValueError("snapshot observation lacks ranked taxonomy evidence")
            if fact:
                lineage = {item["id"] for item in [fact, *fact["ancestors"]]}
                declared = filters.get("taxon_id", "").split(",")
                if any(not re.fullmatch(r"[1-9][0-9]*", target) for target in declared):
                    raise ValueError("invalid taxonomy-bound target filter")
                if (
                    taxonomy_species(fact) != {"id": row["taxon_id"], "name": row["name"]}
                    or row.get("identified_name", fact["name"]) != fact["name"]
                    or set(target_ids) != lineage & {int(target) for target in declared}
                ):
                    raise ValueError("snapshot observation conflicts with ranked taxonomy")
            seen.add(row["id"])
        if facts.keys() != {
            row.get("identified_taxon_id") for row in rows if row.get("identified_taxon_id") in facts
        }:
            raise ValueError("snapshot taxonomy contains unused evidence")
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
