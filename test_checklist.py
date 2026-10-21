"""The checklist layer: Chao2 arithmetic, observer-day units, taxa roll-up and pixel encoding."""

import json
import os

import numpy as np
import pandas as pd
import pytest

import build_checklist as bc

OUT = os.path.join(os.path.dirname(__file__), "cluster_results", "checklist")


# iNEXT 3.0.2 reference values (ChaoRichness and Chat.Sam, unrounded), incidence_freq data: T, then
# per-species incidence counts. Fixture B has Q2 = 0; C has Q1 = 1, iNEXT's Poisson fallback interval.
INEXT = {
    "A": (12, [1, 1, 1, 2, 2, 3, 5, 8, 12], 11.0625, 9.241088183, 26.64460705, 0.9235521236),
    "B": (10, [1, 1, 1, 1, 3, 4], 11.4, 6.866540671, 39.65104602, 0.6614420063),
    "C": (30, [1, 2, 2, 4, 6, 9, 15, 20, 30, 30], 10.24166667, 10.01264637, 14.6181472, 0.9926152279),
    "E": (50, [1] * 20 + [2] * 5 + [3, 4, 6, 10, 25], 69.2, 42.20282, 155.9249911, 0.7461797462),
}


def _stats(t, counts):
    q = np.array(counts, float)
    return {
        "s_obs": len(q), "q1": (q == 1).sum(), "q2": (q == 2).sum(), "m": t, "u": q.sum(),
        "e1": np.exp(-q).sum(), "e2": np.exp(-2 * q).sum(), "qe1": (q * np.exp(-q)).sum(),
    }  # fmt: skip


@pytest.mark.parametrize("name", INEXT)
def test_matches_inext(name):
    t, counts, est, lo, hi, cov = INEXT[name]
    st = _stats(t, counts)
    assert bc.chao2(st["s_obs"], st["q1"], st["q2"], t) == pytest.approx(est, rel=1e-8)
    got_lo, got_hi = bc.chao2_interval(st["s_obs"], st["q1"], st["q2"], t, st["e1"], st["e2"], st["qe1"])
    assert (got_lo, got_hi) == pytest.approx((lo, hi), rel=1e-6)
    assert bc.coverage(st["q1"], st["q2"], st["u"], t) == pytest.approx(cov, rel=1e-8)


def test_no_singletons_is_complete_with_a_finite_interval():
    st = _stats(15, [2, 2, 3, 3, 5, 8, 15])
    assert bc.chao2(st["s_obs"], st["q1"], st["q2"], 15) == 7
    lo, hi = bc.chao2_interval(st["s_obs"], st["q1"], st["q2"], 15, st["e1"], st["e2"], st["qe1"])
    assert lo == 7 and 7 < hi < 9
    assert bc.coverage(st["q1"], st["q2"], st["u"], 15) == 1


def test_single_unit_adds_nothing():
    st = _stats(1, [1, 1, 1])
    assert bc.chao2(3, 3, 0, 1) == 3
    lo, hi = bc.chao2_interval(3, 3, 0, 1, st["e1"], st["e2"], st["qe1"])
    assert np.isfinite([lo, hi]).all() and lo == 3  # iNEXT returns NaN here
    assert bc.coverage(3, 0, 3, 1) == 1


def test_observer_day_is_the_unit():
    # Cell 0: observer 1 on day 1 logs species A three times and B once -> one unit {A, B}.
    # Observer 1 on day 2 logs A; observer 2 on day 1 logs C. Units: 3. q: A=2, B=1, C=1.
    cell = [0, 0, 0, 0, 0, 0, 9]
    observer = [1, 1, 1, 1, 1, 2, 1]
    day = [1, 1, 1, 1, 2, 1, 1]
    species = [10, 10, 10, 11, 10, 12, 10]
    cells, st = bc.completeness_by_cell(cell, observer, day, species)
    assert cells.tolist() == [0, 9]
    assert st["m"].tolist() == [3, 1]
    assert st["s_obs"].tolist() == [3, 1]
    assert st["q1"].tolist() == [2, 1]
    assert st["q2"].tolist() == [1, 0]
    assert st["u"].tolist() == [4, 1]


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
    comp, units, present, canadian = bc.decode(rgba)
    assert present.tolist() == [[True, False, False], [False, True, False]]
    assert canadian.tolist() == [[True, True, True], [True, True, False]]  # no records != outside
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
            assert bc.sha256_file(os.path.join(OUT, g["ci_png"])) == g["ci_sha256"]
