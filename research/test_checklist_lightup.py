"""The light-up view's dating, climate-strata and per-tier cell helpers."""
from types import SimpleNamespace

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


def test_tier_cells_lists_every_canadian_cell_including_unrecorded(monkeypatch):
    # 3 x 4 lattice of 5 km cells; the last column is outside Canada.
    lat = SimpleNamespace(res=5000, x0=0.0, y1=15000.0, nrow=3, ncol=4)
    hidden = np.zeros((3, 4), bool)
    hidden[:, 3] = True
    monkeypatch.setattr(cl.bc, "tier_grid", lambda res, crs: (lat, hidden))
    # Cell 0 gets 30 observer-days of one species, cell 5 one record, cell 3 (hidden) one record.
    n = 32
    rec = {"row": np.array([0] * 30 + [1, 0]), "col": np.array([0] * 30 + [1, 3]), "acc": np.full(n, np.nan),
           "group": np.zeros(n, int), "observer": np.arange(n), "day": np.arange(n), "species": np.zeros(n, int)}
    _, _, df = cl.tier_cells(rec, 5000, None, "All biodiversity")
    assert sorted(df.cell) == [c for c in range(12) if c % 4 != 3]
    by = df.set_index("cell")
    assert by.loc[0, "m"] == 30 and by.loc[0, "effort"] and by.loc[5, "m"] == 1
    empty = by.drop([0, 5])
    assert (empty.m == 0).all() and empty.comp.isna().all() and not (empty.chao2 | empty.effort).any()
    r, c = np.divmod(df.cell.to_numpy(), lat.ncol)
    assert ((df.x > c * 5000) & (df.x < (c + 1) * 5000) & (df.y < 15000 - r * 5000) & (df.y > 10000 - r * 5000)).all()
