"""Offline tests of taxonomy count diagnostics and final layer membership."""

import csv
import json

import pytest

import compare_atrisk_taxonomy as taxonomy
from atrisk_ranges import LISTING_COLS


def row(name="Species", col_key="COL", alias_keys="", backbone_key="BB"):
    return {
        "name": name,
        "status_weight": "1",
        "col_key": col_key,
        "alias_keys": alias_keys,
        "backbone_key": backbone_key,
        "col_accepted_name": name,
        "col_matchType": "EXACT",
    }


def test_alias_records_prevent_false_complete_loss():
    centaurium = row("Centaurium muehlenbergii", "5CZX8", "TVMH7")
    assert taxonomy.count_changes([centaurium], {"BB": 34}, {"TVMH7": 34}) == []


def test_primary_and_alias_keys_are_summed_once():
    species = row(alias_keys="COL ALIAS ALIAS OTHER")
    changes = taxonomy.count_changes([species], {"BB": 1}, {"COL": 3, "ALIAS": 4, "OTHER": 5})
    assert len(changes) == 1
    assert changes[0]["col_records"] == 12
    assert changes[0]["ratio"] == 12


def test_empty_keys_do_not_count_records():
    species = row(col_key="", alias_keys="  ", backbone_key="")
    assert taxonomy.count_changes([species], {"": 12}, {"": 34}) == []


def test_empty_primary_key_does_not_hide_nonempty_alias():
    species = row(col_key="", alias_keys="ALIAS ALIAS")
    changes = taxonomy.count_changes([species], {"BB": 1}, {"": 1000, "ALIAS": 12})
    assert changes[0]["col_records"] == 12
    assert changes[0]["in_layer"] is False


@pytest.mark.parametrize("selected_names,expected", [(None, True), (set(), False), ({"Species"}, True)])
def test_final_selection_controls_layer_membership(selected_names, expected):
    changes = taxonomy.count_changes([row()], {"BB": 12}, {}, selected_names=selected_names)
    assert changes[0]["in_layer"] is expected


def test_selection_cannot_include_unmatched_name():
    changes = taxonomy.count_changes([row(col_key="")], {"BB": 12}, {}, selected_names={"Species"})
    assert changes[0]["in_layer"] is False


@pytest.mark.parametrize("bb_count,col_count", [(1, 10), (10, 1)])
def test_exact_tenfold_change_is_not_flagged(bb_count, col_count):
    assert taxonomy.count_changes([row()], {"BB": bb_count}, {"COL": col_count}) == []


def test_selected_listing_names_reads_final_shipping_policy(tmp_path):
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"shipping_policy": {"dropped_listing_ids": ["2"]}}))
    listings = tmp_path / "listings.csv"
    with listings.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LISTING_COLS)
        writer.writeheader()
        for lid, names in [("1", "Retained;Shared"), ("2", "Dropped;Shared")]:
            writer.writerow({"listing_id": lid, "names": names, "status_weight": 1})
    assert taxonomy.selected_listing_names(provenance, listings) == {"Retained", "Shared"}


def test_missing_policy_preserves_default_membership(tmp_path):
    provenance = tmp_path / "provenance.json"
    assert taxonomy.selected_listing_names(provenance) is None
    provenance.write_text("{}")
    assert taxonomy.selected_listing_names(provenance) is None
