"""Fixed, presence-only BC plant ranking pilot. Scores are indices, not probabilities."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import Point, shape

from research.popup_ranking_eval import prepare

METHOD = "bc-plants-habitat-season-v1"
FEATURES = ["bio1", "bio4", "bio12"] + [f"cover_{v}" for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)]
ARMS = ("rarest", "frequency", "seasonal", "habitat_season")
MIN_CELLS = 3
SEASON_PRIOR = 30
RARITY_BONUS = 0.25


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def habitat_cells(document, grid):
    """Read explicitly keyed raw environmental features, never implicit npy row order."""
    if (
        document.get("schema_version") != 1
        or document.get("row_format") != ["row", "col", "lat", "lon"] + FEATURES
    ):
        raise ValueError("habitat schema or feature order differs")
    if any(document.get("grid", {}).get(k) != grid.metadata[k] for k in ("crs", "lattice", "res_m")):
        raise ValueError("habitat lattice or CRS differs from the app grid")
    if document.get("sampling", {}).get("points_per_cell") != 225:
        raise ValueError("habitat must declare its 225-point sampling support")
    validity = {}
    for row in document.get("validity", []):
        if (
            not isinstance(row, list)
            or len(row) != 6
            or tuple(row[:2]) in validity
            or any(type(v) is not int or not 0 <= v <= 225 for v in row[2:])
        ):
            raise ValueError("invalid habitat sample counts")
        validity[tuple(row[:2])] = row[2:]
    cells = {}
    for row in document.get("cells", []):
        if not isinstance(row, list) or len(row) != 18:
            raise ValueError("malformed habitat row")
        key = tuple(row[:2])
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in key) or key in cells:
            raise ValueError("invalid or duplicate habitat cell")
        lat, lon = (float(v[0]) for v in grid.centres([key[0]], [key[1]]))
        if (
            key not in grid.axes
            or not np.isfinite(row[2:4]).all()
            or not np.allclose(row[2:4], [lat, lon], atol=0.000501, rtol=0)
        ):
            raise ValueError("habitat coordinates do not match the app cell")
        features = np.asarray([np.nan if v is None else v for v in row[4:]], dtype=float)
        if np.isfinite(features[3:]).all() and (
            (features[3:] < 0).any()
            or (features[3:] > 1).any()
            or not np.isclose(features[3:].sum(), 1, atol=1e-5)
        ):
            raise ValueError("invalid land-cover fractions")
        if key not in validity:
            raise ValueError("missing habitat sampling support")
        # Partial cover must not masquerade as a complete habitat description.
        if min(validity[key]) < 180:
            features[:] = np.nan
        cells[key] = features
    if cells.keys() != validity.keys():
        raise ValueError("habitat sample counts name unknown cells")
    return cells


def seasonal_profiles(document):
    """Recording-volume-normalized monthly shares, smoothed toward plant background."""
    if document.get("schema_version") != 1 or document.get("months") != list(range(1, 13)):
        raise ValueError("season schema must contain all twelve months")
    totals = np.asarray(document.get("monthly_species_record_totals"), dtype=float)
    if totals.shape != (12,) or not np.isfinite(totals).all() or (totals <= 0).any():
        raise ValueError("season needs positive monthly background counts")
    background = totals / totals.sum()
    profiles, actual = {}, np.zeros(12)
    for taxon, counts in document.get("species", []):
        counts = np.asarray(counts, dtype=float)
        if (
            isinstance(taxon, bool)
            or not isinstance(taxon, int)
            or taxon <= 0
            or taxon in profiles
            or counts.shape != (12,)
            or not np.isfinite(counts).all()
            or (counts < 0).any()
            or not np.equal(counts, np.floor(counts)).all()
        ):
            raise ValueError("invalid seasonal species counts")
        actual += counts
        profiles[taxon] = (counts + SEASON_PRIOR * background) / (counts.sum() + SEASON_PRIOR) / background
    if not np.array_equal(actual, totals):
        raise ValueError("season species counts do not match monthly totals")
    return profiles


def bc_frame(raw, grid, boundary):
    """Reuse train-only taxonomy normalization and clip actual points to BC."""
    polygon = shape(boundary["geometry"])
    if polygon.is_empty or not polygon.is_valid or polygon.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("invalid BC polygon")
    frame = prepare(raw, grid)
    inside = np.array(
        [polygon.covers(Point(lon, lat)) for lon, lat in zip(frame.lon, frame.lat, strict=True)]
    )
    return frame.loc[inside].copy()


def block_key(grid, key, size=100000):
    x, y = grid.lattice.centres([key[0]], [key[1]])
    return int(np.floor(x[0] / size)), int(np.floor(y[0] / size))


def bc_centres(grid, boundary):
    """One coverage predicate for evaluation targets and deployable model cells."""
    polygon = shape(boundary["geometry"])
    selected = set()
    for key in grid.axes:
        lat, lon = (float(v[0]) for v in grid.centres([key[0]], [key[1]]))
        if polygon.covers(Point(lon, lat)):
            selected.add(key)
    return selected


def outside_buffer(frame, grid, block, size=100000, buffer=40000):
    """Remove an evaluation block and its declared 40 km rectangular buffer."""
    x, y = grid.forward.transform(frame.lon.to_numpy(), frame.lat.to_numpy())
    xmin, ymin = block[0] * size - buffer, block[1] * size - buffer
    xmax, ymax = (block[0] + 1) * size + buffer, (block[1] + 1) * size + buffer
    # WGS84 projection roundtrips move exact boundaries by submillimetres.
    tolerance = 0.001
    return frame.loc[
        (x < xmin - tolerance) | (x > xmax + tolerance) | (y < ymin - tolerance) | (y > ymax + tolerance)
    ]


def fit_profiles(train, cells, season):
    """Cell-balanced environmental niches with fixed variance shrinkage.

    One species-cell pair contributes once, regardless of record volume. Background
    is occupied training cells, so this estimates recording-environment similarity.
    It does not identify occurrence, detectability or causal habitat preference.
    """
    paired = train.loc[(train.row >= 0) & (train.col >= 0)].drop_duplicates(["row", "col", "taxon_id"])
    keys = sorted(set(zip(paired.row, paired.col, strict=True)))
    keys = [key for key in keys if key in cells and np.isfinite(cells[key]).all()]
    if len(keys) < MIN_CELLS:
        return {"scaler": None, "species": []}
    background = np.array([cells[key] for key in keys])
    mean, scale = background.mean(0), background.std(0)
    scale = np.where(scale > 1e-6, scale, 1)
    lookup = {key: (cells[key] - mean) / scale for key in keys}
    incidence = paired.groupby("taxon_id").size()
    rarity = 1 - incidence.rank(method="average", pct=True)
    species = []
    for taxon, group in paired.groupby("taxon_id", sort=True):
        values = [lookup[key] for key in zip(group.row, group.col, strict=True) if key in lookup]
        if len(values) < MIN_CELLS or int(taxon) not in season:
            continue
        values = np.array(values)
        # Fixed 50% shrinkage to unit background variance avoids degenerate niches.
        variance = 0.5 * values.var(0) + 0.5
        species.append(
            {
                "id": int(taxon),
                "mean": values.mean(0).tolist(),
                "variance": variance.tolist(),
                "months": season[int(taxon)].tolist(),
                "rarity": float(rarity.loc[taxon]),
                "support_cells": len(values),
            }
        )
    return {"scaler": {"mean": mean.tolist(), "scale": scale.tolist()}, "species": species}


def score_species(model, features, month):
    """Exact browser contract. Null means insufficient or weak support, never absence."""
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError("month must be 1..12")
    raw = np.asarray(features, dtype=float)
    if raw.shape != (14,) or not np.isfinite(raw).all() or model.get("scaler") is None:
        return {}
    scaler = model["scaler"]
    z = (raw - np.asarray(scaler["mean"])) / np.asarray(scaler["scale"])
    scores = {}
    for species in model["species"]:
        d2 = float(np.mean((z - np.asarray(species["mean"])) ** 2 / np.asarray(species["variance"])))
        habitat = float(np.exp(-0.5 * d2))
        season = species["months"][month - 1]
        if habitat < 0.2 or season < 0.5 or species["support_cells"] < MIN_CELLS:
            continue
        plausibility = habitat * min(season, 1)
        scores[species["id"]] = {
            "habitat": habitat,
            "season": season,
            "tier": int(np.floor(plausibility * 5)),
            "score": plausibility * (1 + RARITY_BONUS * species["rarity"]),
        }
    return scores


def rank_pool(pool, model, features, month, season):
    """Same candidate pool for four fixed arms; combined arm may abstain."""
    scores = score_species(model, features, month)
    supported = [row for row in pool if row["taxon_id"] in scores]
    seasonal = lambda row: row["count"] * min(5, float(season.get(row["taxon_id"], np.ones(12))[month - 1]))
    return {
        "rarest": sorted(pool, key=lambda row: (row["count"], row["taxon_id"]))[:4],
        "frequency": sorted(pool, key=lambda row: (-row["count"], row["taxon_id"]))[:4],
        "seasonal": sorted(pool, key=lambda row: (-seasonal(row), row["taxon_id"]))[:4],
        "habitat_season": sorted(
            supported,
            key=lambda row: (
                -scores[row["taxon_id"]]["tier"],
                -scores[row["taxon_id"]]["score"],
                row["taxon_id"],
            ),
        )[:4],
    }


def release_gate(comparisons, provenance_ok, minimum_blocks=10):
    """Fail closed. Historical exploratory results alone cannot release a ranking."""
    reasons = []
    if not provenance_ok:
        reasons.append("historical_availability_unverified")
    for baseline in ("rarest", "frequency", "seasonal"):
        values = comparisons.get(baseline, {})
        for metric in ("total", "q1"):
            result = values.get(metric, {})
            interval = result.get("bootstrap_95_percentile")
            valid_interval = (
                isinstance(interval, list)
                and len(interval) == 2
                and all(type(v) in (int, float) and np.isfinite(v) for v in interval)
                and 0 < interval[0] <= interval[1]
            )
            blocks, positive = result.get("blocks"), result.get("positive_outcome_blocks")
            valid_blocks = (
                type(blocks) is int and type(positive) is int and minimum_blocks <= positive <= blocks
            )
            if not valid_blocks or not valid_interval:
                reasons.append(f"{baseline}_{metric}_improvement_unestablished")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "interpretation": "blocked exploratory diagnostics are not a prospective field-effectiveness test",
    }


def make_artifact(model, cells, boundary, grid, gate, training_before, season_through):
    """Only release-passing payloads carry model weights into the public page."""
    date = pd.Timestamp(training_before)
    artifact = {
        "schema_version": 1,
        "method": METHOD,
        "release_gate": gate,
        "training_before": str(date.date()),
        "valid_until": str((date + pd.Timedelta(days=365)).date()),
        "season_through": season_through,
        "feature_names": FEATURES,
        "scaler": model["scaler"] if gate["passed"] else None,
        "species": model["species"] if gate["passed"] else [],
        "cells": {},
    }
    if gate["passed"]:
        coverage = bc_centres(grid, boundary)
        for key, values in sorted(cells.items()):
            if key in coverage and np.isfinite(values).all():
                artifact["cells"][f"{key[0]},{key[1]}"] = values.tolist()
    return artifact


def write_new(path, document):
    """Never silently overwrite an inspected result or model artifact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(document, stream, indent=2, allow_nan=False)
        stream.write("\n")
