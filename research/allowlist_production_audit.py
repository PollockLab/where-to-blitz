"""Independently reconstruct and compare the selected at-risk production layer.

Uses CSV/JSON inputs, pyproj, Shapely and a pandas groupby, never production
selection or scoring functions. Run only after the announced rebuild finishes.
"""

import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import shape


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_csv(path):
    with open(path) as stream:
        return list(csv.DictReader(stream))


def connected_units(key_sets):
    graph = {}
    for keys in key_sets:
        for key in keys:
            graph.setdefault(key, set()).update(keys - {key})
    assigned = {}
    for key in sorted(graph):
        if key in assigned:
            continue
        todo = [key]
        while todo:
            node = todo.pop()
            if node in assigned:
                continue
            assigned[node] = key
            todo.extend(graph[node] - assigned.keys())
    return assigned


def aggregate(gi, gj, units, weights):
    data = pd.DataFrame({"gi": gi, "gj": gj, "unit": units, "weight": weights})
    best = data[data.weight > 0].groupby(["gi", "gj", "unit"], sort=True).weight.max()
    cells = best.groupby(level=[0, 1]).agg(["sum", "size"])
    return cells, best


def synthetic_validation():
    units = connected_units([{ "K", "K2" }, { "S" }])
    assert units["K"] == units["K2"] != units["S"]
    cells, _ = aggregate(np.array([0, 0, 0, 1]), np.array([0, 0, 0, 1]),
                         [units[k] for k in ("K", "K2", "S", "K")], np.array([3, 1, 2, 0]))
    assert len(cells) == 1 and int(cells.loc[(0, 0), "sum"]) == 5
    assert int(cells.loc[(0, 0), "size"]) == 2
    return {"alias_keys_deduplicated": True, "per_unit_max_weight": True,
            "zero_weight_rows_omitted": True, "two_units_score": 5, "two_units_n_species": 2}


