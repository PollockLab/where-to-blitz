"""Scientific and release boundaries of the BC plants pilot."""

import json
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from backtest_appscore import AppGrid
from evaluate_plant_pilot import evaluate, paired_diagnostic, training_state, validate_history
from fetch_plant_season import QUERY
from plant_pilot import (
    FEATURES,
    bc_frame,
    block_key,
    digest,
    fit_profiles,
    habitat_cells,
    make_artifact,
    outside_buffer,
    rank_pool,
    release_gate,
    score_species,
    seasonal_profiles,
    write_new,
)
from research.popup_ranking_eval import GEOD
from test_popup_evidence import _run_popup
from test_popup_ranking_eval import observations


@pytest.fixture(scope="module")
def grid():
    return AppGrid("All biodiversity")


def season_document():
    return {
        "schema_version": 1,
        "months": list(range(1, 13)),
        "species": [[1, [10] * 12], [2, [20] * 12]],
        "monthly_species_record_totals": [30] * 12,
    }


def features(value=0):
    return np.array([value, value, value, 1] + [0] * 10, dtype=float)


def model():
    return {
        "scaler": {"mean": [0] * 14, "scale": [1] * 14},
        "species": [
            {
                "id": taxon,
                "mean": features().tolist(),
                "variance": [1] * 14,
                "months": [1] * 12,
                "rarity": 1 / taxon,
                "support_cells": 3,
            }
            for taxon in range(1, 7)
        ],
    }


def test_season_normalizes_monthly_background_and_shrinks_sparse_species():
    assert np.allclose(seasonal_profiles(season_document())[1], 1)
    doc = season_document()
    doc["species"][0][1][6] = 100
    doc["monthly_species_record_totals"][6] = 120
    profiles = seasonal_profiles(doc)
    assert profiles[1][6] > profiles[1][0]
    assert profiles[2][6] < profiles[2][0]
    assert np.isfinite(profiles[1]).all()


@pytest.mark.parametrize("mutation", ["missing_month", "duplicate", "negative", "fractional", "mismatch"])
def test_season_rejects_unknown_coverage_and_corrupt_counts(mutation):
    doc = season_document()
    if mutation == "missing_month":
        doc["monthly_species_record_totals"][0] = 0
    elif mutation == "duplicate":
        doc["species"].append(doc["species"][0])
    elif mutation == "negative":
        doc["species"][0][1][0] = -1
    elif mutation == "fractional":
        doc["species"][0][1][0] = 0.5
    else:
        doc["monthly_species_record_totals"][0] += 1
    with pytest.raises(ValueError):
        seasonal_profiles(doc)


def habitat_document(grid):
    rr, cc = grid.locate(np.array([49]), np.array([-123]))
    key = int(rr[0]), int(cc[0])
    lat, lon = (float(v[0]) for v in grid.centres([key[0]], [key[1]]))
    return {
        "schema_version": 1,
        "row_format": ["row", "col", "lat", "lon"] + FEATURES,
        "cells": [[*key, lat, lon] + features().tolist()],
        "grid": deepcopy(grid.metadata),
        "sampling": {"points_per_cell": 225},
        "validity": [[*key, 225, 225, 225, 225]],
    }


def test_environment_loader_uses_explicit_keys_and_rejects_bad_cover(grid):
    doc = habitat_document(grid)
    key = tuple(doc["cells"][0][:2])
    assert np.array_equal(habitat_cells(doc, grid)[key], features())
    doc["cells"].append(doc["cells"][0])
    with pytest.raises(ValueError, match="duplicate"):
        habitat_cells(doc, grid)
    doc["cells"] = doc["cells"][:1]
    doc["cells"][0][-1] = 0.5
    with pytest.raises(ValueError, match="fractions"):
        habitat_cells(doc, grid)


@pytest.mark.parametrize("change", ["coordinates", "origin", "crs", "support"])
def test_environment_rejects_wrong_origin_coordinates_and_unknown_support(grid, change):
    doc = habitat_document(grid)
    if change == "coordinates":
        doc["cells"][0][2:4] = [89, 179]
    elif change == "origin":
        doc["grid"]["lattice"]["x0"] += 25000
    elif change == "crs":
        doc["grid"]["crs"] = "EPSG:4326"
    else:
        doc["validity"] = []
    with pytest.raises(ValueError):
        habitat_cells(doc, grid)


