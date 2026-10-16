"""The sparse-cell experiment must keep its reference outside the held-out cells."""

import copy
import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from evaluate_gettingeven import (
    evaluate,
    fit_reference,
    load_gate,
    load_inputs,
    spatial_folds,
    summarize,
    validate_counts,
    winner,
)


def inventory():
    rng = np.random.default_rng(17)
    counts = np.array([rng.multinomial(600, rng.dirichlet(np.ones(7))) for _ in range(40)])
    rowcol = np.array([(r * 4, c * 4) for r in range(8) for c in range(5)])
    return counts.sum(axis=1), counts[:, :6], rowcol


def run(total, counts, rowcol, **kwargs):
    return evaluate(total, counts, rowcol, res_m=25000, folds=3, repeats=1, draws=8, **kwargs)


def test_whole_spatial_blocks_stay_in_the_same_fold():
    rowcol = np.array([[0, 0], [3, 3], [4, 0], [4, 3], [8, 8]])
    folds, blocks = spatial_folds(rowcol, 4, 5)
    assert folds[0] == folds[1]
    assert folds[2] == folds[3]
    assert blocks.tolist() == [[0, 0], [0, 0], [1, 0], [1, 0], [2, 2]]
    assert np.array_equal(folds, spatial_folds(rowcol[::-1], 4, 5)[0][::-1])


def test_held_out_counts_cannot_change_their_reference_standardization():
    total, counts, rowcol = inventory()
    before = run(total, counts, rowcol)
    assignments, _ = spatial_folds(rowcol, 4, 3)
    fold = before["folds"][0]["fold"]
    changed = counts.copy()
    changed[assignments == fold] = np.roll(changed[assignments == fold], 1, axis=1)
    after = run(total, changed, rowcol)
    old_ref = next(f for f in before["folds"] if f["fold"] == fold)
    new_ref = next(f for f in after["folds"] if f["fold"] == fold)
    assert old_ref == new_ref
    # Independent arithmetic checks the fitted population standard deviation.
    shares = counts[assignments != fold] / total[assignments != fold, None]
    assert old_ref["mean"] == pytest.approx(shares.mean(axis=0))
    assert old_ref["sd"] == pytest.approx(np.sqrt(((shares - shares.mean(axis=0)) ** 2).mean(axis=0)))


def test_replay_is_exact_and_never_authorizes_a_production_floor():
    total, counts, rowcol = inventory()
    report = run(total, counts, rowcol)
    assert report == run(total, counts, rowcol)
    assert report["release_gate"]["passed"] is False
    assert report["reference_cells"] == len(total)
    assert report["methods"]["floor_1"]["coverage"] == 1
    assert report["methods"]["floor_5"]["coverage"] == pytest.approx(2 / 3)
    assert report["methods"]["floor_20"]["coverage"] == pytest.approx(1 / 3)
    assert report["methods"]["floor_1"]["cases"] == len(total) * 3
    assert report["by_budget"]["1"]["floor_5"]["coverage"] == 0
    assert report["by_budget"]["20"]["floor_5"]["coverage"] == 1
    for method in report["methods"].values():
        assert 0 <= method["coverage"] <= 1
        assert method["recovery"] is None or 0 <= method["recovery"] <= 1


def test_richness_exclusions_apply_to_reference_and_candidates():
    total, counts, rowcol = inventory()
    allowed = np.zeros(counts.shape, dtype=bool)
    allowed[:, 2] = True
    report = run(total, counts, rowcol, allowed=allowed)
    assert report["methods"]["floor_1"]["recovery"] == 1
    assert report["methods"]["shrinkage"]["recovery"] == 1
    allowed[:] = False
    with pytest.raises(ValueError, match="no eligible held-out"):
        run(total, counts, rowcol, allowed=allowed)


def test_unscoreable_categories_are_not_silently_named():
    shares = np.array([[0.1, 0.2, 0.1, 0.1, 0.1, 0.1]])
    mean, sd = np.full(6, 0.2), np.ones(6)
    assert winner(shares, mean, sd, np.zeros((1, 6), bool)).tolist() == [-1]
    assert winner(shares, mean, sd, np.array([[False, True, False, False, False, False]])).tolist() == [1]


