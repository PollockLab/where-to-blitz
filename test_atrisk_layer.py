"""Offline test of the GBIF SQL-download TSV -> per-cell score aggregation."""

import csv
import io

import build_atrisk_layer as bal
from build_atrisk_layer import (
    RES,
    aggregate,
    build_sql,
    count_mismatches,
    crosswalk_diff,
    diff_blocks,
    species_totals,
)

HDR = "specieskey\tgi\tgj\tn\n"


def rows(tsv):
    return list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))


def test_species_in_two_cells_adds_weight_once_per_cell():
    score, nsp = aggregate(rows(HDR + "1\t10\t-5\t400\n1\t11\t-5\t2\n"), {"1": 3})
    assert score == {(10, -5): 3, (11, -5): 3}
    assert nsp == {(10, -5): 1, (11, -5): 1}


def test_two_species_in_one_cell_sum():
    score, nsp = aggregate(rows(HDR + "1\t10.0\t-5.0\t1\n2\t10\t-5\t9\n"), {"1": 3, "2": 1})
    assert score[(10, -5)] == 4
    assert nsp[(10, -5)] == 2


def test_key_without_weight_ignored():
    score, nsp = aggregate(rows(HDR + "1\t10\t-5\t1\n99\t10\t-5\t1\n"), {"1": 2})
    assert score == {(10, -5): 2} and nsp == {(10, -5): 1}


def test_empty_input():
    score, nsp = aggregate(rows(HDR), {"1": 3})
    assert not score and not nsp
    assert not aggregate([], {})[0]


def test_sql_derived_from_res_and_keys():
    sql = build_sql({"4RF6J", "3"})
    assert f"FLOOR(decimallatitude / {RES})" in sql and "AND specieskey IN ('3', '4RF6J')" in sql
    assert "SELECT specieskey AS specieskey" in sql and "GROUP BY specieskey," in sql
    assert "gbif_classification" not in sql
    assert '"year" >= 1990' in sql
    assert "NOT IN ('FOSSIL_SPECIMEN', 'LIVING_SPECIMEN')" in sql


def test_species_totals_sum_cells():
    assert species_totals(rows(HDR + "1\t10\t-5\t4\n1\t11\t-5\t2\n2\t10\t-5\t1\n")) == {"1": 6, "2": 1}
    assert species_totals(rows(HDR)) == {}


def test_count_mismatches_flags_missing_and_drift():
    # key 3 is in the API but absent from the download: the empty-filter failure the check exists for
    bad = count_mismatches({"A": 100, "B": 50}, {"A": 103, "B": 70, "C": 40})
    assert bad == {"B": (50, 70), "C": (0, 40)}
    assert count_mismatches({"A": 1}, {"A": 3, "D": 0}) == {}  # within the 2-record floor


def test_write_layer_keeps_every_cell(tmp_path, monkeypatch):
    out = tmp_path / "atrisk.csv"
    monkeypatch.setattr(bal, "OUT_CSV", str(out))
    bal.write_layer({(10, -5): 4.0, (-1, 2): 2.0}, {(10, -5): 2, (-1, 2): 1})
    got = {(int(r["gi"]), int(r["gj"])): r for r in csv.DictReader(open(out))}
    assert set(got) == {(10, -5), (-1, 2)}
    assert (
        float(got[(10, -5)]["conservation_norm"]) == 1.0 and float(got[(-1, 2)]["conservation_norm"]) == 0.5
    )
    assert float(got[(-1, 2)]["lat"]) == -0.5 * RES and float(got[(10, -5)]["lon"]) == -4.5 * RES


# crosswalk diff gate: {name: (key, weight)}
OLD = {"a": ("1", 3), "b": ("2", 2), "c": ("3", 1), "d": ("3", 1), "e": ("", 1)}


def test_diff_no_change_even_when_keys_are_renamed():
    new = {"a": ("X", 3), "b": ("Y", 2), "c": ("Z", 1), "d": ("Z", 1), "e": ("", 1)}
    d = crosswalk_diff(OLD, new)
    assert not diff_blocks(d)
    assert not any(d.values())


def test_diff_merge_with_different_weights_blocks():
    d = crosswalk_diff(OLD, {**OLD, "b": ("1", 2)})  # b joins a (weights 3 and 2)
    assert diff_blocks(d)
    assert d["merged_diff_weight"] == [("1", ["a", "b"], [2, 3])]


def test_diff_merge_with_same_weight_is_reported_not_blocking():
    old = {"a": ("1", 2), "b": ("2", 2)}
    d = crosswalk_diff(old, {"a": ("1", 2), "b": ("1", 2)})
    assert not diff_blocks(d) and d["merged_same_weight"] == [("1", ["a", "b"], [2])]


def test_diff_new_name_joining_existing_key_with_other_weight_blocks():
    d = crosswalk_diff(OLD, {**OLD, "e": ("1", 1)})  # unmatched e now shares a's key, weights 3 vs 1
    assert diff_blocks(d) and d["merged_diff_weight"][0][1] == ["a", "e"]


def test_diff_split_blocks():
    d = crosswalk_diff(OLD, {**OLD, "d": ("9", 1)})  # c and d no longer share a key
    assert diff_blocks(d) and d["split"] == [("3", ["c", "d"], ["3", "9"])]


def test_diff_lost_match_blocks_and_missing_name_counts():
    d = crosswalk_diff(OLD, {**OLD, "a": ("", 3)})
    assert diff_blocks(d) and d["lost"] == ["a"]
    gone = {k: v for k, v in OLD.items() if k != "b"}
    assert crosswalk_diff(OLD, gone)["lost"] == ["b"]


def test_diff_gained_name_does_not_block():
    d = crosswalk_diff(OLD, {**OLD, "e": ("7", 1)})
    assert not diff_blocks(d) and d["gained"] == ["e"]


def test_diff_empty_inputs():
    assert not diff_blocks(crosswalk_diff({}, {}))
    assert not diff_blocks(crosswalk_diff({}, OLD))
    assert crosswalk_diff(OLD, {})["lost"] == ["a", "b", "c", "d"]
