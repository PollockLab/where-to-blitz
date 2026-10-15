"""Acquire complete, bounded historical-month plant counts and the actual BC boundary.

Only aggregate species counts and provenance enter the repository. Full public API
responses stay in a new /tmp cache. Existing output files are never replaced.
"""

import argparse
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.inaturalist.org/v1"
PER_PAGE = 500
MAX_PAGES = 12
MAX_REQUESTS = 144
QUERY = {
    "place_id": 7085,
    "iconic_taxa": "Plantae",
    "quality_grade": "research",
    "geoprivacy": "open",
    "taxon_geoprivacy": "open",
    "threatened": "false",
    "d2": "2024-12-31",
    "created_d2": "2025-01-01",
    "per_page": PER_PAGE,
    "order_by": "count",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def month_pages(fetch, month, max_pages=MAX_PAGES):
    """Enumerate every rank before retaining species; reject partial or shifting pages."""
    if not integer(month, 1) or month > 12:
        raise ValueError("month must be an integer from 1 to 12")
    if not integer(max_pages, 1) or max_pages > MAX_PAGES:
        raise ValueError("page limit must be from 1 to 12")
    rows, seen, manifest, total, previous = [], set(), [], None, None
    page = 1
    while True:
        url = (
            requests.Request(
                "GET", f"{API}/observations/species_counts", params={**QUERY, "month": month, "page": page}
            )
            .prepare()
            .url
        )
        data, evidence = fetch(url)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError(f"month {month} page {page}: invalid response")
        actual_total = data.get("total_results")
        if (
            not integer(actual_total)
            or not integer(data.get("page"), 1)
            or data["page"] != page
            or not integer(data.get("per_page"), 1)
            or data["per_page"] != PER_PAGE
        ):
            raise ValueError(f"month {month} page {page}: invalid pagination")
        if total is None:
            total = actual_total
            if math.ceil(total / PER_PAGE) > max_pages:
                raise ValueError(f"month {month}: complete enumeration exceeds page limit")
        if actual_total != total:
            raise ValueError(f"month {month}: total changed during enumeration")
        expected = min(PER_PAGE, max(0, total - (page - 1) * PER_PAGE))
        if len(data["results"]) != expected:
            raise ValueError(f"month {month} page {page}: incomplete page")
        for row in data["results"]:
            taxon = row.get("taxon") if isinstance(row, dict) else None
            if (
                not isinstance(taxon, dict)
                or not integer(taxon.get("id"), 1)
                or not isinstance(taxon.get("rank"), str)
                or not taxon["rank"]
                or not integer(row.get("count"), 1)
            ):
                raise ValueError(f"month {month} page {page}: invalid taxon count")
            taxon_id = taxon["id"]
            if taxon_id in seen:
                raise ValueError(f"month {month}: duplicate taxon across pages")
            if previous is not None and row["count"] > previous:
                raise ValueError(f"month {month}: count order changed across pages")
            seen.add(taxon_id)
            previous = row["count"]
            rows.append((taxon_id, taxon["rank"], row["count"]))
        manifest.append(
            {
                **evidence,
                "month": month,
                "page": page,
                "total_results": total,
                "result_count": len(data["results"]),
            }
        )
        if len(rows) == total:
            break
        page += 1
    return {taxon_id: count for taxon_id, rank, count in rows if rank == "species"}, manifest


def boundary_feature(data):
    """Require the requested province's real closed polygon, never its bounding box."""
    if not isinstance(data, dict):
        raise ValueError("invalid BC place response")
    results = data.get("results")
    if (
        data.get("total_results") != 1
        or not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], dict)
        or results[0].get("id") != 7085
        or results[0].get("name") != "British Columbia"
        or results[0].get("display_name") != "British Columbia, CA"
    ):
        raise ValueError("place response is not British Columbia, CA (7085)")
    place = results[0]
    geometry = place.get("geometry_geojson")
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError("BC place has no polygon geometry")
    polygons = [geometry.get("coordinates")] if geometry["type"] == "Polygon" else geometry.get("coordinates")
    if not isinstance(polygons, list) or not polygons:
        raise ValueError("BC polygon is empty")
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("BC polygon has no rings")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("BC polygon has an invalid ring")
            for xy in ring:
                if (
                    not isinstance(xy, list)
                    or len(xy) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in xy)
                    or not -180 <= xy[0] <= 180
                    or not -90 <= xy[1] <= 90
                ):
                    raise ValueError("BC polygon has invalid coordinates")
    return {
        "type": "Feature",
        "properties": {"place_id": 7085, "name": place["name"], "display_name": place["display_name"]},
        "geometry": geometry,
    }


