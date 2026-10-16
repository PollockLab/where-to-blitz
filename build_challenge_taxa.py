"""Derive an explicitly approved verified subset, preserving every omitted source row.

The private CSV is never copied. Publish only the allowlisted taxonomy-audit
derivative and its approved exact targets, with the original CSV fingerprint.
Regeneration needs the reviewed audit JSON, no observation records or network.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from audit_challenge_taxa import normalized, query_name
from conservation_challenge import validate_list

AUDIT_URL = (
    "https://raw.githubusercontent.com/PollockLab/where-to-blitz/main/webapp/challenge_taxa_audit.json"
)
REASONS = {
    "scientific name is blank": "blank_scientific_name",
    "source explicitly marks an inactive taxon": "inactive_source_taxon",
    "no unique exact active scientific-name match": "no_unique_exact_match",
    "taxon search result is incomplete": "incomplete_search",
    "target rank is broader than species": "broader_than_species",
    "exact name has a different rank from the source": "rank_mismatch",
    "species ancestry is not independently available": "missing_species_ancestry",
}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def checksum(value):
    return hashlib.sha256(value).hexdigest()


def validate_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("audit source and lookup references require lowercase SHA256")


def public_lookup(value):
    if not isinstance(value, dict) or set(value) != {"url", "sha256"}:
        raise ValueError("audit lookup requires URL and SHA256")
    validate_digest(value["sha256"])
    url = urlparse(value["url"])
    if (
        url.scheme != "https"
        or url.netloc != "api.inaturalist.org"
        or not re.fullmatch(r"/v1/taxa(?:/[1-9][0-9]*)?", url.path)
        or url.fragment
    ):
        raise ValueError("audit lookup must reference the public taxonomy API")
    return {"url": value["url"], "sha256": value["sha256"]}


def public_target(value, supported=True):
    if (
        not isinstance(value, dict)
        or type(value.get("id")) is not int
        or value["id"] <= 0
        or not isinstance(value.get("name"), str)
        or not value["name"].strip()
        or not isinstance(value.get("rank"), str)
    ):
        raise ValueError("invalid audited target identity")
    target = {key: value[key] for key in ("id", "name", "rank")}
    if supported:
        if target["rank"] not in ("species", "subspecies", "variety", "form"):
            raise ValueError("resolved audit target has an unsupported rank")
        if target["rank"] != "species":
            species = value.get("species")
            if (
                not isinstance(species, dict)
                or type(species.get("id")) is not int
                or species["id"] <= 0
                or not isinstance(species.get("name"), str)
            ):
                raise ValueError("audited infraspecies requires species grouping metadata")
            target["species"] = {key: species[key] for key in ("id", "name")}
    return target


def generate(data, *, approved=False, audit_url=AUDIT_URL):
    """Return reproducible public audit/config bytes after explicit subset approval."""
    if approved is not True:
        raise ValueError("verified-subset publication requires explicit approval")
    original = json.loads(data)
    if (
        not isinstance(original, dict)
        or type(original.get("schema_version")) is not int
        or original["schema_version"] != 1
        or not isinstance(original.get("rows"), list)
        or not original["rows"]
        or not isinstance(original.get("source_encoding"), str)
    ):
        raise ValueError("invalid reviewed taxonomy audit")
    validate_digest(original.get("source_sha256"))
    input_audit_sha = original.get("input_audit_sha256", checksum(data))
    validate_digest(input_audit_sha)
    rows, resolved, omissions, targets = [], [], [], {}
    for csv_row, entry in enumerate(original["rows"], 2):
        if (
            not isinstance(entry, dict)
            or type(entry.get("csv_row")) is not int
            or entry["csv_row"] != csv_row
            or any(
                not isinstance(entry.get(key), str) for key in ("raw_scientific_name", "common_name", "query")
            )
            or entry["query"] != query_name(entry["raw_scientific_name"])
            or entry.get("status") not in ("resolved", "needs_review")
        ):
            raise ValueError("audit must preserve every source row in order and its original names")
        row = {
            key: entry[key] for key in ("csv_row", "raw_scientific_name", "common_name", "query", "status")
        }
        for key in ("lookup", "ancestry_lookup"):
            if key in entry:
                row[key] = public_lookup(entry[key])
        if entry["query"] and "lookup" not in row:
            raise ValueError("nonempty audit source row lacks lookup evidence")
        if row["status"] == "resolved":
            if not entry["query"] or "inactive taxon" in normalized(entry["raw_scientific_name"]).casefold():
                raise ValueError("blank or explicitly inactive source names cannot enter the verified subset")
            target = public_target(entry.get("target"))
            match_type = entry.get("match_type")
            if match_type not in ("accepted_name", "recorded_scientific_synonym"):
                raise ValueError("resolved target must have an exact scientific-name match")
            if (
                match_type == "accepted_name"
                and normalized(target["name"]).casefold() != entry["query"].casefold()
            ):
                raise ValueError("accepted scientific name differs from the source query")
            if target["id"] in targets and target != targets[target["id"]]:
                raise ValueError("conflicting target metadata for one audited identifier")
            targets[target["id"]] = target
            row.update(target=target, match_type=match_type)
            resolved.append({"csv_row": csv_row, "target_id": target["id"]})
        elif row["status"] == "needs_review":
            reason = entry.get("reason")
            if not isinstance(reason, str) or reason not in REASONS:
                raise ValueError("unrecognized omission reason requires review before publication")
            row.update(reason=reason, reason_code=REASONS[reason])
            if "candidate" in entry:
                row["candidate"] = public_target(entry["candidate"], supported=False)
            omissions.append(
                {
                    key: row[key]
                    for key in ("csv_row", "raw_scientific_name", "common_name", "reason", "reason_code")
                }
            )
        else:
            raise ValueError("every audit row must be resolved or explicitly reviewed")
        rows.append(row)
    if (
        type(original.get("resolved_rows")) is not int
        or original["resolved_rows"] != len(resolved)
        or type(original.get("review_rows")) is not int
        or original["review_rows"] != len(omissions)
    ):
        raise ValueError("reviewed audit totals disagree with its source rows")
    public = {
        "schema_version": 1,
        "source_sha256": original["source_sha256"],
        "source_encoding": original["source_encoding"],
        "input_audit_sha256": input_audit_sha,
        "rows": rows,
        "resolved_rows": len(resolved),
        "review_rows": len(omissions),
        "approved_for_publication": False,
    }
    audit_bytes = encoded(public)
    audit_sha = checksum(audit_bytes)
    config = {
        "schema_version": 1,
        "version": "verified-subset-" + audit_sha[:16],
        "source": {"url": audit_url, "sha256": audit_sha},
        "approved_for_publication": True,
        "taxa": [targets[identity] for identity in sorted(targets)],
        "coverage": {
            "source_rows": len(rows),
            "resolved_rows": len(resolved),
            "unique_targets": len(targets),
            "omitted_rows": len(omissions),
            "original_source_sha256": original["source_sha256"],
            "resolved": resolved,
            "omissions": omissions,
        },
    }
    validate_list(config)
    return audit_bytes, encoded(config)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path, help="reviewed exact-name taxonomy audit JSON")
    parser.add_argument("--audit-out", required=True, type=Path)
    parser.add_argument("--config-out", required=True, type=Path)
    parser.add_argument("--audit-url", default=AUDIT_URL)
    parser.add_argument("--approve-verified-subset", required=True, action="store_true")
    args = parser.parse_args(argv)
    public, config = generate(
        args.audit.read_bytes(), approved=args.approve_verified_subset, audit_url=args.audit_url
    )
    args.audit_out.write_bytes(public)
    args.config_out.write_bytes(config)
    result = json.loads(config)
    print(
        json.dumps(
            {
                key: result["coverage"][key]
                for key in ("source_rows", "resolved_rows", "unique_targets", "omitted_rows")
            }
        )
    )


if __name__ == "__main__":
    main()
