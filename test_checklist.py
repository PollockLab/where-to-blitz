"""The checklist layer: Chao2 arithmetic, observer-day units, taxa roll-up and pixel encoding."""

import json
import os

import numpy as np
import pandas as pd
import pytest

import build_checklist as bc

OUT = os.path.join(os.path.dirname(__file__), "cluster_results", "checklist")


def test_chao2_known_value():
    # S_obs=10, Q1=4, Q2=2, m=20: 10 + (19/20) * 4*3 / (2*3) = 11.9
    assert bc.chao2(10, 4, 2, 20) == pytest.approx(11.9)


def test_chao2_no_singletons_is_complete():
    assert bc.chao2(7, 0, 3, 15) == 7
    assert bc.chao2(7, 1, 3, 15) == 7  # one singleton: Q1 (Q1 - 1) = 0


def test_chao2_single_unit_adds_nothing():
    assert bc.chao2(5, 5, 0, 1) == 5


def test_observer_day_is_the_unit():
    # Cell 0: observer 1 on day 1 logs species A three times and B once -> one unit {A, B}.
    # Observer 1 on day 2 logs A; observer 2 on day 1 logs C. Units: 3. q: A=2, B=1, C=1.
    cell = [0, 0, 0, 0, 0, 0, 9]
    observer = [1, 1, 1, 1, 1, 2, 1]
    day = [1, 1, 1, 1, 2, 1, 1]
    species = [10, 10, 10, 11, 10, 12, 10]
    cells, m, s_obs, q1, q2 = bc.completeness_by_cell(cell, observer, day, species)
    assert cells.tolist() == [0, 9]
    assert m.tolist() == [3, 1]
    assert s_obs.tolist() == [3, 1]
    assert q1.tolist() == [2, 1]
    assert q2.tolist() == [1, 0]


def test_species_lookup_rolls_up_and_assigns_group():
    taxa = pd.DataFrame(
        {
            "taxon_id": [48460, 1, 47158, 100, 101, 102, 47126, 200],
            "ancestry": [
                None,
                "48460",
                "48460/1",
                "48460/1/47158",
                "48460/1/47158/100",
                "48460/1/47158",
                "48460",
                "48460/47126",
            ],
            "rank_level": [100, 70, 50, 10, 5, 20, 70, 10],
        }
    )
    sp, grp = bc.species_lookup(taxa)
    assert sp[100] == 100 and grp[100] == bc.GROUP_CODE["Insecta"]
    assert sp[101] == 100 and grp[101] == bc.GROUP_CODE["Insecta"]  # subspecies -> species
    assert sp[102] == 0  # genus: dropped
    assert sp[200] == 200 and grp[200] == bc.GROUP_CODE["Plantae"]


class _Lat:
    nrow, ncol = 2, 3


def test_encode_round_trip_and_hidden():
    hidden = np.zeros((2, 3), bool)
    hidden[1, 2] = True
    rgba = bc.encode(_Lat, np.array([0, 4, 5]), np.array([0.913, 1.0, 0.5]), np.array([12, 70000, 3]), hidden)
    comp, units, present = bc.decode(rgba)
    assert present.tolist() == [[True, False, False], [False, True, False]]
    assert comp[0, 0] == pytest.approx(0.912)  # round(0.913*250)=228 -> 0.912
    assert units[0, 0] == 12 and units[1, 1] == 65535  # capped
    assert comp[1, 1] == 1.0


def test_status_counts_respect_min_units():
    comp = np.array([0.95, 0.95, 0.8, 0.5])
    m = np.array([bc.MIN_UNITS, bc.MIN_UNITS - 1, 50, 50])
    assert bc.status_counts(comp, m) == {"complete": 1, "close": 1, "far": 1, "too_few": 1}


@pytest.mark.skipif(not os.path.exists(os.path.join(OUT, "checklist.json")), reason="layer not built")
def test_shipped_sidecar_matches_pngs():
    doc = json.load(open(os.path.join(OUT, "checklist.json")))
    assert doc["bands"] == bc.BANDS and doc["min_units"] == bc.MIN_UNITS
    for tier in doc["tiers"].values():
        assert set(tier["groups"]) == set(bc.GROUP_TO_COG)
        for g in tier["groups"].values():
            assert bc.sha256_file(os.path.join(OUT, g["png"])) == g["sha256"]