class PublicFetcher:
    """Finite timeout/retries, global network-attempt budget, and exclusive raw cache."""

    def __init__(self, cache_dir, session=None, clock=time.monotonic, sleep=time.sleep):
        self.cache_dir = Path(cache_dir).resolve()
        if not self.cache_dir.is_relative_to(Path("/tmp").resolve()):
            raise ValueError("full responses must be cached under /tmp")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = session or requests.Session()
        self.clock, self.sleep = clock, sleep
        self.attempts, self.last_started = 0, None

    def __call__(self, url):
        for attempt in range(3):
            if self.attempts >= MAX_REQUESTS:
                raise ValueError("global 144-request budget exhausted")
            if self.last_started is not None:
                self.sleep(max(0, 1.0 - (self.clock() - self.last_started)))
            self.last_started = self.clock()
            self.attempts += 1
            try:
                response = self.session.get(
                    url,
                    timeout=(10, 30),
                    headers={"Accept": "application/json", "User-Agent": "where-to-blitz-bc-plant-pilot/1.0"},
                )
            except requests.RequestException:
                if attempt == 2:
                    raise
                self.sleep(2**attempt)
                continue
            retrieved = utc_now()
            raw = response.content
            cache_file = self.cache_dir / f"response-{self.attempts:03d}.json"
            with cache_file.open("xb") as stream:
                stream.write(raw)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                try:
                    retry = float(response.headers.get("Retry-After", 2**attempt))
                except ValueError:
                    retry = 2**attempt
                if not math.isfinite(retry):
                    retry = 2**attempt
                self.sleep(min(max(retry, 1), 10))
                continue
            response.raise_for_status()
            return json.loads(raw), {
                "url": url,
                "retrieved_utc": retrieved,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        raise ValueError("request retries exhausted")


def acquire(fetch):
    """Return aggregates only after all twelve months and the boundary are complete."""
    boundary_url = f"{API}/places/7085"
    data, boundary_evidence = fetch(boundary_url)
    boundary = boundary_feature(data)
    species, totals, page_evidence = {}, [], []
    for month in range(1, 13):
        counts, pages = month_pages(fetch, month)
        if not counts:
            raise ValueError(f"month {month}: no species records, annual coverage unavailable")
        totals.append(sum(counts.values()))
        for taxon_id, count in counts.items():
            species.setdefault(taxon_id, [0] * 12)[month - 1] = count
        page_evidence.extend(pages)
    aggregate = {
        "schema_version": 1,
        "region": {"place_id": 7085, "name": "British Columbia, CA"},
        "months": list(range(1, 13)),
        "species": [[taxon_id, counts] for taxon_id, counts in sorted(species.items())],
        "monthly_species_record_totals": totals,
        "query": QUERY,
        "manifest": {
            "retrieved_utc": utc_now(),
            "pages": page_evidence,
            "boundary": {**boundary_evidence, "output": "bc_boundary.geojson"},
            "source": "iNaturalist public API, https://api.inaturalist.org/v1/docs/",
            "attribution": "iNaturalist contributors; aggregate counts only, no photos or individual records.",
            "rights": "No blanket copyright license is inferred for source observations or place geometry.",
            "scope": [
                "Current taxonomy, research-grade and protection snapshot, queried with observed/created cutoffs.",
                "Historical identifications, grade, protection and actual availability at the cutoff are unknown.",
                "Counts describe filtered research-grade records, not abundance or detection probability.",
                "Species rank only, retained after complete enumeration of every monthly result page.",
                "Live candidates must independently pass current protection, privacy and taxonomy filters.",
            ],
        },
    }
    return aggregate, boundary


def write_exclusive(path, value):
    """Install complete JSON atomically and refuse any existing destination."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".plant-input-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=Path("research/data/bc_plant_season.json"))
    parser.add_argument("--boundary", type=Path, default=Path("research/data/bc_boundary.geojson"))
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.boundary.exists() or args.output.resolve() == args.boundary.resolve():
        parser.error("output paths must be distinct and must not already exist")
    fetch = PublicFetcher(args.cache_dir or tempfile.mkdtemp(prefix="wtb-plant-inputs-", dir="/tmp"))
    aggregate, boundary = acquire(fetch)
    aggregate["manifest"]["network_attempts"] = fetch.attempts
    aggregate["manifest"]["boundary"]["output"] = args.boundary.name
    boundary_bytes = (json.dumps(boundary, separators=(",", ":"), allow_nan=False) + "\n").encode()
    aggregate["manifest"]["boundary"]["output_sha256"] = hashlib.sha256(boundary_bytes).hexdigest()
    write_exclusive(args.boundary, boundary)
    write_exclusive(args.output, aggregate)
    print(
        f"Saved {len(aggregate['species'])} species across twelve complete months; "
        f"{fetch.attempts} network attempts. Raw cache: {fetch.cache_dir}"
    )


if __name__ == "__main__":
    main()