def reconstruct(root, download):
    crosswalk = {row["name"]: row for row in read_csv(root / "cluster_results/ca/atrisk_species_keys.csv")}
    listings = read_csv(root / "cluster_results/ca/atrisk_listings.csv")
    policy = json.loads((root / "cluster_results/ca/atrisk_shipping_policy.json").read_text())
    assert policy["method"] == "allowlist"
    tests = {}
    for row in read_csv(root / "research/subspecies_test_set.csv"):
        item = tests.setdefault(row["species"], {"names": set(), "classes": set()})
        item["names"].add(row["name"])
        item["classes"].add(row["class"])
    keep_unclear = {"Taxidea taxus", "Lanius ludovicianus", "Bos bison"}
    dropped_names = set().union(*(test["names"] for species, test in tests.items()
                                 if "COVERS" not in test["classes"] and species not in keep_unclear))
    derived_drop = {row["listing_id"] for row in listings
                    if dropped_names.intersection(row["names"].split(";"))}
    assert len(derived_drop) == 35 and derived_drop == set(policy["dropped_listing_ids"])
    entries = []
    for row in listings:
        names = row["names"].split(";")
        keys = set()
        for name in names:
            match = crosswalk.get(name)
            if match and match["col_key"]:
                keys.update([match["col_key"], *match["alias_keys"].split()])
        if keys:
            entries.append({"id": row["listing_id"], "keys": keys,
                            "weight": int(row["status_weight"]), "range_ids": row["range_ids"].split(),
                            "names": names})
    original_units = connected_units([entry["keys"] for entry in entries])
    ambiguous_names = {"Lampetra richardsoni", "Peltigera gowardii"}
    risky_units = {original_units[key] for entry in entries if ambiguous_names.intersection(entry["names"])
                   for key in entry["keys"]}
    guarded = {entry["id"] for entry in entries
               if any(original_units[key] in risky_units for key in entry["keys"])}
    assert guarded == {"58", "96", "418"} == set(policy["polygon_safeguards"])
    selected = [entry for entry in entries if entry["id"] not in derived_drop]
    selected_units = connected_units([entry["keys"] for entry in selected])
    with (
        zipfile.ZipFile(download) as archive,
        archive.open(next(name for name in archive.namelist() if not name.endswith("/"))) as stream,
    ):
        data = pd.read_csv(stream, sep="\t", dtype={"specieskey": str, "acceptedtaxonkey": str},
                           keep_default_na=False)
    indices = data.groupby("specieskey", sort=False).indices
    yi, xi, n = (data[column].to_numpy().astype(np.int64) for column in ("yi", "xi", "n"))
    weights = np.zeros(len(data), dtype=np.int8)
    transform = Transformer.from_crs("EPSG:4326", "EPSG:3978", always_xy=True)
    x, y = transform.transform((xi + 0.5) * 0.01, (yi + 0.5) * 0.01)
    wanted_ranges = {rid for entry in selected if entry["id"] in guarded for rid in entry["range_ids"]}
    features = json.loads((root / "cluster_results/ca/atrisk_ranges.geojson").read_text())["features"]
    geometries = {}
    for feature in features:
        rid = feature["properties"]["range_id"]
        if rid in wanted_ranges:
            geographic = shape(feature["geometry"])
            geometries[rid] = shapely.make_valid(shapely.transform(
                geographic, lambda xy: np.column_stack(transform.transform(xy[:, 0], xy[:, 1]))))
    assert set(geometries) == wanted_ranges
    expected_audit, accepted_by_listing, buffered_geometries = {}, {}, {}
    for entry in entries:
        arrays = [indices[key] for key in sorted(entry["keys"]) if key in indices]
        rows = np.concatenate(arrays) if arrays else np.array([], dtype=np.int64)
        total = int(n[rows].sum())
        if entry["id"] in derived_drop:
            accepted = np.array([], dtype=np.int64)
        elif entry["id"] in guarded:
            assert entry["range_ids"]
            geometry = shapely.union_all([geometries[rid] for rid in entry["range_ids"]]).buffer(10000)
            assert geometry.is_valid and not geometry.is_empty
            shapely.prepare(geometry)
            buffered_geometries[entry["id"]] = geometry
            accepted = rows[shapely.contains_xy(geometry, x[rows], y[rows])]
        else:
            accepted = rows
        weights[accepted] = np.maximum(weights[accepted], entry["weight"])
        expected_audit[entry["id"]] = (int(n[accepted].sum()), total)
        accepted_by_listing[entry["id"]] = accepted
    gi, gj = yi // 25, xi // 25
    units = data.specieskey.map(selected_units).fillna("").to_numpy()
    cells, best = aggregate(gi, gj, units, weights)
    waterfan_unit = selected_units[crosswalk["Peltigera gowardii"]["col_key"]]
    west_rows, east_rows = accepted_by_listing["58"], accepted_by_listing["96"]
    west_cells = {(int(gi[i]), int(gj[i])) for i in west_rows}
    east_cells = {(int(gi[i]), int(gj[i])) for i in east_rows}
    west_only = west_cells - east_cells
    east_only = east_cells - west_cells
    west_weight = {int(best.loc[(a, b, waterfan_unit)]) for a, b in west_only}
    east_weight = {int(best.loc[(a, b, waterfan_unit)]) for a, b in east_only}
    assert west_only and east_only and west_weight == {1} and east_weight == {2}
    return {"cells": cells, "audit": expected_audit, "policy": policy, "dropped": derived_drop,
            "guarded": guarded, "matched_entries": entries, "listings": listings,
            "stats": {"fine_rows": len(data), "occurrence_count": int(n.sum()),
                      "matched_listing_count": len(entries), "retained_listing_count": len(selected),
                      "species_level_listing_count": len(selected) - len(guarded),
                      "western_waterfan_listing_records": expected_audit["58"][0],
                      "eastern_waterfan_listing_records": expected_audit["96"][0],
                      "western_waterfan_only_cells": len(west_only), "eastern_waterfan_only_cells": len(east_only),
                      "western_waterfan_unit_weight": sorted(west_weight),
                      "eastern_waterfan_unit_weight": sorted(east_weight)},
            "validation": synthetic_validation()}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--download", required=True)
    parser.add_argument("--report", default="research/allowlist_production_audit.json")
    args = parser.parse_args()
    root, download = Path(args.worktree).resolve(), Path(args.download).resolve()
    paths = {"download": download, "crosswalk": root / "cluster_results/ca/atrisk_species_keys.csv",
             "listings": root / "cluster_results/ca/atrisk_listings.csv",
             "ranges": root / "cluster_results/ca/atrisk_ranges.geojson",
             "policy": root / "cluster_results/ca/atrisk_shipping_policy.json",
             "test_set": root / "research/subspecies_test_set.csv",
             "production_layer": root / "cluster_results/ca/ca_atrisk_richness.csv",
             "production_listing_audit": root / "cluster_results/ca/atrisk_listing_clip.csv",
             "production_provenance": root / "cluster_results/ca/ca_atrisk_provenance.json"}
    before = {key: sha256(path) for key, path in paths.items()}
    result = reconstruct(root, download)
    cells = result.pop("cells")
    rows = read_csv(paths["production_layer"])
    actual = {(int(row["gi"]), int(row["gj"])): row for row in rows}
    expected_cells = {tuple(map(int, key)) for key in cells.index}
    assert len(actual) == len(rows), "Duplicate production cell"
    assert set(actual) == expected_cells, "Production cell set differs"
    maximum = int(cells["sum"].max())
    differences = []
    for (gi, gj), row in actual.items():
        expected_score = int(cells.loc[(gi, gj), "sum"])
        expected_species = int(cells.loc[(gi, gj), "size"])
        expected_normal = f"{expected_score / maximum:.5f}"
        if (float(row["score_raw"]) != expected_score or int(row["n_species"]) != expected_species
                or row["conservation_norm"] != expected_normal
                or float(row["lat"]) != (gi + 0.5) * 0.25
                or float(row["lon"]) != (gj + 0.5) * 0.25):
            differences.append({"gi": gi, "gj": gj, "actual": row,
                                "expected_score": expected_score, "expected_n_species": expected_species,
                                "expected_normal": expected_normal})
    assert not differences, f"Whole-layer differences: {differences[:10]}"
    clip = {row["listing_id"]: row for row in read_csv(paths["production_listing_audit"])}
    assert set(clip) == {row["listing_id"] for row in result["listings"]}
    for lid, (kept, total) in result["audit"].items():
        assert int(clip[lid]["records_kept"]) == kept, f"Listing {lid} kept count differs"
        assert int(clip[lid]["records_species"]) == total, f"Listing {lid} species count differs"
    for lid in result["dropped"]:
        assert int(clip[lid]["records_kept"]) == 0 and clip[lid]["records_species"] != ""
    matched = set(result["audit"])
    for lid in set(clip) - matched:
        assert clip[lid]["records_kept"] == clip[lid]["records_species"] == ""
    flagship = {"piping_plover": ["454", "483"], "spotted_owl": ["507"], "greater_sage_grouse": ["204"]}
    flagship_kept = {species: {lid: int(clip[lid]["records_kept"]) for lid in lids}
                     for species, lids in flagship.items()}
    assert all(any(value > 0 for value in kept.values()) for kept in flagship_kept.values())
    provenance = json.loads(paths["production_provenance"].read_text())
    applied = provenance["shipping_policy"]
    assert applied["applied"] is True and applied["method"] == "allowlist"
    assert provenance["doi"] == "10.15468/dl.6hqdyd"
    assert provenance["download_key"] == result["policy"]["download_key"]
    assert set(applied["dropped_listing_ids"]) == result["dropped"]
    assert set(applied["required_polygon_listing_ids"]) == result["guarded"]
    assert set(provenance["ranges"]["effective_clipped_listing_ids"]) == result["guarded"]
    after = {key: sha256(path) for key, path in paths.items()}
    assert before == after, "Production inputs/outputs changed during independent audit"
    score = int(cells.loc[(170, -322), "sum"])
    result = {"purpose": "Independent whole-layer selected-production verification",
              "implementation": "No imports or calls to atrisk_shipping or atrisk_ranges scoring/selection functions; independent key graph, Shapely predicates and pandas max/sum aggregation",
              "status": "PASS", "script_sha256": sha256(__file__),
              "input_output_hashes": {key: {"path": str(path), "sha256": before[key]}
                                     for key, path in paths.items()},
              "inputs_and_outputs_stable_during_audit": True,
              "cells_checked": len(cells), "maximum_score": maximum,
              "score_raw_n_species_normalization_and_centres_match_all_cells": True,
              "listing_audit_matches_all_matched_and_unmatched_listings": True,
              "drop_count": len(result["dropped"]), "dropped_listing_ids": sorted(result["dropped"], key=int),
              "dropped_rows_keep_zero_with_full_species_totals": True,
              "polygon_guarded_ids": sorted(result["guarded"], key=int),
              "flagships": flagship_kept, "stats": result["stats"],
              "synthetic_validation": result["validation"],
              "long_point": {"gi": 170, "gj": -322, "score_raw": score,
                             "rank": 1 + int((cells["sum"] > score).sum())}}
    Path(args.report).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "cells_checked", "maximum_score", "drop_count", "polygon_guarded_ids", "stats", "long_point")}, indent=2))
    print(f"wrote {args.report}")


if __name__ == "__main__":
    main()
