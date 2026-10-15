"""Run the fixed BC plant pilot against same-pool baselines, preserving earlier outputs.

The available 2025 campaign is exploratory. It cannot reconstruct historical API
availability or establish improved field success. A failed gate emits a small
disabled public artifact, rather than promoting the best-looking result.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_appscore import AppGrid
from plant_pilot import (
    ARMS,
    FEATURES,
    METHOD,
    bc_centres,
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
from research.popup_ranking_eval import candidate_pool, cell_bbox, expected_yield, within_radius

ORIGINS = ("2025-07-01", "2025-08-01", "2025-09-01")


def validate_history(document, boundary_path, first_origin):
    """Reject changed region/privacy filters, late history or an unrelated boundary."""
    query = document.get("query", {})
    expected = {
        "place_id": 7085,
        "iconic_taxa": "Plantae",
        "quality_grade": "research",
        "geoprivacy": "open",
        "taxon_geoprivacy": "open",
        "threatened": "false",
    }
    if any(query.get(key) != value for key, value in expected.items()):
        raise ValueError("season region or public research-grade filters differ")
    for key in ("d2", "created_d2"):
        value = query.get(key)
        date = pd.Timestamp(value) if isinstance(value, str) else pd.NaT
        if pd.isna(date) or date.strftime("%Y-%m-%d") != value or date >= pd.Timestamp(first_origin):
            raise ValueError("season observation and creation cutoffs must precede forecast")
    evidence = document.get("manifest", {}).get("boundary", {})
    if evidence.get("output_sha256") != digest(boundary_path):
        raise ValueError("BC boundary does not match seasonal input provenance")
    return query["d2"]


def paired_diagnostic(rows, baseline, metric, size=100000, n_boot=1000):
    """Keep repeated cell/month outcomes together in a physical spatial block."""
    blocks = {}
    for row in rows:
        if row["future_records"] < 5:
            continue
        key = (int(np.floor(row["x"] / size)), int(np.floor(row["y"] / size)))
        blocks.setdefault(key, []).append(row)
    if not blocks:
        return {
            "blocks": 0,
            "cell_months": 0,
            "positive_outcome_blocks": 0,
            "mean_difference": None,
            "bootstrap_95_percentile": None,
        }
    groups = list(blocks.values())
    differences = np.array(
        [
            sum(row["arms"]["habitat_season"][metric] - row["arms"][baseline][metric] for row in group)
            for group in groups
        ]
    )
    counts = np.array([len(group) for group in groups])
    indexes = np.random.default_rng(20261016).integers(0, len(groups), size=(n_boot, len(groups)))
    draws = differences[indexes].sum(1) / counts[indexes].sum(1)
    return {
        "blocks": len(groups),
        "cell_months": int(counts.sum()),
        "positive_outcome_blocks": sum(any(row["pool"][metric] > 0 for row in group) for group in groups),
        "mean_difference": float(differences.sum() / counts.sum()),
        "bootstrap_95_percentile": np.quantile(draws, [0.025, 0.975]).tolist(),
    }


def training_state(train):
    """No outcome-derived eligibility, incidence weights or target selection."""
    available = train.loc[(train.row >= 0) & (train.col >= 0)]
    local = {tuple(map(int, key)): set(group.taxon_id) for key, group in available.groupby(["row", "col"])}
    incidence = available.drop_duplicates(["row", "col", "taxon_id"]).groupby("taxon_id").size()
    cutoff = float(incidence.quantile(0.25)) if len(incidence) else 0
    rare = set(incidence.loc[incidence <= cutoff].index)
    safe = train.threatened.map(lambda value: str(value).lower() == "false")
    metadata = train.assign(safe=safe, global_count=pd.to_numeric(train.obs_count, errors="coerce"))
    grouped = metadata.groupby("taxon_id")
    allowed = set(grouped.safe.all().loc[lambda value: value].index)
    allowed &= set(grouped.global_count.min().loc[lambda value: value > 40].index)
    return local, allowed, rare, cutoff


def outcomes(selected, future, rare):
    ids = [row["taxon_id"] for row in selected]
    return {
        "cards": len(ids),
        "total": expected_yield(ids, future),
        "q1": expected_yield([taxon for taxon in ids if taxon in rare], future),
    }


def evaluate(frame, grid, cells, season, coverage, origins=ORIGINS):
    rows, audit, support = [], [], {}
    for origin in origins:
        as_of = pd.Timestamp(origin)
        train = frame.loc[frame.date < as_of]
        end = as_of + pd.offsets.MonthBegin(1)
        future = frame.loc[(frame.date >= as_of) & (frame.date < end)]
        local, allowed, rare, cutoff = training_state(train)
        models, model_support = {}, []
        targets = sorted(local.keys() & coverage)
        for key in targets:
            block = block_key(grid, key)
            if block not in models:
                outside = outside_buffer(train, grid, block)
                models[block] = fit_profiles(outside, cells, season)
                model_support.append(
                    {
                        "block": list(block),
                        "training_records": len(outside),
                        "modelled_species": len(models[block]["species"]),
                    }
                )
            model = models[block]
            lat, lon = (float(a[0]) for a in grid.centres([key[0]], [key[1]]))
            x, y = grid.forward.transform(lon, lat)
            seen = set(cell_bbox(train, grid, key).taxon_id)
            pool = candidate_pool(within_radius(train, lat, lon), seen, allowed)
            features = cells.get(key, np.full(14, np.nan))
            orders = rank_pool(pool, model, features, as_of.month, season)
            scores = score_species(model, features, as_of.month)
            target_future = list(cell_bbox(future, grid, key).taxon_id)
            row = {
                "origin": origin,
                "row": key[0],
                "col": key[1],
                "x": x,
                "y": y,
                "future_records": len(target_future),
                "pool_size": len(pool),
                "rare_pool_size": sum(candidate["taxon_id"] in rare for candidate in pool),
                "supported_rare_pool_size": sum(
                    candidate["taxon_id"] in rare and candidate["taxon_id"] in scores for candidate in pool
                ),
                "habitat_available": bool(np.isfinite(features).all()),
                "pool": outcomes(pool, target_future, rare),
                "arms": {arm: outcomes(selected, target_future, rare) for arm, selected in orders.items()},
            }
            rows.append(row)
            audit.append(
                [
                    origin,
                    list(key),
                    [[arm, [r["taxon_id"] for r in selected]] for arm, selected in orders.items()],
                ]
            )
        support[origin] = {
            "training_records": len(train),
            "outcome_records": len(future),
            "target_cells": len(targets),
            "rare_incidence_cutoff": cutoff,
            "niche_fit_blocks": model_support,
        }
        print(f"{origin}: {len(targets)} target cells, {len(models)} held-out niche fits", flush=True)
    comparison = {
        baseline: {metric: paired_diagnostic(rows, baseline, metric) for metric in ("total", "q1")}
        for baseline in ARMS
        if baseline != "habitat_season"
    }
    cohorts = {}
    for label, selected in (
        ("all_targets", rows),
        ("five_record_outcomes", [r for r in rows if r["future_records"] >= 5]),
    ):
        cohorts[label] = {
            "cell_months": len(selected),
            "unique_cells": len({(r["row"], r["col"]) for r in selected}),
            "missing_habitat_cell_months": sum(not r["habitat_available"] for r in selected),
            "cell_months_with_rare_candidates": sum(r["rare_pool_size"] > 0 for r in selected),
            "cell_months_with_model_supported_rare_candidates": sum(
                r["supported_rare_pool_size"] > 0 for r in selected
            ),
            "arms": {},
        }
        for arm in ARMS:
            cohorts[label]["arms"][arm] = {
                "mean_cards": float(np.mean([r["arms"][arm]["cards"] for r in selected]))
                if selected
                else None,
                "cells_with_cards": sum(r["arms"][arm]["cards"] > 0 for r in selected),
                "expected_documentation_at_5": {
                    metric: float(np.mean([r["arms"][arm][metric] for r in selected]))
                    if selected and label == "five_record_outcomes"
                    else None
                    for metric in ("total", "q1")
                },
            }
    return {
        "cohorts": cohorts,
        "rare_objective_status": (
            "unavailable: no Q1 candidates meet the held-out model support threshold"
            if not any(row["supported_rare_pool_size"] for row in rows)
            else "exploratory; see attainable outcomes and contributing blocks"
        ),
        "comparisons": comparison,
        "training_support": support,
        "spatial_sensitivity": {
            str(size): {
                baseline: {
                    metric: paired_diagnostic(rows, baseline, metric, size * 1000)
                    for metric in ("total", "q1")
                }
                for baseline in comparison
            }
            for size in (50, 200)
        },
        "ranking_audit_sha256": hashlib.sha256(json.dumps(audit, separators=(",", ":")).encode()).hexdigest(),
        "cell_months": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--habitat", type=Path, required=True)
    parser.add_argument("--season", type=Path, required=True)
    parser.add_argument("--boundary", type=Path, required=True)
    parser.add_argument("--grid-dir", type=Path, default=Path("cluster_results/ca"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.artifact.exists():
        parser.error("output or artifact already exists; choose new paths to preserve inspected results")
    grid = AppGrid("All biodiversity", args.grid_dir)
    habitat, season_document, boundary = [
        json.loads(p.read_text()) for p in (args.habitat, args.season, args.boundary)
    ]
    cells, season = habitat_cells(habitat, grid), seasonal_profiles(season_document)
    season_through = validate_history(season_document, args.boundary, ORIGINS[0])
    raw = pd.read_csv(args.observations)
    frame = bc_frame(raw, grid, boundary)
    results = evaluate(frame, grid, cells, season, bc_centres(grid, boundary))
    gate = release_gate(results["comparisons"], provenance_ok=False)
    final = fit_profiles(frame.loc[frame.date < pd.Timestamp(ORIGINS[0])], cells, season)
    artifact = make_artifact(final, cells, boundary, grid, gate, ORIGINS[0], season_through)
    sources = (
        "plant_pilot.py",
        "evaluate_plant_pilot.py",
        "research/popup_ranking_eval.py",
        "backtest_appscore.py",
        "grid_lattice.py",
        "voi_backtest.py",
    )
    result = {
        "schema_version": 1,
        "method": METHOD,
        "status": "exploratory_historical_pilot",
        "release_gate": gate,
        "methods": {
            "origins": list(ORIGINS),
            "targets": "BC-clipped published 25 km cells with dated pre-origin records",
            "pool": "same full pre-origin 40km geodesic candidate pool, >=3 records, zero sampled local bbox records, metadata eligibility; four arms, up to four cards",
            "season": "pre-2025 province-wide public research-grade monthly species recording shares, 30 background-proportional pseudo-records; normalized by all-plant monthly recording shares",
            "habitat": "cell-balanced climate and WorldCover fractions, train-only standardization, per-species diagonal variance with 50% unit-background shrinkage",
            "model_support": "at least three occupied training cells, a historical seasonal profile and >=80% valid samples for every environmental input; unavailable support is reported separately from predictive failure",
            "combined": "exclude habitat similarity<0.2 or seasonal index<0.5; plausibility=habitat*min(season,1); sort floor(plausibility*5), then plausibility*(1+0.25*training-incidence rarity percentile), then taxon ID",
            "seasonal_baseline": "nearby count times seasonal recording index capped at5, neutral1 for missing prior-year species",
            "spatial": "niche fitting excludes each target100km block plus40km rectangular buffer; fixed province-wide historical seasonal prior includes those regions; paired block-resampling keeps repeated cell/month rows together",
            "outcome": "expected distinct suggested taxa among five following-month records, exact hypergeometric expectation; separately all taxa and lower-quartile pre-origin cell-incidence taxa",
            "gate": "positive100km block interval for total andQ1 outcomes against every baseline, >=10 blocks with attainable outcomes for each metric, and verified historical feature availability; current cache cannot satisfy provenance prerequisite",
            "features": FEATURES,
        },
        "limitations": [
            "Previously inspected2025 campaign, not a fresh confirmatory test or prospective field experiment.",
            "Historical observation dates do not establish historical API availability, privacy, identification or research-grade state; current cached taxonomy/metadata are imperfect proxies.",
            "No observer identity, visit duration, unsuccessful searches or detectability. Record budgets are not equal field effort; sampled gaps are not ecological absence.",
            "Province-wide prior-year seasonal summaries are not spatially withheld; niche fitting alone excludes evaluation regions and buffers.",
            "Broad25km climate/land-cover composition does not establish accessible microhabitat, species occurrence or encounter probability.",
            "Combined arm abstains on weak or missing features; coverage and card counts are reported rather than filling with unqualified candidates.",
            "A failed gate intentionally leaves the public model disabled. No state-of-the-art or field-effectiveness claim follows from exploratory superiority.",
        ],
        "raw_records": len(raw),
        "valid_BC_records": len(frame),
        "inputs": {
            label: {"sha256": digest(path), "bytes": path.stat().st_size}
            for label, path in (
                ("observations", args.observations),
                ("habitat", args.habitat),
                ("season", args.season),
                ("boundary", args.boundary),
            )
        },
        "source_sha256": {path: digest(Path(__file__).parent / path) for path in sources},
        "grid": grid.metadata,
        "results": results,
    }
    write_new(args.output, result)
    write_new(args.artifact, artifact)
    print(f"Release gate: {gate['passed']}; {gate['reasons']}", flush=True)


if __name__ == "__main__":
    main()