def test_incomplete_habitat_is_unknown_rather_than_complete_cover(grid):
    doc = habitat_document(grid)
    doc["validity"][0][-1] = 179
    key = tuple(doc["cells"][0][:2])
    assert np.isnan(habitat_cells(doc, grid)[key]).all()


@pytest.mark.parametrize("change", ["place", "privacy", "creation", "date", "boundary"])
def test_history_rejects_late_wrong_or_unlinked_sources(tmp_path, change):
    boundary = tmp_path / "bc.json"
    boundary.write_text("{}")
    doc = {"query": dict(QUERY), "manifest": {"boundary": {"output_sha256": digest(boundary)}}}
    assert validate_history(doc, boundary, "2025-07-01") == "2024-12-31"
    if change == "place":
        doc["query"]["place_id"] = 1
    elif change == "privacy":
        doc["query"]["geoprivacy"] = "obscured"
    elif change == "creation":
        doc["query"]["created_d2"] = "2025-07-01"
    elif change == "date":
        doc["query"]["d2"] = "NaT"
    else:
        boundary.write_text('{"changed":true}')
    with pytest.raises(ValueError):
        validate_history(doc, boundary, "2025-07-01")


def test_fitting_balances_species_cells_instead_of_record_volume():
    frame = pd.DataFrame({"row": [1, 2, 3, 4], "col": [1] * 4, "taxon_id": [1] * 4})
    cells = {(r, 1): features(r) for r in range(1, 5)}
    season = {1: np.ones(12)}
    fitted = fit_profiles(frame, cells, season)
    duplicate = pd.concat([frame, frame.iloc[[0]]] * 4, ignore_index=True)
    assert fit_profiles(duplicate, cells, season) == fitted
    assert fitted["species"][0]["support_cells"] == 4
    assert np.all(np.asarray(fitted["species"][0]["variance"]) >= 0.5)
    assert fit_profiles(frame.iloc[:2], cells, season)["species"] == []
    assert fit_profiles(frame, cells, {})["species"] == []


def test_combined_order_prioritizes_plausibility_then_rarity_and_can_abstain():
    fitted = model()
    # A common but unsupported niche must not fill a slot; rarity cannot rescue it.
    fitted["species"][0]["mean"] = [100] * 14
    fitted["species"][1]["months"][6] = 0.49
    fitted["species"][2]["support_cells"] = 2
    pool = [{"taxon_id": i, "count": i * 3} for i in range(1, 7)]
    orders = rank_pool(pool, fitted, features(), 7, {i: np.ones(12) for i in range(1, 7)})
    assert [r["taxon_id"] for r in orders["habitat_season"]] == [4, 5, 6]
    assert [r["taxon_id"] for r in orders["rarest"]] == [1, 2, 3, 4]
    assert [r["taxon_id"] for r in orders["frequency"]] == [6, 5, 4, 3]
    assert score_species(fitted, [np.nan] * 14, 7) == {}
    with pytest.raises(ValueError):
        score_species(fitted, features(), 13)


def test_holdout_excludes_block_plus_buffer_without_using_outcomes(grid):
    origin = observations((1,), longitude=-123, latitude=49)
    rr, cc = grid.locate(origin.lat.values, origin.lon.values)
    block = block_key(grid, (int(rr[0]), int(cc[0])))
    xmin, ymin = block[0] * 100000, block[1] * 100000
    x = np.array([xmin - 40001, xmin - 40000, xmin + 50000, xmin + 140001])
    y = np.full(4, ymin + 50000)
    lon, lat = grid.inverse.transform(x, y)
    frame = pd.DataFrame({"id": range(4), "lon": lon, "lat": lat})
    assert outside_buffer(frame, grid, block).id.tolist() == [0, 3]


