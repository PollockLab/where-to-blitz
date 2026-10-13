"""Offline test of the GBIF SQL-download TSV -> per-cell score aggregation."""

import csv
import io

from build_atrisk_layer import RES, aggregate, build_sql

HDR = "specieskey\tgi\tgj\tn\n"


def rows(tsv):
    return list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))


def test_species_in_two_cells_adds_weight_once_per_cell():
    score, nsp = aggregate(rows(HDR + "1\t10\t-5\t400\n1\t11\t-5\t2\n"), {1: 3})
    assert score == {(10, -5): 3, (11, -5): 3}
    assert nsp == {(10, -5): 1, (11, -5): 1}


def test_two_species_in_one_cell_sum():
    score, nsp = aggregate(rows(HDR + "1\t10.0\t-5.0\t1\n2\t10\t-5\t9\n"), {1: 3, 2: 1})
    assert score[(10, -5)] == 4
    assert nsp[(10, -5)] == 2


def test_key_without_weight_ignored():
    score, nsp = aggregate(rows(HDR + "1\t10\t-5\t1\n99\t10\t-5\t1\n"), {1: 2})
    assert score == {(10, -5): 2} and nsp == {(10, -5): 1}


def test_empty_input():
    score, nsp = aggregate(rows(HDR), {1: 3})
    assert not score and not nsp
    assert not aggregate([], {})[0]


def test_sql_derived_from_res_and_keys():
    sql = build_sql({7, 3})
    assert f"FLOOR(decimallatitude / {RES})" in sql and "gbif_classification.specieskey IN ('3', '7')" in sql
    assert '"year" >= 1990' in sql
