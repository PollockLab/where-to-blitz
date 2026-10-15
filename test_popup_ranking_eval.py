"""Boundary and leakage checks for the exploratory popup ordering harness."""

from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest_appscore import AppGrid
from research.popup_ranking_eval import (
    GEOD,
    attainable_diagnostic,
    candidate_pool,
    cell_bbox,
    evaluate,
    expected_yield,
    prepare,
    rankings,
    source_hashes,
    training_state,
    within_radius,
)


@pytest.fixture(scope="module")
def grid():
    return AppGrid("All biodiversity")


def observations(taxa=(1, 1, 1), dates=None, longitude=-79.4, latitude=43.7):
    dates = dates or ["2025-06-01", "2025-06-02", "2025-06-03"]
    return pd.DataFrame({"id": range(len(taxa)), "lon": longitude, "lat": latitude,
                         "observed_on": [dates[i % len(dates)] for i in range(len(taxa))],
                         "taxon_id": taxa, "taxon_name": ["Testus species"] * len(taxa),
                         "rank": "species", "obs_count": 100, "threatened": False,
                         "ancestry": "48460/1", "date": pd.to_datetime(
                             [dates[i % len(dates)] for i in range(len(taxa))]),
                         "fine_row": np.arange(len(taxa)), "fine_col": 0})


def test_zero_vs_one_local_record_and_common_pool():
    nearby = observations((1, 1, 1, 2, 2, 2, 3, 3))
    pool = candidate_pool(nearby, set(), {1, 2, 3})
    assert {row["taxon_id"] for row in pool} == {1, 2}
    assert [row["taxon_id"] for row in candidate_pool(nearby, {1}, {1, 2, 3})] == [2]
    assert candidate_pool(nearby, set(), set()) == []
    for selected in rankings(pool).values():
        assert {row["taxon_id"] for row in selected} == {1, 2}


def test_rankings_top_four_ties_and_support():
    pool = [{"taxon_id": taxon, "count": 3 if taxon < 5 else 10,
             "replicated": taxon >= 4} for taxon in range(1, 7)]
    result = rankings(pool)
    assert [r["taxon_id"] for r in result["rarest"]] == [1, 2, 3, 4]
    assert [r["taxon_id"] for r in result["frequency"]] == [5, 6, 1, 2]
    assert [r["taxon_id"] for r in result["replicated_rare"]] == [4, 5, 6, 1]
    assert rankings([]) == {"rarest": [], "frequency": [], "replicated_rare": []}
    nearby = observations()
    assert candidate_pool(nearby, set(), {1})[0]["replicated"]
    nearby["fine_row"] = 0
    assert not candidate_pool(nearby, set(), {1})[0]["replicated"]
    nearby["fine_row"] = [0, 1, 2]
    nearby["date"] = pd.Timestamp("2025-06-01")
    assert not candidate_pool(nearby, set(), {1})[0]["replicated"]


def test_geodesic_radius_boundary_and_empty():
    centre = (-79.4, 43.7)
    frame = observations()
    lon, lat, _ = GEOD.fwd([centre[0]] * 3, [centre[1]] * 3, [0, 120, 240],
                          [39999, 40000, 40001])
    frame["lon"], frame["lat"] = lon, lat
    assert within_radius(frame, centre[1], centre[0]).id.tolist() == [0, 1]
    assert within_radius(frame.iloc[:0], centre[1], centre[0]).empty
    with pytest.raises(ValueError):
        within_radius(frame, 43, -79, -1)


def test_exact_expectation_matches_enumeration_and_boundaries():
    records = [1, 1, 2, 3, 4, 4]
    expected = np.mean([len(set(draw) & {1, 4}) for draw in combinations(records, 5)])
    assert expected_yield([1, 4], records) == pytest.approx(expected)
    assert expected_yield([], records) == 0
    assert expected_yield([1], [], 0) == 0
    assert expected_yield([1], records[:4]) is None
    assert expected_yield([1, 1], records) == expected_yield([1], records)
    with pytest.raises(ValueError):
        expected_yield([1], records, -1)


def test_training_incidence_eligibility_do_not_use_future_metadata(grid):
    raw = observations((1, 1, 2), ["2025-06-01"])
    raw.loc[2, "taxon_name"] = "Otherus species"
    state = training_state(prepare(raw, grid))
    future = observations((3,), ["2025-07-01"])
    future["id"] = 999
    future["taxon_name"] = "Futureus species"
    future["threatened"] = True
    combined = pd.concat([raw, future], ignore_index=True)
    after = training_state(prepare(combined, grid))
    assert state[1:4] == after[1:4]
    assert np.array_equal(state[4], after[4])
    # A later record cannot change training eligibility for an existing taxon.
    combined.loc[3, "taxon_id"] = 1
    combined.loc[3, "taxon_name"] = "Testus species"
    assert training_state(prepare(combined, grid))[2] == state[2]


