"""Spatially held-out stability experiment for Getting Even (#135).

This measures recovery of a full-input reference under simulated count thinning,
not ecological correctness or recording benefit. Reference means and standard
deviations are fitted on training blocks once, then held fixed for test cells.
No result from this experiment changes the production estimator or record floor.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
from PIL import Image
from rasterio.io import MemoryFile

from grid_lattice import Lattice

GROUPS = {
    "Fishes": ["Actinopterygii"],
    "Fungi": ["Fungi"],
    "Reptiles & Amphibians": ["Amphibia", "Reptilia"],
    "Invertebrates": ["Arachnida", "Insecta", "Mollusca"],
    "Mammals": ["Mammalia"],
    "Plants": ["Plantae"],
}


def validate_counts(total, counts, rowcol):
    total, counts, rowcol = np.asarray(total), np.asarray(counts), np.asarray(rowcol)
    if total.ndim != 1 or counts.shape != (len(total), 6) or rowcol.shape != (len(total), 2):
        raise ValueError("expected total[n], counts[n,6] and rowcol[n,2]")
    for value in (total, counts, rowcol):
        if not np.isfinite(value).all() or (value < 0).any() or (value != np.floor(value)).any():
            raise ValueError("counts and lattice indices must be finite nonnegative integers")
    if (counts.sum(axis=1) > total).any():
        raise ValueError("disjoint category counts exceed the all-taxa total")
    if len(np.unique(rowcol, axis=0)) != len(total):
        raise ValueError("duplicate lattice cells")
    return total.astype(np.int64), counts.astype(np.int64), rowcol.astype(np.int64)


def spatial_folds(rowcol, block_cells, folds):
    if block_cells < 1 or folds < 2:
        raise ValueError("block size must be positive and at least two folds are required")
    blocks = np.asarray(rowcol, dtype=np.int64) // block_cells
    assigned = np.array(
        [int.from_bytes(hashlib.sha256(f"{r},{c}".encode()).digest()[:8], "big") % folds for r, c in blocks]
    )
    return assigned, blocks


def fit_reference(total, counts):
    """Fit category-share standardization using only the supplied training cells."""
    use = total > 0
    if use.sum() < 2:
        raise ValueError("at least two positive training cells are required")
    shares = counts[use] / total[use, None]
    mean, sd = shares.mean(axis=0), shares.std(axis=0)
    if not (sd > 0).all():
        raise ValueError("each category needs variation in the training reference")
    return mean, sd


def winner(shares, mean, sd, allowed):
    z = np.where(allowed, (shares - mean) / sd, np.inf)
    answer = np.argmin(z, axis=-1)
    return np.where(allowed.any(axis=-1), answer, -1)


def posterior_predictions(samples, budget, mean, sd, allowed, rng, strength, draws):
    """Compare empirical shares with a training-prior estimate and its agreement score.

    Dirichlet draws describe model agreement, not calibrated confidence that a
    category is ecologically correct. The seventh category is all excluded taxa.
    """
    prior = np.r_[mean, max(0.0, 1.0 - mean.sum())]
    prior = (prior + 0.5 / strength) / (1.0 + 3.5 / strength)
    empirical = winner(samples[:, :6] / budget, mean, sd, allowed)
    alpha = samples + strength * prior
    smoothed = winner(alpha[:, :6] / alpha.sum(axis=1, keepdims=True), mean, sd, allowed)
    gamma = rng.gamma(alpha[:, None, :], 1.0, size=(len(samples), draws, 7))
    posterior = gamma / gamma.sum(axis=2, keepdims=True)
    votes = winner(posterior[:, :, :6], mean, sd, allowed[:, None, :])
    agreement = (votes == smoothed[:, None]).mean(axis=1)
    return empirical, smoothed, agreement


def summarize(rows, rng, bootstrap=200):
    """Marginal intervals bootstrap whole spatial blocks, keeping repeats together."""
    grouped = {}
    for block, accepted, correct in rows:
        tally = grouped.setdefault(tuple(block), [0, 0, 0])
        tally[0] += 1
        tally[1] += int(accepted)
        tally[2] += int(accepted and correct)
    tallies = np.asarray(list(grouped.values()), dtype=np.int64)
    if not len(tallies):
        return {
            "cases": 0,
            "blocks": 0,
            "coverage": None,
            "recovery": None,
            "coverage_interval": None,
            "recovery_interval": None,
        }
    n, accepted, correct = tallies.sum(axis=0)
    estimates = []
    for _ in range(bootstrap):
        nn, aa, cc = tallies[rng.integers(len(tallies), size=len(tallies))].sum(axis=0)
        estimates.append((aa / nn, cc / aa if aa else np.nan))
    estimates = np.asarray(estimates)

    def interval(column):
        finite = estimates[:, column][np.isfinite(estimates[:, column])]
        return np.quantile(finite, [0.025, 0.975]).tolist() if len(finite) else None

    return {
        "cases": int(n),
        "blocks": len(tallies),
        "accepted": int(accepted),
        "coverage": float(accepted / n),
        "recovery": float(correct / accepted) if accepted else None,
        "coverage_interval": interval(0),
        "recovery_interval": interval(1),
    }


def evaluate(
    total,
    counts,
    rowcol,
    *,
    res_m,
    allowed=None,
    min_reference=300,
    budgets=(1, 5, 20),
    repeats=5,
    folds=5,
    block_km=100,
    seed=0,
    strength=10.0,
    draws=64,
):
    total, counts, rowcol = validate_counts(total, counts, rowcol)
    if res_m not in (5000, 25000) or block_km * 1000 % res_m:
        raise ValueError("use a 5/25 km tier and an integer number of cells per spatial block")
    if (
        not budgets
        or any(type(n) is not int or n < 1 for n in budgets)
        or len(set(budgets)) != len(budgets)
        or min_reference < max(budgets)
        or repeats < 1
        or draws < 2
        or not np.isfinite(strength)
        or strength <= 0
    ):
        raise ValueError("invalid thinning/reference/posterior settings")
    allowed = np.ones(counts.shape, dtype=bool) if allowed is None else np.asarray(allowed)
    if allowed.shape != counts.shape or allowed.dtype != bool:
        raise ValueError("allowed categories must be a boolean array matching counts")
    assignments, blocks = spatial_folds(rowcol, int(block_km * 1000 // res_m), folds)
    methods = {f"floor_{floor}": [] for floor in (1, 5, 20)}
    methods.update({"shrinkage": [], "agreement_0.5": [], "agreement_0.7": [], "agreement_0.9": []})
    by_budget = {str(budget): {name: [] for name in methods} for budget in budgets}

    def record(name, budget, block, accepted, correct):
        value = (block, accepted, correct)
        methods[name].append(value)
        by_budget[str(budget)][name].append(value)

    fold_details = []
    for fold in range(folds):
        train, test = assignments != fold, (assignments == fold) & (total >= min_reference)
        if not test.any():
            continue
        mean, sd = fit_reference(total[train], counts[train])
        ids = np.flatnonzero(test & allowed.any(axis=1))
        if not len(ids):
            continue
        reference = winner(counts[ids] / total[ids, None], mean, sd, allowed[ids])
        fold_details.append(
            {
                "fold": fold,
                "training_cells": int((train & (total > 0)).sum()),
                "test_cells": len(ids),
                "mean": mean.tolist(),
                "sd": sd.tolist(),
            }
        )
        complete = np.column_stack((counts[ids], total[ids] - counts[ids].sum(axis=1)))
        # Each fold has an independent stream, so adding an empty fold cannot shift other draws.
        rng = np.random.default_rng(np.random.SeedSequence([seed, fold]))
        for budget in budgets:
            for _ in range(repeats):
                samples = np.array([rng.multivariate_hypergeometric(cell, budget) for cell in complete])
                empirical, smoothed, agreement = posterior_predictions(
                    samples, budget, mean, sd, allowed[ids], rng, strength, draws
                )
                for j, cell_id in enumerate(ids):
                    block = blocks[cell_id].tolist()
                    for floor in (1, 5, 20):
                        record(f"floor_{floor}", budget, block, budget >= floor, empirical[j] == reference[j])
                    record("shrinkage", budget, block, True, smoothed[j] == reference[j])
                    for threshold in (0.5, 0.7, 0.9):
                        record(
                            f"agreement_{threshold}",
                            budget,
                            block,
                            agreement[j] >= threshold,
                            smoothed[j] == reference[j],
                        )
    if not fold_details:
        raise ValueError("no eligible held-out reference cells")
    summary_rng = np.random.default_rng(np.random.SeedSequence([seed, folds]))
    return {
        "schema_version": 1,
        "endpoint": "full_input_reference_recovery_under_simulated_thinning",
        "res_m": res_m,
        "categories": list(GROUPS),
        "config": {
            "min_reference": min_reference,
            "budgets": list(budgets),
            "repeats": repeats,
            "folds": folds,
            "block_km": block_km,
            "seed": seed,
            "strength": strength,
            "posterior_draws": draws,
            "block_bootstrap_draws": 200,
        },
        "cells": len(total),
        "reference_cells": sum(f["test_cells"] for f in fold_details),
        "folds": fold_details,
        "methods": {name: summarize(rows, summary_rng) for name, rows in methods.items()},
        "by_budget": {
            budget: {name: summarize(rows, summary_rng) for name, rows in entries.items()}
            for budget, entries in by_budget.items()
        },
        "release_gate": {
            "passed": False,
            "reason": "Research diagnostic; no approved coverage tradeoff or field validation",
        },
        "limits": [
            "Reference recovery is neither ecological correctness nor recording benefit",
            "Only originally well-recorded cells supply full-input references",
            "Dirichlet agreement is an uncalibrated model agreement score",
            "Source counts may be rounded density-derived proxies, not individual records",
            "Coverage is conditional on high-count reference cells and the declared thinning budgets, not national map coverage",
        ],
    }


def load_inputs(directory, res_m):
    """Read a frozen app inventory or actual per-group count stacks, recording their hashes."""
    directory = Path(directory)
    index_path = directory / "index.json"
    index_bytes = index_path.read_bytes()
    index = json.loads(index_bytes)
    lattice = Lattice(res_m, rasterio.crs.CRS.from_wkt(index["crs"]))
    used = {"index.json": hashlib.sha256(index_bytes).hexdigest()}
    if res_m == 25000:
        columns, coords = {}, None
        offset = index["row_format"].index("n_train")
        for group in ["All biodiversity"] + [g for groups in GROUPS.values() for g in groups]:
            name = index["files"][group]
            path = directory / name
            payload = path.read_bytes()
            rows = json.loads(payload)[group]
            here = [(r[0], r[1]) for r in rows]
            if coords is not None and here != coords:
                raise ValueError(f"{group}: coordinates differ from the all-taxa inventory")
            coords = here
            columns[group] = np.array([r[offset] for r in rows])
            used[name] = hashlib.sha256(payload).hexdigest()
        x, y = rasterio.warp.transform(
            "EPSG:4326", lattice.crs, [c[1] for c in coords], [c[0] for c in coords]
        )
        rowcol = np.column_stack(rasterio.transform.rowcol(lattice.transform, x, y))
    else:
        columns, rowcol = {}, None
        for group in ["All biodiversity"] + [g for groups in GROUPS.values() for g in groups]:
            name = f"grid_{res_m}m/{group.replace(' ', '_')}.tif"
            path = directory / name
            payload = path.read_bytes()
            with MemoryFile(payload) as memory, memory.open() as source:
                if (source.crs, source.shape, source.transform) != (
                    lattice.crs,
                    lattice.shape,
                    lattice.transform,
                ):
                    raise ValueError(f"{name}: stack does not match the declared lattice")
                values = source.read(source.descriptions.index("n_train") + 1)
            if rowcol is None:
                rowcol = np.column_stack(np.where(np.isfinite(values)))
            column = values[rowcol[:, 0], rowcol[:, 1]]
            # Missing group support is not evidence for a zero share.
            if not np.isfinite(column).all():
                raise ValueError(f"{name}: missing category count inside the all-taxa support")
            columns[group] = column
            used[name] = hashlib.sha256(payload).hexdigest()
    total = columns["All biodiversity"]
    counts = np.column_stack([sum(columns[g] for g in groups) for groups in GROUPS.values()])
    return total, counts, rowcol, used


def load_gate(directory, res_m, total, counts, rowcol):
    """Use frozen richness exclusions only when they match the evaluated counts."""
    from gettingeven_inputs import observation_hash

    directory = Path(directory)
    sidecar_path = directory / "gettingeven_grid.json"
    sidecar_bytes = sidecar_path.read_bytes()
    sidecar = json.loads(sidecar_bytes)
    if sidecar.get("cats") != list(GROUPS):
        raise ValueError("richness gate category order differs from the evaluator")
    tier = sidecar["tiers"][str(res_m)]
    if tier.get("inputs", {}).get("observations_sha256") != observation_hash(
        (rowcol[:, 0], rowcol[:, 1]), total, counts.T
    ):
        raise ValueError("richness gate belongs to different observation inputs")
    mask = tier.get("gate_mask", {})
    name = mask.get("png")
    if name != f"gettingeven_gate_{res_m}m.png":
        raise ValueError("missing frozen richness gate mask; rebuild it or explicitly use --ungated")
    path = directory / name
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != mask.get("sha256"):
        raise ValueError("richness gate mask hash mismatch")
    with Image.open(io.BytesIO(payload)) as image:
        pixels = np.asarray(image.convert("RGBA"))
    values = pixels[rowcol[:, 0], rowcol[:, 1], 0]
    if (values > 63).any():
        raise ValueError("invalid category bitmask")
    return (values[:, None] & (1 << np.arange(6))) == 0, {
        "sidecar_sha256": hashlib.sha256(sidecar_bytes).hexdigest(),
        "mask": name,
        "mask_sha256": digest,
        "reference": "frozen published richness exclusions",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("cluster_results/ca"))
    parser.add_argument("--res", type=int, choices=(5000, 25000), default=25000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--draws", type=int, default=64)
    parser.add_argument("--ungated", action="store_true", help="Evaluate without richness exclusions")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; use a new path to preserve prior evidence")
    evaluator_bytes = Path(__file__).read_bytes()
    total, counts, rowcol, used = load_inputs(args.data_dir, args.res)
    allowed, gate = (
        (None, "explicitly ungated research comparison")
        if args.ungated
        else load_gate(args.data_dir, args.res, total, counts, rowcol)
    )
    result = evaluate(
        total,
        counts,
        rowcol,
        res_m=args.res,
        seed=args.seed,
        repeats=args.repeats,
        draws=args.draws,
        allowed=allowed,
    )
    result["inputs"] = used
    result["richness_gate"] = gate
    if Path(__file__).read_bytes() != evaluator_bytes:
        raise ValueError("evaluator source changed during the experiment; use a stable source checkout")
    result["evaluator_sha256"] = hashlib.sha256(evaluator_bytes).hexdigest()
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"reference_cells": result["reference_cells"], "methods": result["methods"]}, indent=2))


if __name__ == "__main__":
    main()