def test_block_summary_keeps_repeats_together_and_handles_abstention():
    rows = [([0, 0], True, True), ([0, 0], True, False), ([1, 0], False, False)]
    result = summarize(rows, np.random.default_rng(0))
    assert result["cases"] == 3 and result["blocks"] == 2
    assert result["coverage"] == pytest.approx(2 / 3)
    assert result["recovery"] == 0.5
    assert result["recovery_interval"] == [0.5, 0.5]
    empty = summarize([([0, 0], False, False)], np.random.default_rng(0))
    assert empty["coverage"] == 0 and empty["recovery"] is None
    assert empty["recovery_interval"] is None


@pytest.mark.parametrize("bad", ["negative", "fraction", "excess", "duplicate", "nan"])
def test_invalid_counts_are_rejected_before_a_result_is_computed(bad):
    total, counts, rowcol = (x.astype(float) for x in inventory())
    if bad == "negative":
        counts[0, 0] = -1
    if bad == "fraction":
        counts[0, 0] = 1.5
    if bad == "excess":
        counts[0, 0] = total[0] + 1
    if bad == "duplicate":
        rowcol[0] = rowcol[1]
    if bad == "nan":
        total[0] = np.nan
    with pytest.raises(ValueError):
        validate_counts(total, counts, rowcol)


def test_inputs_are_not_mutated_and_flat_references_fail_clearly():
    original = inventory()
    before = copy.deepcopy(original)
    run(*original)
    assert all(np.array_equal(a, b) for a, b in zip(original, before))
    with pytest.raises(ValueError, match="variation"):
        fit_reference(np.array([10, 10]), np.ones((2, 6)))


def test_gate_is_bound_to_the_actual_counts_and_mask_bytes(tmp_path):
    from gettingeven_inputs import observation_hash

    total, counts = np.array([10, 20]), np.array([[1, 1, 1, 1, 1, 1], [2, 2, 2, 2, 2, 2]])
    rowcol = np.array([[0, 0], [0, 1]])
    mask = tmp_path / "gettingeven_gate_25000m.png"
    Image.fromarray(np.array([[1, 32]], dtype=np.uint8)).save(mask)
    sidecar = {
        "cats": ["Fishes", "Fungi", "Reptiles & Amphibians", "Invertebrates", "Mammals", "Plants"],
        "tiers": {
            "25000": {
                "inputs": {
                    "observations_sha256": observation_hash((rowcol[:, 0], rowcol[:, 1]), total, counts.T)
                },
                "gate_mask": {"png": mask.name, "sha256": hashlib.sha256(mask.read_bytes()).hexdigest()},
            }
        },
    }
    (tmp_path / "gettingeven_grid.json").write_text(json.dumps(sidecar))
    allowed, evidence = load_gate(tmp_path, 25000, total, counts, rowcol)
    assert allowed.tolist() == [[False, True, True, True, True, True], [True, True, True, True, True, False]]
    assert evidence["mask_sha256"] == hashlib.sha256(mask.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="different observation"):
        load_gate(tmp_path, 25000, total + 1, counts, rowcol)
    Image.fromarray(np.array([[0, 0]], dtype=np.uint8)).save(mask)
    with pytest.raises(ValueError, match="hash mismatch"):
        load_gate(tmp_path, 25000, total, counts, rowcol)


def test_count_provenance_hashes_exactly_the_bytes_consumed_even_if_source_changes(tmp_path, monkeypatch):
    from pathlib import Path

    import rasterio

    from evaluate_gettingeven import GROUPS

    groups = ["All biodiversity"] + [g for names in GROUPS.values() for g in names]
    index = {
        "crs": rasterio.crs.CRS.from_string(
            "+proj=laea +lat_0=45 +lon_0=-100 +datum=WGS84 +units=m"
        ).to_wkt(),
        "row_format": ["lat", "lon", "n_train"],
        "files": {group: group.replace(" ", "_") + ".json" for group in groups},
    }
    (tmp_path / "index.json").write_text(json.dumps(index))
    for group, filename in index["files"].items():
        (tmp_path / filename).write_text(
            json.dumps({group: [[45, -100, 100 if group == "All biodiversity" else 1]]})
        )
    target = tmp_path / index["files"]["All biodiversity"]
    original_read = Path.read_bytes
    consumed = original_read(target)

    def read_then_change(path):
        payload = original_read(path)
        if path == target:
            path.write_text(json.dumps({"All biodiversity": [[45, -100, 200]]}))
        return payload

    monkeypatch.setattr(Path, "read_bytes", read_then_change)
    total, _, _, evidence = load_inputs(tmp_path, 25000)
    assert total.tolist() == [100]
    assert evidence[target.name] == hashlib.sha256(consumed).hexdigest()
    assert evidence[target.name] != hashlib.sha256(original_read(target)).hexdigest()
