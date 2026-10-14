"""Independent rank math, paired deletions and archived-input boundary checks."""

import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer

import backtest_appscore as bas
import backtest_spatial_sensitivity as spatial
import voi_backtest as vb


def sample_cells(n=24):
    cells = pd.DataFrame(
        {
            "gi": np.arange(n) + 80,
            "gj": (np.arange(n) % 3) * 3 + 40,
            "scarcity": np.arange(n) % 7,
            "app_shipped": np.arange(n) % 5,
            "full_newK": (np.arange(n) * 3) % 11,
            "standardized_newK": (np.arange(n) * 5) % 13,
        }
    )
    cells.attrs["grid"] = {"res_m": 25000}
    return cells


def independent_rho(x, y):
    """Textbook average ranks and centred inner product, no production helpers."""

    def ranks(values):
        return [
            1 + sum(other < value for other in values) + (sum(other == value for other in values) - 1) / 2
            for value in values
        ]

    a, b = ranks(list(x)), ranks(list(y))
    a = [v - sum(a) / len(a) for v in a]
    b = [v - sum(b) / len(b) for v in b]
    denominator = math.sqrt(sum(v * v for v in a) * sum(v * v for v in b))
    return sum(v * w for v, w in zip(a, b, strict=True)) / denominator


def test_every_partition_and_deletion_matches_independent_paired_rank_math():
    cells = sample_cells()
    report = spatial.block_deletion_sensitivity(cells)
    assert len(report["partitions"]) == 12
    assert [(p["width_km"], p["row_offset_cells"], p["col_offset_cells"]) for p in report["partitions"]] == [
        (width, ro, co) for width in (50, 100, 200) for ro in (0, width // 50) for co in (0, width // 50)
    ]
    for partition in report["partitions"]:
        width = partition["width_km"] // 25
        ro, co = partition["row_offset_cells"], partition["col_offset_cells"]
        block_keys = [
            ((int(r) - ro) // width, (int(c) - co) // width) for r, c in zip(cells.gi, cells.gj, strict=True)
        ]
        assert [tuple(d["block_row_col"]) for d in partition["deletions"]] == sorted(set(block_keys))
        assert sum(d["removed_cells"] for d in partition["deletions"]) == len(cells)
        for deletion in partition["deletions"]:
            keep = [i for i, block in enumerate(block_keys) if block != tuple(deletion["block_row_col"])]
            retained = cells.iloc[keep]
            assert deletion["retained_cells"] == len(retained)
            assert 0 < deletion["removed_cells"] == len(cells) - len(retained)
            assert len(retained) >= 10
            for key, col in spatial.SCORE_COLUMNS.items():
                a = independent_rho(retained[col], retained.full_newK)
                b = independent_rho(retained[col], retained.standardized_newK)
                assert deletion["scores"][key] == pytest.approx(
                    {
                        "full_training_rho": a,
                        "standardized_training_rho": b,
                        "delta_rho": b - a,
                    }
                )
        for key in spatial.SCORE_COLUMNS:
            values = [d["scores"][key]["delta_rho"] for d in partition["deletions"]]
            assert partition["ranges"][key]["delta_rho"] == [min(values), max(values)]


def test_statistics_are_invariant_to_cell_row_order_and_do_not_mutate_input():
    cells = sample_cells()
    original = cells.copy(deep=True)
    expected = spatial.block_deletion_sensitivity(cells)
    actual = spatial.block_deletion_sensitivity(cells.sample(frac=1, random_state=9))
    assert actual["cohort_keys_sha256"] == expected["cohort_keys_sha256"]
    for a, b in zip(actual["partitions"], expected["partitions"], strict=True):
        assert a["width_km"] == b["width_km"]
        for da, db in zip(a["deletions"], b["deletions"], strict=True):
            assert da["block_row_col"] == db["block_row_col"]
            for key in spatial.SCORE_COLUMNS:
                assert da["scores"][key] == pytest.approx(db["scores"][key])
    pd.testing.assert_frame_equal(cells, original)


def test_unused_observation_payload_is_not_copied_for_every_deletion():
    class CopyCounter:
        calls = 0

        def __deepcopy__(self, _memo):
            type(self).calls += 1
            return self

    cells = sample_cells()
    token = CopyCounter()
    cells.attrs["aux"] = token
    report = spatial.block_deletion_sensitivity(cells)
    assert report["partitions"]
    assert CopyCounter.calls == 0
    assert cells.attrs["aux"] is token


def test_tied_scores_and_outcomes_are_reranked_after_deleting_a_block():
    cells = sample_cells()
    cells["scarcity"] = [0] * 12 + [1] * 12
    cells["full_newK"] = [0, 1, 1, 2, 4, 4] * 4
    cells["standardized_newK"] = [2, 2, 0, 0, 1, 1] * 4
    report = spatial.block_deletion_sensitivity(cells, widths_km=(100,))
    for partition in report["partitions"]:
        for deletion in partition["deletions"]:
            if "unavailable_reason" in deletion["scores"]["discover_leakfree"]:
                continue
            span = partition["width_cells"]
            row, col = deletion["block_row_col"]
            keep = ((cells.gi - partition["row_offset_cells"]) // span != row) | (
                (cells.gj - partition["col_offset_cells"]) // span != col
            )
            retained = cells.loc[keep]
            expected = independent_rho(retained.scarcity, retained.full_newK)
            assert deletion["scores"]["discover_leakfree"]["full_training_rho"] == pytest.approx(expected)


@pytest.mark.parametrize("n", [0, 5, 9])
def test_small_or_empty_paired_cohorts_are_unavailable(n):
    result = spatial.block_deletion_sensitivity(sample_cells(n))
    assert result["n_cells"] == n
    assert result["unavailable_reason"] == "fewer than minimum paired cells"
    assert result["partitions"] == []
    assert result["baseline"] == {}


def test_deletions_below_the_cell_threshold_are_retained_as_unavailable():
    report = spatial.block_deletion_sensitivity(sample_cells(10))
    assert "unavailable_reason" not in report
    assert all("unavailable_reason" in d for p in report["partitions"] for d in p["deletions"])
    assert all(r["n_available_deletions"] == 0 for p in report["partitions"] for r in p["ranges"].values())


@pytest.mark.parametrize("column", ["scarcity", "full_newK", "standardized_newK"])
def test_constant_scores_or_outcomes_do_not_get_invented_zero_correlations(column):
    cells = sample_cells()
    cells[column] = 1.0
    report = spatial.block_deletion_sensitivity(cells)
    assert "unavailable_reason" in report["baseline"]["discover_leakfree"]
    for partition in report["partitions"]:
        assert partition["ranges"]["discover_leakfree"]["n_available_deletions"] == 0
        for deletion in partition["deletions"]:
            assert "unavailable_reason" in deletion["scores"]["discover_leakfree"]


def test_zero_origin_coordinates_allow_negative_offset_block_ids():
    cells = sample_cells()
    cells["gi"] -= 80
    cells["gj"] -= 40
    report = spatial.block_deletion_sensitivity(cells)
    assert any(d["block_row_col"][0] == -1 for p in report["partitions"] for d in p["deletions"])


@pytest.mark.parametrize("widths", [(), (25,), (75,), (50, 50), (50.0,), (True,), (0,)])
def test_invalid_or_duplicate_block_widths_fail(widths):
    with pytest.raises(ValueError, match="block widths"):
        spatial.block_deletion_sensitivity(sample_cells(), widths_km=widths)


@pytest.mark.parametrize("minimum", [True, 0, 2, 10.0])
def test_invalid_minimum_sample_sizes_fail(minimum):
    with pytest.raises(ValueError, match="min_cells"):
        spatial.block_deletion_sensitivity(sample_cells(), min_cells=minimum)


@pytest.mark.parametrize(
    "change", ["degree_grid", "duplicate", "fractional", "negative", "nan_outcome", "inf_score"]
)
def test_invalid_lattice_or_paired_values_fail(change):
    cells = sample_cells()
    if change == "degree_grid":
        cells.attrs["grid"] = {"res_m": 0.25}
    elif change == "duplicate":
        cells.loc[1, ["gi", "gj"]] = cells.loc[0, ["gi", "gj"]]
    elif change == "fractional":
        cells["gi"] = cells.gi.astype(float) + 0.5
    elif change == "negative":
        cells.loc[0, "gi"] = -1
    elif change == "nan_outcome":
        cells["full_newK"] = np.nan
    elif change == "inf_score":
        cells["scarcity"] = np.inf
    with pytest.raises(ValueError):
        spatial.block_deletion_sensitivity(cells)


@pytest.fixture
def archived_fixture(tmp_path, monkeypatch):
    index = json.loads((spatial.ROOT / "cluster_results/ca/index.json").read_text())
    inverse = Transformer.from_crs(index["crs"], "EPSG:4326", always_xy=True)
    data_dir = tmp_path / "grid"
    data_dir.mkdir()
    inventory, observations = [], []
    for i in range(12):
        lon, lat = inverse.transform(
            index["lattice"]["x0"] + 40.5 * 25000, index["lattice"]["y1"] - (80.5 + i) * 25000
        )
        inventory.append([round(lat, 3), round(lon, 3), 0.2 + i * 0.05, 0.1, 0.4, 0.3, 0.2, 30, 20])
        for j in range(20 + i):
            observations.append([lat, lon, "2025-06-01", 100 + j % 3, "species"])
        for j in range(6):
            taxon = 200 + j if j < 1 + i % 5 else 100
            observations.append([lat, lon, "2025-08-01", taxon, "species"])
    (data_dir / "index.json").write_text(
        json.dumps(dict(index, n_cells=12, files={"Amphibia": "cells.json"}))
    )
    (data_dir / "cells.json").write_text(json.dumps({"Amphibia": inventory}))
    (data_dir / "us_cells.json").write_text(json.dumps({"us_cells": []}))
    path = tmp_path / "inat_Amphibia.csv"
    pd.DataFrame(observations, columns=["lat", "lon", "observed_on", "taxon_id", "rank"]).to_csv(
        path, index=False
    )
    monkeypatch.setattr(vb, "N_PERM", 5)
    reference, _ = bas.analyse("Amphibia", pd.read_csv(path), data_dir=data_dir, input_path=path)
    return reference, tmp_path, data_dir


def test_exact_reconstruction_matches_the_archived_cohort_and_statistics(archived_fixture):
    reference, input_dir, data_dir = archived_fixture
    cells, provenance = spatial.reconstruct_cohort(reference, "BC", input_dir, data_dir)
    assert len(cells) == reference["training_history_sensitivity"]["n_cells"]
    assert spatial.cohort_hash(cells) == reference["training_history_sensitivity"]["cohort_keys_sha256"]
    assert (
        provenance["input_sha256"]
        == hashlib.sha256((input_dir / "inat_Amphibia.csv").read_bytes()).hexdigest()
    )
    assert cells.attrs["grid"]["res_m"] == 25000
    assert np.isfinite(cells.full_newK).all() and np.isfinite(cells.standardized_newK).all()


def test_reconstruction_detaches_unrelated_metadata_before_derived_cell_operations(
    archived_fixture, monkeypatch
):
    class UnusedPayload:
        def __deepcopy__(self, _memo):
            pytest.fail("unrelated observation payload copied during reconstruction")

    original = vb.build_cells

    def with_unused_payload(*args, **kwargs):
        cells = original(*args, **kwargs)
        cells.attrs["unused"] = UnusedPayload()
        return cells

    monkeypatch.setattr(vb, "build_cells", with_unused_payload)
    reference, input_dir, data_dir = archived_fixture
    cells, _ = spatial.reconstruct_cohort(reference, "BC", input_dir, data_dir)
    assert set(cells.attrs) == {"grid"}


@pytest.mark.parametrize(
    "field", ["input_sha256", "observation_frame_sha256", "source_sha256", "cohort_keys_sha256", "spearman"]
)
def test_hash_or_statistic_drift_fails_preflight(archived_fixture, field):
    reference, input_dir, data_dir = archived_fixture
    if field == "cohort_keys_sha256":
        reference["training_history_sensitivity"][field] = "0" * 64
    elif field == "spearman":
        reference["scores"]["discover_leakfree"][field] += 0.1
    elif field == "source_sha256":
        reference["run"][field]["voi_backtest.py"] = "0" * 64
    else:
        reference["run"][field] = "0" * 64
    with pytest.raises(ValueError, match="reference mismatch"):
        spatial.reconstruct_cohort(reference, "BC", input_dir, data_dir)


def test_changed_inventory_does_not_silently_become_the_archived_run(archived_fixture):
    reference, input_dir, data_dir = archived_fixture
    path = data_dir / "cells.json"
    inventory = json.loads(path.read_text())
    inventory["Amphibia"][0][2] = 0.9
    path.write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match="grid inventory"):
        spatial.reconstruct_cohort(reference, "BC", input_dir, data_dir)


def test_all_eight_cohorts_are_verified_before_any_spatial_analysis(tmp_path, monkeypatch):
    bc, east = tmp_path / "bc.json", tmp_path / "east.json"
    bc.write_text(json.dumps([{"taxon": t} for t in bas.GROUP]))
    east.write_text(json.dumps([{"taxon": t} for t in ["Aves", "Insecta", "Mammalia"]]))
    verified = []

    def reconstruct(reference, region, *_args):
        verified.append((region, reference["taxon"]))
        if len(verified) == 8:
            raise ValueError("last reference does not match")
        return sample_cells(), {}

    def diagnostic(_cells):
        pytest.fail("analysis started before all reference cohorts were verified")

    monkeypatch.setattr(spatial, "reconstruct_cohort", reconstruct)
    monkeypatch.setattr(spatial, "block_deletion_sensitivity", diagnostic)
    with pytest.raises(ValueError, match="last reference"):
        spatial.build_report(tmp_path, tmp_path, bc, east)
    assert len(verified) == 8


def test_failed_preflight_preserves_existing_artifact(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    output.write_text("existing report\n")

    def failed(*_args):
        raise ValueError("mismatched archived inputs")

    monkeypatch.setattr(spatial, "build_report", failed)
    with pytest.raises(ValueError, match="mismatched"):
        spatial.main(["--output", str(output)])
    assert output.read_text() == "existing report\n"
    assert list(tmp_path.iterdir()) == [output]


def test_nonfinite_json_cannot_replace_a_verified_report(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    output.write_text("existing report\n")
    monkeypatch.setattr(spatial, "build_report", lambda *_args: {"invalid": np.nan})
    with pytest.raises(ValueError):
        spatial.main(["--output", str(output)])
    assert output.read_text() == "existing report\n"


def test_successful_output_is_atomic_and_leaves_no_temporary_files(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    monkeypatch.setattr(spatial, "build_report", lambda *_args: {"verified": True})
    spatial.main(["--output", str(output)])
    assert json.loads(output.read_text()) == {"verified": True}
    assert list(tmp_path.iterdir()) == [output]
