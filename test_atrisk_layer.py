"""Offline tests of the at-risk SQL, download totals, layer writer, alias parsing and crosswalk gates."""

import csv
import io

import build_atrisk_layer as bal
from atrisk_crosswalk import crosswalk_diff, diff_blocks
from atrisk_ranges import read_columns
from build_atrisk_layer import FINE, RES, build_sql, count_mismatches, edits, species_totals, variant_aliases

HDR = "specieskey\tacceptedtaxonkey\tyi\txi\tn\n"


def cols(tsv):
    return read_columns(io.StringIO(tsv))


def test_sql_derived_from_res_and_keys():
    sql = build_sql({"4RF6J", "3"})
    assert f"FLOOR(decimallatitude / {FINE})" in sql and "AND specieskey IN ('3', '4RF6J')" in sql
    assert "acceptedtaxonkey AS acceptedtaxonkey" in sql and "GROUP BY specieskey, acceptedtaxonkey," in sql
    assert "SELECT specieskey AS specieskey" in sql and "GROUP BY specieskey," in sql
    assert "gbif_classification" not in sql
    assert '"year" >= 1990' in sql
    assert "NOT IN ('FOSSIL_SPECIMEN', 'LIVING_SPECIMEN')" in sql


def test_species_totals_sum_cells():
    got = species_totals(cols(HDR + "1\t1\t10\t-5\t4\n1\tS\t11\t-5\t2\n2\t2\t10\t-5\t1\n"))
    assert got == {"1": 6, "2": 1}
    assert species_totals(cols(HDR)) == {}


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


def alt(
    key,
    mt,
    rank="SPECIES",
    status="ACCEPTED",
    genus="Zeltnera",
    author="(Griseb.) G.Mans.",
    ep="muhlenbergii",
):
    usage = {
        "key": key,
        "rank": rank,
        "status": status,
        "genericName": genus,
        "authorship": author,
        "specificEpithet": ep,
    }
    return {"usage": usage, "diagnostics": {"matchType": mt}}


def test_variant_aliases_keep_orthographic_duplicates_only():
    m = {
        "usage": alt("TVMH7", "EXACT", ep="muehlenbergii")["usage"],
        "diagnostics": {
            "alternatives": [
                alt("5CZX8", "VARIANT"),
                alt("TVMH7", "VARIANT"),  # the matched key itself
                alt("SUB1", "VARIANT", rank="SUBSPECIES"),
                alt("SYN1", "VARIANT", status="SYNONYM"),
                alt("FUZ1", "FUZZY"),
                alt("ULUS", "VARIANT", genus="Ulus"),  # near-homonym in another genus
                alt("OTHR", "VARIANT", author="Raf."),  # same genus, different species
                alt("PINN", "VARIANT", ep="mehlenbergiiata"),  # same genus and author, other epithet
            ]
        },
    }
    assert variant_aliases(m, "TVMH7") == ["5CZX8"]
    assert edits("caroliniana", "caroliana") == 2 and edits("pedata", "pinnata") == 3 and edits("", "ab") == 2
    assert variant_aliases({}, "TVMH7") == [] and variant_aliases({"diagnostics": {}}, "X") == []


def cw(name, col_key, bb_key, weight=2, acc="", mt="EXACT"):
    return {
        "name": name,
        "col_key": col_key,
        "col_accepted_name": acc or name,
        "col_matchType": mt,
        "backbone_key": bb_key,
        "status_weight": str(weight),
    }


def test_count_changes_flags_both_directions_and_zero():
    from compare_atrisk_taxonomy import count_changes

    rows = [
        cw("Ixobrychus exilis", "CR94R", "2480852", acc="Botaurus exilis"),  # 70x up
        cw("Centaurium muehlenbergii", "5CZX8", "9541322", 3),  # to zero
        cw("Bombus affinis", "B1", "11"),  # 5x, under the threshold
        cw("Lupinus densiflorus", "", "2964246", 3, mt="HIGHERRANK"),  # lost COL key
        cw("Sebastes sp.type 1", "", "", 1),  # no key either side
        cw("Gone", "G1", "12"),  # zero on both sides
    ]
    bb = {"2480852": 355, "9541322": 34, "11": 10, "2964246": 352}
    col = {"CR94R": 24852, "B1": 50}
    got = {d["name"]: d for d in count_changes(rows, bb, col)}
    assert set(got) == {"Ixobrychus exilis", "Centaurium muehlenbergii", "Lupinus densiflorus"}
    assert got["Ixobrychus exilis"]["ratio"] == 24852 / 355 and got["Ixobrychus exilis"]["in_layer"]
    assert (
        got["Centaurium muehlenbergii"]["ratio"] == 0
        and got["Centaurium muehlenbergii"]["status"] == "Endangered"
    )
    assert not got["Lupinus densiflorus"]["in_layer"]
    assert count_changes([], {}, {}) == []