def test_supported_infraspecific_parent_prevents_false_gap(grid):
    raw = observations((11, 12), ["2025-06-01"])
    raw.loc[1, "taxon_name"] = "Testus species subspecies"
    raw.loc[1, "rank"] = "subspecies"
    raw.loc[1, "ancestry"] = "48460/1/11"
    normalized = prepare(raw, grid)
    assert normalized.taxon_id.tolist() == [11, 11]
    assert normalized.attrs["normalized_infraspecific_records"] == 1
    raw.loc[1, "ancestry"] = "48460/1/99"
    assert len(prepare(raw, grid)) == 1


def test_bbox_includes_neighbour_corner_and_excludes_outside(grid):
    row, col = grid.locate(np.array([43.7]), np.array([-79.4]))
    key = (int(row[0]), int(col[0]))
    x, y = grid.lattice.centres([key[0]], [key[1]])
    half = 12500
    lon, lat = grid.inverse.transform([x[0]-half, x[0]-half, x[0]+half, x[0]+half],
                                       [y[0]-half, y[0]+half, y[0]-half, y[0]+half])
    # A geographic rectangle corner generally lies outside the projected square.
    points = pd.DataFrame({"lat": [min(lat), max(lat)+0.001],
                           "lon": [min(lon), min(lon)], "id": [1, 2]})
    assert cell_bbox(points, grid, key).id.tolist() == [1]
    rr, cc = grid._project(points.lat.values[:1], points.lon.values[:1])
    assert (rr[0], cc[0]) != key


def test_empty_evaluation_and_no_future_outcome(grid):
    raw = observations()
    empty = raw.iloc[:0]
    result = evaluate(empty, grid)
    assert result["target_cells"] == 0
    result = evaluate(raw, grid)
    assert result["target_cells"] == 1
    assert result["future_5_record_cells"] == 0
    assert result["arms"]["rarest"]["all_targets"]["mean_cards"] == 0


def test_attainable_oracle_is_labeled_and_bounds_arm_yield():
    pool = [{"taxon_id": taxon, "count": 3, "replicated": False} for taxon in range(1, 7)]
    future = [1, 2, 3, 4, 5, 5, 6, 6, 6]
    result = attainable_diagnostic(pool, future, {1: 1, 2: 1, 3: 2, 4: 3, 5: 4, 6: 4})
    assert result["q1_candidates"] == 2
    assert result["full_pool_q1"] == expected_yield([1, 2], future)
    assert result["outcome_informed_oracle_top4_total"] == expected_yield([6, 5, 1, 2], future)
    assert result["full_pool_total"] >= result["outcome_informed_oracle_top4_total"]
    for order in rankings(pool).values():
        assert expected_yield([row["taxon_id"] for row in order], future) <= result["outcome_informed_oracle_top4_total"]
    assert attainable_diagnostic(pool, [], {1: 1})["outcome_informed_oracle_top4_total"] is None


def test_attainable_coverage_is_not_future_conditioned(grid):
    local = observations((1,), ["2025-06-01"])
    row, col = grid.locate(local.lat.values, local.lon.values)
    lat, lon = grid.centres(row, col)
    # A point30km east is outside the local bbox but within the40km circle.
    nlon, nlat, _ = GEOD.fwd(lon[0], lat[0], 90, 30000)
    nearby = observations((2, 2, 2), longitude=nlon, latitude=nlat)
    nearby["id"] += 10
    nearby["taxon_name"] = "Otherus species"
    raw = pd.concat([local, nearby], ignore_index=True)
    before = evaluate(raw, grid)
    future = observations((2,) * 5, ["2025-07-01"])
    future["id"] += 20
    future["taxon_name"] = "Otherus species"
    after = evaluate(pd.concat([raw, future], ignore_index=True), grid)
    assert before["attainable_pool_diagnostic_only"]["all_targets"] == after["attainable_pool_diagnostic_only"]["all_targets"]
    assert before["target_cells"] == after["target_cells"]
    diagnostic = after["attainable_pool_diagnostic_only"]
    assert diagnostic["all_targets"]["cells_with_q1_candidates"] > 0
    assert diagnostic["future_5_record_revisits"]["expected_yield_at_5"]["outcome_informed_oracle_top4_q1"] == 1


def test_source_hash_keys_are_checkout_relative():
    hashes = source_hashes()
    assert set(hashes) == {"research/popup_ranking_eval.py", "backtest_appscore.py",
                           "voi_backtest.py", "grid_lattice.py"}
    assert all(not Path(key).is_absolute() and len(value) == 64 for key, value in hashes.items())
