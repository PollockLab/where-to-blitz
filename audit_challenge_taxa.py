"""Resolve a lab challenge CSV conservatively, preserving every source row.

Exact scientific names and explicitly recorded scientific synonyms only. Ambiguous names, inactive annotations,
higher ranks and blank cells remain explicit review items; no fuzzy match or
species-level expansion of a named subspecies/variety is accepted. The report is
an audit, never an approved publication configuration.
"""

import argparse
import csv
import hashlib
import io
import json
import re
import time
import unicodedata
from pathlib import Path

import requests

API = "https://api.inaturalist.org/v1/taxa"
SUPPORTED = {"species", "subspecies", "variety", "form"}


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value.replace("Â\u00a0", " ")).split())


def query_name(value):
    name = normalized(value)
    name = re.sub(r"\s+\(Inactive Taxon\)$", "", name, flags=re.I)
    name = re.sub(r"\s+spp\.$", "", name)
    return re.sub(r"\s+(?:ssp\.|subsp\.|var\.|f\.)\s+", " ", name)


def lookup_url(name):
    return (
        requests.Request(
            "GET", API, params={"q": name, "per_page": 200, "all_names": "true", "is_active": "true"}
        )
        .prepare()
        .url
    )


def cached_response(url, fetch, cache):
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if path.exists():
        payload = path.read_bytes()
    else:
        payload = json.dumps(fetch(url), sort_keys=True, allow_nan=False).encode()
        response = json.loads(payload)
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise ValueError("invalid taxon response")
        path.write_bytes(payload)
    return json.loads(payload), {"url": url, "sha256": hashlib.sha256(payload).hexdigest()}


def resolve(raw, response):
    """A source name is a concept to preserve, not a hint for nearest-name matching."""
    if "inactive taxon" in normalized(raw).casefold():
        return {"status": "needs_review", "reason": "source explicitly marks an inactive taxon"}
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("results"), list)
        or type(response.get("total_results")) is not int
    ):
        raise ValueError("invalid taxon response")
    name = query_name(raw)
    if response["total_results"] > len(response["results"]):
        return {"status": "needs_review", "reason": "taxon search result is incomplete"}

    def exact_scientific_name(taxon):
        return normalized(taxon["name"]).casefold() == name.casefold() or any(
            isinstance(alias, dict)
            and alias.get("lexicon") == "scientific-names"
            and isinstance(alias.get("name"), str)
            and normalized(alias["name"]).casefold() == name.casefold()
            for alias in taxon.get("names", [])
        )

    expected = (
        "variety"
        if " var. " in normalized(raw)
        else "subspecies"
        if re.search(r"\s(?:ssp\.|subsp\.)\s", normalized(raw))
        else "form"
        if " f. " in normalized(raw)
        else "genus"
        if normalized(raw).endswith(" spp.")
        else "species"
        if len(name.split()) == 2
        else None
    )
    matches = [
        r
        for r in response["results"]
        if isinstance(r, dict)
        and isinstance(r.get("name"), str)
        and exact_scientific_name(r)
        and r.get("is_active") is True
    ]
    ranked = [r for r in matches if r.get("rank") == expected]
    if ranked:
        matches = ranked
    if len(matches) != 1:
        return {"status": "needs_review", "reason": "no unique exact active scientific-name match"}
    match = matches[0]
    if type(match.get("id")) is not int or match["id"] <= 0:
        raise ValueError("invalid taxon identifier")
    rank = match.get("rank")
    if rank not in SUPPORTED:
        return {
            "status": "needs_review",
            "reason": "target rank is broader than species",
            "candidate": {"id": match["id"], "name": match["name"], "rank": rank},
        }
    if expected and rank != expected:
        return {"status": "needs_review", "reason": "exact name has a different rank from the source"}
    target = {"id": match["id"], "name": match["name"], "rank": rank}
    if rank != "species":
        ancestors = [
            a
            for a in match.get("ancestors", [])
            if a.get("rank") == "species"
            and a.get("is_active") is not False
            and type(a.get("id")) is int
            and a["id"] > 0
            and isinstance(a.get("name"), str)
        ]
        if len(ancestors) != 1:
            return {
                "status": "needs_review",
                "reason": "species ancestry is not independently available",
                "candidate": target,
            }
        target["species"] = {"id": ancestors[0]["id"], "name": ancestors[0]["name"]}
    return {
        "status": "resolved",
        "target": target,
        "match_type": "accepted_name"
        if normalized(match["name"]).casefold() == name.casefold()
        else "recorded_scientific_synonym",
    }


def audit(data, fetch, cache):
    try:
        text, encoding = data.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        text, encoding = data.decode("cp1252"), "cp1252"
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != ["Common.name", "Scientific.name"]:
        raise ValueError("expected Common.name,Scientific.name columns")
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    results = []
    for index, row in enumerate(reader, start=2):
        raw = row.get("Scientific.name")
        if not isinstance(raw, str) or not isinstance(row.get("Common.name"), str) or None in row:
            raise ValueError(f"malformed CSV row {index}")
        result = {
            "csv_row": index,
            "raw_scientific_name": raw,
            "common_name": row["Common.name"],
            "query": query_name(raw),
        }
        if not result["query"]:
            result.update(status="needs_review", reason="scientific name is blank")
        else:
            url = lookup_url(result["query"])
            response, result["lookup"] = cached_response(url, fetch, cache)
            answer = resolve(raw, response)
            if answer.get("reason") == "species ancestry is not independently available":
                target = answer["candidate"]
                detail_url = API + "/" + str(target["id"])
                detail, result["ancestry_lookup"] = cached_response(detail_url, fetch, cache)
                if len(detail.get("results", [])) != 1 or detail["results"][0].get("id") != target["id"]:
                    raise ValueError("taxon ancestry lookup returned a different identifier")
                answer = resolve(raw, detail)
            result.update(answer)
        results.append(result)
    return {
        "schema_version": 1,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_encoding": encoding,
        "rows": results,
        "resolved_rows": sum(r["status"] == "resolved" for r in results),
        "review_rows": sum(r["status"] != "resolved" for r in results),
        "approved_for_publication": False,
    }


class TaxonFetcher:
    def __init__(self):
        self.session = requests.Session()
        self.last_request = 0.0

    def __call__(self, url):
        for attempt in range(3):
            time.sleep(max(0, 1.0 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.get(
                    url, timeout=(10, 30), headers={"User-Agent": "where-to-blitz-challenge-taxonomy/1.0"}
                )
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise ValueError("taxon lookup retries exhausted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; preserve previous audits and choose another path")
    result = audit(args.csv.read_bytes(), TaxonFetcher(), args.cache)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(f"Resolved {result['resolved_rows']} source rows; {result['review_rows']} need review.")


if __name__ == "__main__":
    main()
