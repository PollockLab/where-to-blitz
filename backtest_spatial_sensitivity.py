"""Check location dominance of the archived, paired sampling-history association.

This deterministic diagnostic deletes one occupied LAEA block at a time. It
reports every 50/100/200 km partition at zero/half-width row and column offsets,
not a selected partition. The resulting ranges are sensitivity ranges, not
confidence intervals, calibrated probabilities or prospective field validation.

Reconstruct exact outcomes from the original hash-verified observation CSVs.
CSV round trips of derived outcomes can break numerical rank ties. All eight
reference cohorts are verified before the output artifact is replaced.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyproj

import backtest_appscore as bas
import voi_backtest as vb

ROOT = Path(__file__).resolve().parent
WIDTHS_KM = (50, 100, 200)
MIN_CELLS = 10
SCORE_COLUMNS = {"discover_leakfree": "scarcity", "app_shipped": "app_shipped"}
PRIMARY_SOURCES = (
    "backtest_appscore.py",
    "voi_backtest.py",
    "goal_presets.py",
    "grid_lattice.py",
    "grid_schema.py",
)


def cohort_hash(cells):
    keys = sorted((int(r), int(c)) for r, c in zip(cells.gi, cells.gj, strict=True))
    return hashlib.sha256(json.dumps(keys).encode()).hexdigest()


def paired_statistic(score, full, standardized):
    """Re-rank both outcomes on the identical retained cells, preserving ties."""
    a, b = vb.spearman(score, full), vb.spearman(score, standardized)
    if not np.isfinite(a) or not np.isfinite(b):
        return {"unavailable_reason": "constant score or outcome on the retained cohort"}
    return {"full_training_rho": a, "standardized_training_rho": b, "delta_rho": b - a}


def block_deletion_sensitivity(cells, widths_km=WIDTHS_KM, min_cells=MIN_CELLS):
    """Descriptive influence of deleting each occupied block from a paired cohort.

    Coordinates must be integer row/column keys on the declared 25 km viewer
    lattice. Scores retain their original cohort definitions; each correlation
    re-ranks on the retained cells. No observation is resampled or reweighted.
    """
    if not isinstance(min_cells, (int, np.integer)) or isinstance(min_cells, bool) or min_cells < 3:
        raise ValueError("min_cells must be an integer of at least three")
    if cells.attrs.get("grid", {}).get("res_m") != 25000:
        raise ValueError("spatial sensitivity requires declared 25000 m grid row/column keys")
    # Pandas propagates attrs by deep copy on slicing. The primary analysis's
    # aux payload contains the full observation lists, which this diagnostic
    # does not need. Detach numeric inputs without copying unrelated metadata.
    columns = ["gi", "gj"] + list(SCORE_COLUMNS.values()) + ["full_newK", "standardized_newK"]
    positions = [cells.columns.get_loc(column) for column in columns]
    values = np.asarray(cells.to_numpy(copy=False)[:, positions], dtype=float)
    cells = pd.DataFrame(values, columns=columns, index=cells.index)
    coords = cells[["gi", "gj"]].to_numpy(dtype=float)
    if not np.isfinite(coords).all() or (coords < 0).any() or (coords != np.floor(coords)).any():
        raise ValueError("spatial sensitivity requires finite nonnegative integer row/column keys")
    if cells.duplicated(["gi", "gj"]).any():
        raise ValueError("spatial sensitivity requires unique cells")
    columns = list(SCORE_COLUMNS.values()) + ["full_newK", "standardized_newK"]
    if not np.isfinite(cells[columns].to_numpy(dtype=float)).all():
        raise ValueError("paired scores and outcomes must be finite")
    widths_km = tuple(widths_km)
    if (
        not widths_km
        or len(set(widths_km)) != len(widths_km)
        or any(
            not isinstance(w, (int, np.integer)) or isinstance(w, bool) or w < 50 or w % 50 for w in widths_km
        )
    ):
        raise ValueError("block widths must be unique positive multiples of 50 km")
    result = {
        "n_cells": len(cells),
        "cohort_keys_sha256": cohort_hash(cells),
        "minimum_retained_cells": int(min_cells),
        "estimand": "change in within-cohort Spearman correlation after standardizing sampled training history",
        "delta_definition": "standardized_training_rho minus full_training_rho",
        "interpretation": "location-dominance diagnostic, not a confidence interval or dependence-corrected test",
        "baseline": {},
        "partitions": [],
    }
    if len(cells) < min_cells:
        result["unavailable_reason"] = "fewer than minimum paired cells"
        return result
    for key, column in SCORE_COLUMNS.items():
        result["baseline"][key] = paired_statistic(cells[column], cells.full_newK, cells.standardized_newK)
    for width in widths_km:
        span = int(width // 25)
        for row_offset in (0, span // 2):
            for col_offset in (0, span // 2):
                block_rows = (cells.gi.to_numpy(dtype=int) - row_offset) // span
                block_cols = (cells.gj.to_numpy(dtype=int) - col_offset) // span
                keys = sorted(set(zip(block_rows.tolist(), block_cols.tolist(), strict=True)))
                partition = {
                    "width_km": int(width),
                    "width_cells": span,
                    "row_offset_cells": row_offset,
                    "col_offset_cells": col_offset,
                    "n_occupied_blocks": len(keys),
                    "deletions": [],
                    "ranges": {},
                }
                for row, col in keys:
                    keep = (block_rows != row) | (block_cols != col)
                    retained = cells.iloc[np.flatnonzero(keep)]
                    deletion = {
                        "block_row_col": [row, col],
                        "removed_cells": int((~keep).sum()),
                        "retained_cells": int(keep.sum()),
                        "scores": {},
                    }
                    if len(retained) < min_cells:
                        deletion["unavailable_reason"] = "fewer than minimum retained cells"
                    else:
                        for key, column in SCORE_COLUMNS.items():
                            deletion["scores"][key] = paired_statistic(
                                retained[column], retained.full_newK, retained.standardized_newK
                            )
                    partition["deletions"].append(deletion)
                for key in SCORE_COLUMNS:
                    valid = [
                        d["scores"][key]
                        for d in partition["deletions"]
                        if key in d["scores"] and "unavailable_reason" not in d["scores"][key]
                    ]
                    summary = {
                        "n_available_deletions": len(valid),
                        "n_unavailable_deletions": len(keys) - len(valid),
                    }
                    if valid:
                        for statistic in ("full_training_rho", "standardized_training_rho", "delta_rho"):
                            summary[statistic] = [
                                min(d[statistic] for d in valid),
                                max(d[statistic] for d in valid),
                            ]
                    else:
                        summary["unavailable_reason"] = (
                            "no deletion has enough cells and nonconstant scores/outcomes"
                        )
                    partition["ranges"][key] = summary
                result["partitions"].append(partition)
    return result


def _assert_equal(actual, expected, label):
    if actual != expected:
        raise ValueError(f"reference mismatch: {label}")


def _assert_close(actual, expected, label):
    if actual is None or expected is None:
        _assert_equal(actual, expected, label)
    elif not np.isfinite(actual) or not np.isfinite(expected) or abs(actual - expected) > 1e-12:
        raise ValueError(f"reference mismatch: {label}")


def reconstruct_cohort(reference, region, input_dir, data_dir):
    """Require the archived inputs, inventory and paired statistics before analysis."""
    taxon = reference["taxon"]
    label = f"{region}/{taxon}"
    run = reference["run"]
    _assert_equal(run["split"], vb.SPLIT.date().isoformat(), f"{label} split")
    _assert_equal(run["rarefaction_method"], "exact hypergeometric expectation", f"{label} outcome method")
    _assert_equal(run["K"], reference["K"], f"{label} K")
    for name in PRIMARY_SOURCES:
        _assert_equal(bas.sha256(ROOT / name), run["source_sha256"][name], f"{label} source {name}")
    filename = f"inat_{'east_' if region == 'East' else ''}{taxon}.csv"
    input_path = Path(input_dir) / filename
    _assert_equal(bas.sha256(input_path), run["input_sha256"], f"{label} observation input hash")
    observations = pd.read_csv(input_path)
    canonical = observations.sort_values(list(observations.columns)).to_csv(index=False).encode()
    _assert_equal(
        hashlib.sha256(canonical).hexdigest(),
        run["observation_frame_sha256"],
        f"{label} observation frame hash",
    )
    grid = bas.AppGrid(bas.GROUP[taxon], data_dir)
    _assert_equal(grid.metadata, run["grid"], f"{label} grid inventory and source hashes")
    cells = vb.build_cells(observations, grid=grid)
    if cells is None:
        raise ValueError(f"reference mismatch: {label} has no split cells")
    aux = cells.attrs["aux"]
    cells.attrs = {"grid": grid.metadata}
    cells = bas.join_axes(cells, grid.axes)
    _assert_equal(len(cells), reference["n_cells"], f"{label} cell count")
    K = reference["K"]
    cells["full_newK"] = vb.expected_new_at_k(cells, aux, K=K)
    eligible = cells.dropna(subset=["full_newK", "app_env", "app_urgency"]).copy()
    _assert_equal(len(eligible), reference["n_rarefied"], f"{label} outcome cohort count")
    eligible["app_shipped"] = bas.composite(eligible, bas.DEFAULT, "app_discover")
    for key, column in SCORE_COLUMNS.items():
        _assert_close(
            vb.spearman(eligible[column], eligible.full_newK),
            reference["scores"][key]["spearman"],
            f"{label} {key} full-cohort rho",
        )
    paired_reference = reference["training_history_sensitivity"]
    _assert_equal(paired_reference["K"], K, f"{label} paired K")
    M = paired_reference["M"]
    eligible["standardized_newK"] = vb.expected_standardized_new_at_k(eligible, aux, M=M, K=K)
    paired = eligible.dropna(subset=["standardized_newK"]).copy()
    paired.attrs = {"grid": grid.metadata}
    _assert_equal(len(paired), paired_reference["n_cells"], f"{label} paired cell count")
    _assert_equal(cohort_hash(paired), paired_reference["cohort_keys_sha256"], f"{label} paired cohort hash")
    for column, field in (
        ("full_newK", "mean_full_training"),
        ("standardized_newK", "mean_standardized_training"),
    ):
        mean = float(paired[column].mean()) if len(paired) else None
        _assert_close(mean, paired_reference[field], f"{label} {field}")
    if len(paired) >= MIN_CELLS:
        for key, column in SCORE_COLUMNS.items():
            for field, outcome in (
                ("full_training", "full_newK"),
                ("standardized_training", "standardized_newK"),
            ):
                _assert_close(
                    vb.spearman(paired[column], paired[outcome]),
                    paired_reference["paired_scores"][key][field]["spearman"],
                    f"{label} {key} {field} rho",
                )
    frame_columns = ["gi", "gj", "scarcity", "app_shipped", "full_newK", "standardized_newK"]
    frame = paired.sort_values(["gi", "gj"])[frame_columns].to_numpy(dtype=float).tolist()
    provenance = {
        "taxon": taxon,
        "region": region,
        "M": M,
        "K": K,
        "input_file": filename,
        "input_sha256": run["input_sha256"],
        "observation_frame_sha256": run["observation_frame_sha256"],
        "paired_frame_columns": frame_columns,
        "paired_frame_sha256": hashlib.sha256(json.dumps(frame, allow_nan=False).encode()).hexdigest(),
        "grid": grid.metadata,
        "reference_verified": "input/frame/grid/source hashes, cell counts, outcome means and both named full/paired score correlations",
        "limitations": run["limitations"],
    }
    return paired, provenance


def build_report(input_dir, data_dir, bc_reference, east_reference):
    """Preflight every archived cohort before producing any diagnostic report."""
    prepared, references = [], {}
    for region, path, expected in (
        ("BC", bc_reference, set(bas.GROUP)),
        ("East", east_reference, {"Aves", "Insecta", "Mammalia"}),
    ):
        path = Path(path)
        records = json.loads(path.read_text())
        if (
            not isinstance(records, list)
            or len(records) != len(expected)
            or {r["taxon"] for r in records} != expected
        ):
            raise ValueError(f"{region} reference must contain exactly the archived taxon cohorts")
        references[region] = {"file": path.name, "sha256": bas.sha256(path)}
        for reference in records:
            cells, provenance = reconstruct_cohort(reference, region, input_dir, data_dir)
            prepared.append((cells, provenance))
    report = {
        "method": "deterministic leave-one-occupied-spatial-block-out paired correlation sensitivity",
        "block_widths_km": list(WIDTHS_KM),
        "partitions": "each width uses row/column offsets 0 and half-width relative to the declared LAEA viewer lattice",
        "score_policy": "retain original cohort score values, re-rank scores and both outcomes within each retained identical cohort",
        "scope": "sampled observed-date record novelty, not future ecological discovery or equal field hours",
        "range_policy": "report all deletion ranges; no partition selection, confidence intervals or calibrated probabilities",
        "sources": {
            "spatial_evaluation_context": "https://doi.org/10.1111/ecog.02881",
            "spatial_resampling_assumptions": "https://doi.org/10.1214/009053606000000551",
            "sampling_bias_context": "https://doi.org/10.1002/ecog.08559",
            "citation_scope": "motivating limitations, not a claim that these sources validate this descriptive deletion diagnostic",
        },
        "references": references,
        "source_sha256": {name: bas.sha256(ROOT / name) for name in PRIMARY_SOURCES + (Path(__file__).name,)},
        "versions": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "pyproj": pyproj.__version__,
            "PROJ": pyproj.proj_version_str,
        },
        "cohorts": [],
    }
    for cells, provenance in prepared:
        report["cohorts"].append(dict(provenance, sensitivity=block_deletion_sensitivity(cells)))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("cluster_results"))
    parser.add_argument("--data-dir", type=Path, default=Path("cluster_results/ca"))
    parser.add_argument(
        "--bc-reference", type=Path, default=Path("cluster_results/voi_appscore_results.json")
    )
    parser.add_argument(
        "--east-reference", type=Path, default=Path("cluster_results/voi_appscore_east_results.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("cluster_results/appscore_spatial_sensitivity.json")
    )
    args = parser.parse_args(argv)
    report = build_report(args.input_dir, args.data_dir, args.bc_reference, args.east_reference)
    payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
    # A failed preflight or serialization leaves any existing report untouched.
    with tempfile.NamedTemporaryFile(
        mode="w", dir=args.output.parent, prefix=f".{args.output.name}.", delete=False
    ) as f:
        temporary = Path(f.name)
        try:
            f.write(payload)
            f.close()
            os.replace(temporary, args.output)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Verified archived cohorts and wrote {args.output}")


if __name__ == "__main__":
    main()