def test_bc_clipping_and_future_rows_cannot_change_pre_origin_eligibility(grid):
    boundary = {
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-124, 48], [-122, 48], [-122, 50], [-124, 50], [-124, 48]]],
        }
    }
    past = observations((1, 1, 1), longitude=-123, latitude=49)
    future = observations((2,), ["2025-07-01"], longitude=-123, latitude=49)
    future["id"] = 999
    future["taxon_name"] = "Futureus species"
    first = bc_frame(past, grid, boundary)
    combined = bc_frame(pd.concat([past, future], ignore_index=True), grid, boundary)
    assert training_state(first)[1:] == training_state(combined.loc[combined.date < "2025-07-01"])[1:]
    future["lon"] = -120
    assert len(bc_frame(pd.concat([past, future], ignore_index=True), grid, boundary)) == len(first)


def comparison_fixture(value=0.1):
    return {
        baseline: {
            metric: {"blocks": 10, "positive_outcome_blocks": 10, "bootstrap_95_percentile": [value, 0.2]}
            for metric in ("total", "q1")
        }
        for baseline in ("rarest", "frequency", "seasonal")
    }


def test_release_needs_provenance_and_rare_evidence_against_every_baseline():
    comparison = comparison_fixture()
    assert release_gate(comparison, True)["passed"]
    assert not release_gate(comparison, False)["passed"]
    comparison["frequency"]["q1"]["positive_outcome_blocks"] = 9
    assert not release_gate(comparison, True)["passed"]
    assert not release_gate(comparison_fixture(0), True)["passed"]
    assert not release_gate({}, True)["passed"]


@pytest.mark.parametrize("interval", [[float("nan"), 1], [1, float("inf")], [0.2, 0.1], ["0.1", 1]])
def test_release_rejects_invalid_intervals(interval):
    comparison = comparison_fixture()
    comparison["rarest"]["total"]["bootstrap_95_percentile"] = interval
    assert not release_gate(comparison, True)["passed"]


@pytest.mark.parametrize("blocks,positive", [(float("nan"), float("nan")), (True, 10), (10, 11), (10, 10.1)])
def test_release_rejects_invalid_block_counts(blocks, positive):
    comparison = comparison_fixture()
    comparison["rarest"]["total"].update(blocks=blocks, positive_outcome_blocks=positive)
    assert not release_gate(comparison, True)["passed"]


def test_disabled_public_artifact_carries_no_model_or_cell_payload(grid, tmp_path):
    gate = release_gate({}, False)
    artifact = make_artifact(model(), {(10, 20): features()}, {}, grid, gate, "2025-07-01", "2024-12-31")
    assert artifact["cells"] == {} and artifact["species"] == [] and artifact["scaler"] is None
    assert artifact["valid_until"] == "2026-07-01"
    path = tmp_path / "artifact.json"
    write_new(path, artifact)
    with pytest.raises(FileExistsError):
        write_new(path, artifact)


def test_repeated_months_are_resampled_together_and_zero_rare_outcomes_are_visible():
    rows = []
    for month in (7, 8, 9):
        for block in (0, 1):
            rows.append(
                {
                    "x": block * 100000 + 50000,
                    "y": 50000,
                    "future_records": 5,
                    "pool": {"total": 1, "q1": 0},
                    "arms": {"habitat_season": {"total": 0.2, "q1": 0}, "rarest": {"total": 0.1, "q1": 0}},
                }
            )
    result = paired_diagnostic(rows, "rarest", "total")
    assert result["blocks"] == 2 and result["cell_months"] == 6
    assert result["mean_difference"] == pytest.approx(0.1)
    assert paired_diagnostic(rows, "rarest", "q1")["positive_outcome_blocks"] == 0


def test_future_mutation_does_not_change_rankings_or_target_population(grid):
    past = observations((1, 1, 1), longitude=-123, latitude=49)
    past["row"], past["col"] = grid.locate(past.lat.values, past.lon.values)
    future = deepcopy(past.iloc[:1])
    future["id"] = 999
    future["date"] = pd.Timestamp("2025-07-01")
    before = evaluate(past, grid, {}, {}, set(grid.axes), origins=("2025-07-01",))
    after = evaluate(
        pd.concat([past, future], ignore_index=True), grid, {}, {}, set(grid.axes), origins=("2025-07-01",)
    )
    assert before["ranking_audit_sha256"] == after["ranking_audit_sha256"]
    earlier = before["training_support"]["2025-07-01"]
    later = after["training_support"]["2025-07-01"]
    assert {k: v for k, v in earlier.items() if k != "outcome_records"} == {
        k: v for k, v in later.items() if k != "outcome_records"
    }


