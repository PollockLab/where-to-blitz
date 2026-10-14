"""Apply the committed shipping decision without changing the prespecified research scorer."""

import json

import numpy as np

from atrisk_ranges import key_units

METHODS = ("allowlist", "range_clip")
POLYGON_REQUIRED_NAMES = frozenset(("Lampetra richardsoni", "Peltigera gowardii"))


def load_policy(path, download_key, listings):
    """Read a download-specific decision, refusing absent, malformed or stale policies."""
    try:
        with open(path) as f:
            policy = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(
            f"shipping policy unavailable or malformed at {path}: {e}; use --candidate for research"
        )
    if not isinstance(policy, dict):
        raise SystemExit("shipping policy must be a JSON object")
    if policy.get("download_key") != download_key:
        raise SystemExit(
            f"shipping policy download_key {policy.get('download_key')!r} does not match {download_key!r}; "
            "use --candidate for research"
        )
    if policy.get("method") not in METHODS:
        raise SystemExit(f"shipping policy method must be one of {METHODS}")
    for field in ("gate_commit", "scorer_commit"):
        if not isinstance(policy.get(field), str) or not policy[field].strip():
            raise SystemExit(f"shipping policy needs a nonempty {field}")
    dropped = policy.get("dropped_listing_ids")
    if not isinstance(dropped, list) or any(not isinstance(i, str) or not i.strip() for i in dropped):
        raise SystemExit("shipping policy dropped_listing_ids must be a list of nonempty strings")
    if len(set(dropped)) != len(dropped):
        raise SystemExit("shipping policy dropped_listing_ids must be unique")
    unknown = sorted(set(dropped) - {r["listing_id"] for r in listings})
    if unknown:
        raise SystemExit(f"shipping policy contains unknown listing IDs: {unknown}")
    reasons = policy.get("dropped_listing_reasons", {})
    if not isinstance(reasons, dict) or any(
        i not in dropped or not isinstance(reason, str) or not reason.strip() for i, reason in reasons.items()
    ):
        raise SystemExit("shipping policy dropped_listing_reasons must map dropped IDs to nonempty reasons")
    return policy


def apply_policy(policy, listings, entries):
    """Select listing entries, retaining polygon safeguards even for species-level allowlist counting."""
    ambiguous = {r["listing_id"] for r in listings if POLYGON_REQUIRED_NAMES.intersection(r["names"])}
    units = key_units(entries)
    risky_units = {units[k] for e in entries if e["id"] in ambiguous for k in e["keys"]}
    # A shared species key cannot distinguish the ambiguous taxa, including through alias overlaps.
    # Every listing in those species units needs its own polygon to keep its weight in the right range.
    required = {e["id"] for e in entries if any(units[k] in risky_units for k in e["keys"])}
    requested = set(policy["dropped_listing_ids"])
    reasons = {
        i: policy.get("dropped_listing_reasons", {}).get(i, f"excluded by selected {policy['method']} policy")
        for i in sorted(requested)
    }
    selected, dropped = [], []
    for original in entries:
        e = dict(original)
        lid = e["id"]
        if lid not in requested and lid in required:
            if e["geom"] is None or e["geom"].is_empty:
                reasons[lid] = "ambiguous taxon requires a polygon; no usable polygon available"
            else:
                e["infra"] = set()  # ambiguous species keys must never bypass the polygon
        if lid in reasons:
            dropped.append(original)
            print(f"shipping policy dropped listing {lid}: {reasons[lid]}")
        else:
            if policy["method"] == "allowlist" and lid not in required:
                e.update(geom=None, infra=set())
            selected.append(e)
    applied = {
        **policy,
        "applied": True,
        "requested_dropped_listing_ids": sorted(requested),
        "dropped_listing_ids": sorted(reasons),
        "dropped_matched_listing_ids": sorted(e["id"] for e in dropped),
        "dropped_listing_reasons": reasons,
        "retained_listing_ids": sorted(e["id"] for e in selected),
        "required_polygon_listing_ids": sorted(e["id"] for e in selected if e["id"] in required),
    }
    return selected, dropped, applied


def with_dropped_totals(cols, kept, dropped):
    """Keep excluded matched listings in the clip audit with zero kept and their full alias-key totals."""
    return {
        **kept,
        **{
            e["id"]: (0, int(cols["n"][np.isin(cols["specieskey"], sorted(e["keys"]))].sum()))
            for e in dropped
        },
    }


def range_provenance(base, listings, entries, method):
    """Distinguish available range sources from the polygons actually applied by the selected method."""
    clipped = {e["id"] for e in entries if e["geom"] is not None}
    counts = {}
    for r in listings:
        if r["listing_id"] in clipped:
            src = r["range_source"]
            counts[src] = counts.get(src, 0) + 1
    result = dict(base)
    result["selected_listings_by_available_range_source"] = result.pop("listings_in_layer_by_source")
    result.update(
        method=method,
        effective_clipped_listing_ids=sorted(clipped),
        listings_clipped_by_source=counts,
        species_level_listing_ids=sorted(e["id"] for e in entries if e["geom"] is None),
    )
    return result
