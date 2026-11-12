"""The light-up view's dating and climate-strata helpers."""
import checklist_lightup as cl
import numpy as np


def test_nth_unit_day_counts_distinct_observer_days():
    # Cell 1: observer 7 twice on day 3 is one unit, so units are days 3, 5, 9; the 2nd is day 5.
    # Cell 2 has a single unit and never reaches n = 2.
    got = cl.nth_unit_day([1, 1, 1, 1, 2], [7, 7, 8, 7, 7], [3, 3, 9, 5, 1], 2)
    assert got.to_dict() == {1: 5}


def test_nth_unit_day_empty():
    assert cl.nth_unit_day([], [], [], 30).empty


def test_stratum_edges_and_missing():
    i, j = cl.stratum(np.array([0, 10, 20, np.nan]), np.array([5, 5, 50, 5]), [10, 20], [5, 50])
    assert i.tolist() == [0, 1, 2, -1]  # a value on a cut point goes to the upper stratum
    assert j.tolist() == [1, 1, 2, -1]


def test_record_centroid_lies_inside_its_cell():
    # Two 25 km cells (k = 5): cell 0 holds 5 km cells (0,0) and (4,4), cell 1 holds (0,5).
    x0, y1, res = -100.0, 200.0, 25000
    cen = cl.record_centroids([0, 0, 1], [0, 4, 0], [0, 4, 5], x0, y1)
    for cell, (x, y) in cen.iterrows():
        left = x0 + cell * res
        assert left < x < left + res and y1 - res < y < y1
    assert cen.loc[0].tolist() == [x0 + 12500, y1 - 12500]  # mean of the two 5 km centres