def test_evaluation_uses_same_coverage_as_deployment(grid):
    past = observations((1, 1, 1), longitude=-123, latitude=49)
    past["row"], past["col"] = grid.locate(past.lat.values, past.lon.values)
    result = evaluate(past, grid, {}, {}, set(), origins=("2025-07-01",))
    assert result["cohorts"]["all_targets"]["cell_months"] == 0
    assert result["training_support"]["2025-07-01"]["training_records"] == 3


def test_nonempty_model_predictions_ignore_future_records_and_metadata(grid):
    pieces = []
    for lon, lat in ((-123, 49), (-126, 52), (-129, 54), (-121, 55)):
        rr, cc = grid.locate(np.array([lat]), np.array([lon]))
        assert rr[0] >= 0
        clat, clon = (float(v[0]) for v in grid.centres(rr, cc))
        nlon, nlat, _ = GEOD.fwd(clon, clat, 90, 30000)
        for taxon, longitude, latitude in ((1, clon, clat), (2, nlon, nlat)):
            part = observations((taxon,) * 3, longitude=longitude, latitude=latitude)
            part["taxon_name"] = "Firstus species" if taxon == 1 else "Otherus species"
            part["id"] += len(pieces) * 10
            pieces.append(part)
    past = pd.concat(pieces, ignore_index=True)
    past["row"], past["col"] = grid.locate(past.lat.values, past.lon.values)
    keys = set(zip(past.row, past.col, strict=True))
    cells = {key: features() for key in keys}
    season = {1: np.ones(12), 2: np.ones(12)}
    before = evaluate(past, grid, cells, season, set(grid.axes), origins=("2025-07-01",))
    assert sum(row["arms"]["habitat_season"]["cards"] for row in before["cell_months"]) > 0
    future = pd.concat([past.iloc[:1]] * 5, ignore_index=True)
    future["id"] = np.arange(1000, 1005)
    future["taxon_id"] = 2
    future["threatened"] = True
    future["obs_count"] = 0
    future["date"] = pd.Timestamp("2025-07-01")
    after = evaluate(
        pd.concat([past, future], ignore_index=True),
        grid,
        cells,
        season,
        set(grid.axes),
        origins=("2025-07-01",),
    )
    assert before["ranking_audit_sha256"] == after["ranking_audit_sha256"]
    assert (
        before["training_support"]["2025-07-01"]["niche_fit_blocks"]
        == after["training_support"]["2025-07-01"]["niche_fit_blocks"]
    )


def test_python_and_browser_scoring_share_the_same_numeric_contract():
    rng = np.random.default_rng(20261015)
    fitted = {
        "scaler": {"mean": rng.normal(size=14).tolist(), "scale": rng.uniform(0.5, 2, 14).tolist()},
        "species": [
            {
                "id": i,
                "mean": rng.normal(size=14).tolist(),
                "variance": rng.uniform(0.5, 2, 14).tolist(),
                "months": rng.uniform(0.1, 2, 12).tolist(),
                "rarity": float(rng.random()),
                "support_cells": 3,
            }
            for i in range(1, 51)
        ],
    }
    raw = rng.normal(size=14).tolist()
    expected = score_species(fitted, raw, 7)
    script = (
        "console.log(JSON.stringify({scores:scorePlantPilot("
        + json.dumps(fitted)
        + ","
        + json.dumps(raw)
        + ",7)}));"
    )
    actual = _run_popup([], script)["scores"]
    assert [r["id"] for r in actual] == sorted(
        expected, key=lambda i: (-expected[i]["tier"], -expected[i]["score"], i)
    )
    for row in actual:
        for key in ("tier", "habitat", "season", "score"):
            assert row[key] == pytest.approx(expected[row["id"]][key], rel=1e-12)
