"""Reporting-only listing-cell audit. Never used for ship selection.

Reuses the frozen scorer's test labels, taxon resolution and method definitions.
Reports its literal union-times-listing-count diagnostic beside listing-specific
counts, without changing the frozen scorer or production candidate artifacts.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cells_of(cell, indices):
    return {tuple(c) for c in cell[indices]}


def pair_counts(cols, listings, entries_by_method, species_rows, false_only, project, fine, res, ar):
    cell = np.stack([cols["yi"] // round(res / fine), cols["xi"] // round(res / fine)], axis=1)
    original, corrected, per_listing = {}, {}, {}
    for method, entries in entries_by_method.items():
        hit = np.zeros(len(species_rows), dtype=bool)
        per_listing[method] = {row["listing_id"]: 0 for row in listings}
        for entry, _, accepted, _ in ar.accepted_rows(cols, entries, project, fine):
            hit[accepted] = True
            per_listing[method][entry["id"]] = len(cells_of(cell, accepted) & false_only)
        original[method] = len(listings) * len(cells_of(cell, hit & species_rows) & false_only)
        corrected[method] = sum(per_listing[method].values())
    return original, corrected, per_listing


def validate_disjoint(ar, methods):
    from shapely.geometry import box

    cols = {"specieskey": np.array(["K"] * 3, dtype=object),
            "acceptedtaxonkey": np.array(["F"] * 3, dtype=object),
            "yi": np.array([5, 5, 5]), "xi": np.array([5, 55, 105]), "n": np.ones(3, dtype=int)}
    listings = [{"listing_id": lid} for lid in ("west", "east")]
    candidate = [{"id": lid, "keys": {"K"}, "infra": set(), "weight": 1, "geom": geom}
                 for lid, geom in (("west", box(0, 0, 0.25, 0.25)),
                                   ("east", box(0.5, 0, 0.75, 0.25)))]
    live = [dict(entry, geom=None) for entry in candidate]
    entries = {"live": live, "allowlist": [], "clip": candidate, "clip_no_override": candidate}
    original, corrected, detail = pair_counts(
        cols, listings, entries, np.ones(3, dtype=bool), {(0, 0), (0, 2), (0, 4)},
        lambda lon, lat: (lon, lat), 0.01, 0.25, ar,
    )
    assert tuple(original) == methods
    assert original == {"live": 6, "allowlist": 0, "clip": 4, "clip_no_override": 4}
    assert corrected == {"live": 6, "allowlist": 0, "clip": 2, "clip_no_override": 2}
    assert detail["clip"] == {"west": 1, "east": 1}
    return {"case": "Three false-only cells, two disjoint listing ranges accepting one cell each",
            "original_literal": original, "corrected": corrected, "per_listing": detail,
            "assertions_passed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--download", required=True)
    parser.add_argument("--output", default="research/range_clip_pair_audit.json")
    args = parser.parse_args()
    root = Path(args.worktree).resolve()
    download = Path(args.download).resolve()
    os.chdir(root)
    sys.path[:0] = [str(root), str(root / "research")]
    import allowlist_compare as frozen

    import atrisk_ranges as ar
    import build_atrisk_layer as bal

    input_paths = {"download": download, "scorer": root / "research/allowlist_compare.py",
                   "test_set": root / frozen.TEST_SET, "listings": root / ar.LISTINGS,
                   "ranges": root / ar.RANGES, "crosswalk": root / bal.CROSSWALK,
                   "geometry_code": root / "atrisk_ranges.py"}
    hashes = {label: sha256(path) for label, path in input_paths.items()}
    # The original unpublished registration commit may be rewritten by rebase. Pin its bytes.
    assert hashes["scorer"] == "0ed079d12aaa298c5bc389a5a674c41a9d340e421cd05aa643a0a67624eccacb", (
        "Frozen scorer changed"
    )
    validation = validate_disjoint(ar, frozen.METHODS)
    with (
        zipfile.ZipFile(download) as archive,
        archive.open(next(n for n in archive.namelist() if not n.endswith("/"))) as stream,
    ):
        cols = ar.read_columns(stream)
    test = frozen.load_test_set()
    crosswalk = bal.read_crosswalk()
    listings = ar.read_listings()
    geometries = ar.load_range_geoms()
    transform = ar.transformer()
    x, y = transform((cols["xi"] + 0.5) * bal.FINE, (cols["yi"] + 0.5) * bal.FINE)
    x, y = np.asarray(x), np.asarray(y)

    def cached_projection(lon, lat):
        assert len(lon) == len(x) and len(lat) == len(y)
        return x, y

    # Cache identical source unions/buffers while calling the unchanged range-entry builder.
    buffer_cache = {}
    original_buffered_union = ar.buffered_union

    def cached_buffered_union(geoms, distance):
        key = (distance, tuple(id(g) for g in geoms))
        if key not in buffer_cache:
            buffer_cache[key] = original_buffered_union(geoms, distance)
        return buffer_cache[key]

    ar.buffered_union = cached_buffered_union
    sk, accepted_taxa = cols["specieskey"], cols["acceptedtaxonkey"]
    cell = np.stack([cols["yi"] // round(bal.RES / bal.FINE),
                     cols["xi"] // round(bal.RES / bal.FINE)], axis=1)
    totals = {m: {"original_literal": 0, "corrected": 0} for m in frozen.METHODS}
    species_results, skipped = {}, []
    for species, labels in sorted(test.items()):
        subset = [row for row in listings if labels["names"] & set(row["names"])]
        keys = {key for name in labels["names"] for key in crosswalk.get(name, ((), ""))[0]}
        if not subset or not keys:
            skipped.append(species)
            print(f"skip {species}: no matched test listing", flush=True)
            continue
        col_key = crosswalk[next(n for n in labels["names"] if n in crosswalk and crosswalk[n][0])][0][0]
        true_keys, missing_true = frozen.resolve(species, labels["listed"], col_key)
        false_keys, missing_false = frozen.resolve(species, labels["unlisted"], col_key)
        rows = np.isin(sk, sorted(keys))
        is_true = rows & np.isin(accepted_taxa, sorted(true_keys))
        is_false = rows & np.isin(accepted_taxa, sorted(false_keys))
        false_only = cells_of(cell, is_false) - cells_of(cell, is_true)
        clip = [dict(e, infra=set()) for e in ar.listing_entries(subset, crosswalk, geometries)]
        no_override = [dict(e, infra=set()) for e in ar.listing_entries(
            frozen.without_overrides(subset), crosswalk, geometries)]
        live = [dict(e, geom=None, infra=set()) for e in clip]
        keep = "COVERS" in labels["classes"] or species in frozen.KEEP_UNCLEAR
        entries = {"live": live, "allowlist": live if keep else [],
                   "clip": clip, "clip_no_override": no_override}
        original, corrected, detail = pair_counts(
            cols, subset, entries, rows, false_only, cached_projection, bal.FINE, bal.RES, ar,
        )
        # Literal live/allowlist union is pooled species rows, even for an unmatched test listing.
        for method in ("live", "allowlist"):
            original[method] = len(subset) * len(false_only) if method == "live" or keep else 0
        for method in frozen.METHODS:
            totals[method]["original_literal"] += original[method]
            totals[method]["corrected"] += corrected[method]
        species_results[species] = {
            "listing_ids": [r["listing_id"] for r in subset], "classes": sorted(labels["classes"]),
            "false_only_cells": len(false_only), "original_literal": original,
            "corrected": corrected, "per_listing": detail,
            "resolved_true_keys": sorted(true_keys), "resolved_false_keys": sorted(false_keys),
            "unresolved_true_epithets": missing_true, "unresolved_false_epithets": missing_false,
        }
        print(f"{species}: original={original} corrected={corrected}", flush=True)
    after = {label: sha256(path) for label, path in input_paths.items()}
    assert after == hashes, "Inputs changed during reporting audit"
    result = {"purpose": "Reporting-only supplemental diagnostic, never used for ship selection",
              "ship_rule": "No decision or metric used for ship selection is recalculated or changed",
              "scorer_commit": "d765666", "geometry_code_commit": subprocess.check_output(
                  ["git", "log", "-1", "--format=%H", "--", "atrisk_ranges.py"]).decode().strip(),
              "inputs": {label: {"path": str(path), "sha256": hashes[label]}
                         for label, path in input_paths.items()},
              "inputs_unchanged_during_audit": True, "totals": totals,
              "validation": validation, "species": species_results, "skipped": skipped}
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(totals, indent=2), flush=True)
    print(f"wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
