"""Crosswalk diff gate for build_atrisk_layer.py: compares a re-matched name -> species key crosswalk with the
committed one by name partition, so a re-match that merges names of different status weights, splits a key,
or loses a matched name is caught before it reaches the layer."""

import csv
import os
from collections import defaultdict


def read_crosswalk_rows(path):
    """Committed crosswalk -> ({name: (key, weight)}, {name: backbone key}); key '' when unmatched.
    Reads the old Backbone schema (speciesKey) too. Both empty when the file is absent."""
    if not os.path.exists(path):
        return {}, {}
    rows, bb = {}, {}
    with open(path) as f:
        for r in csv.DictReader(f):
            new_schema = "col_key" in r
            rows[r["name"]] = (
                (r["col_key"] if new_schema else r["speciesKey"]) or "",
                int(r["status_weight"]),
            )
            bb[r["name"]] = (r["backbone_key"] if new_schema else r["speciesKey"]) or ""
    return rows, bb


def crosswalk_diff(old, new):
    """Compare two {name: (key, weight)} crosswalks by name partition (keys may live in different
    taxonomies). -> dict of lists: merged_* (a new key joins names that were not one group before, split
    by whether their weights differ), split (an old key whose names now sit under several keys), lost
    (names matched before and not now), gained (names matched now and not before)."""
    d = {"merged_diff_weight": [], "merged_same_weight": [], "split": [], "lost": [], "gained": []}
    by_new, by_old = defaultdict(list), defaultdict(list)
    for n, (k, _) in new.items():
        if k:
            by_new[k].append(n)
    for n, (k, _) in old.items():
        if k:
            by_old[k].append(n)
    for k, names in sorted(by_new.items()):
        olds = {old.get(n, ("", 0))[0] for n in names}
        if len(names) < 2 or (len(olds) == 1 and "" not in olds):
            continue  # one name, or already one group in the old crosswalk
        weights = {new[n][1] for n in names}
        d["merged_diff_weight" if len(weights) > 1 else "merged_same_weight"].append(
            (k, sorted(names), sorted(weights))
        )
    for k, names in sorted(by_old.items()):
        news = {new[n][0] for n in names if n in new and new[n][0]}
        if len(news) > 1:
            d["split"].append((k, sorted(names), sorted(news)))
    d["lost"] = sorted(n for n, (k, _) in old.items() if k and not new.get(n, ("", 0))[0])
    d["gained"] = sorted(n for n, (k, _) in new.items() if k and not old.get(n, ("", 0))[0])
    return d


def diff_blocks(d):
    """True when the diff needs --accept-crosswalk-changes."""
    return bool(d["merged_diff_weight"] or d["split"] or d["lost"])


def print_diff(d):
    print("crosswalk diff vs committed crosswalk:")
    for title, key in (
        ("MERGE, different status weights (blocking)", "merged_diff_weight"),
        ("merge, same status weight", "merged_same_weight"),
        ("SPLIT (blocking)", "split"),
    ):
        print(f"  {title}: {len(d[key])}")
        for k, names, extra in d[key]:
            print(f"    {k}: {names} -> {extra}")
    for title, key in (
        ("LOST previously matched names (blocking)", "lost"),
        ("newly matched names", "gained"),
    ):
        print(f"  {title}: {len(d[key])}")
        for n in d[key]:
            print(f"    {n}")
