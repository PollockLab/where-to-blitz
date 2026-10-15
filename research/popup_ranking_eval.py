"""Exploratory, sampled-record comparison of three fixed popup suggestion orders.

Run from the repository root with --data-dir pointing to the richer immutable
CSV cache. This is an evidence harness, not a live API replay or ecological model.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest_appscore import AppGrid
from voi_backtest import _log_probability_miss

SPLIT = pd.Timestamp("2025-07-01")
END = pd.Timestamp("2025-10-01")
ARMS = ("rarest", "frequency", "replicated_rare")
VERT = ("Amphibia", "Reptilia", "Mammalia", "Aves")
SLICES = {"ON_vertebrates": ("on", VERT), "BC_vertebrates": ("bcall", VERT),
          "BC_plants": ("bcall", ("Plantae",))}
DOMESTIC = {"Felis catus", "Canis familiaris", "Canis lupus familiaris", "Bos taurus",
            "Ovis aries", "Capra hircus", "Equus caballus", "Equus asinus",
            "Sus scrofa domesticus", "Gallus gallus domesticus", "Anas platyrhynchos domesticus",
            "Columba livia domestica", "Meleagris gallopavo domesticus",
            "Oryctolagus cuniculus domesticus", "Mustela furo", "Cavia porcellus"}
GEOD = Geod(ellps="WGS84")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    """Portable repository-relative keys, independent of checkout location."""
    root = Path(__file__).resolve().parents[1]
    paths = ("research/popup_ranking_eval.py", "backtest_appscore.py", "voi_backtest.py", "grid_lattice.py")
    return {path: sha256(root / path) for path in paths}


def prepare(raw, grid):
    """Taxonomy/coordinate cleaning, without inspecting future metadata for eligibility."""
    frame = raw.drop_duplicates("id").copy()
    frame["date"] = pd.to_datetime(frame.observed_on, errors="coerce")
    for column in ("lat", "lon", "taxon_id"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    # A cached infraspecific record can establish its ancestor species locally.
    # Resolve only a named parent present among pre-split species rows, and
    # require that species ID in the cached ancestry. Never infer a parent ID.
    species = frame.loc[(frame.date < SPLIT) & frame["rank"].eq("species")]
    parents = dict(zip(species.taxon_name, species.taxon_id, strict=True))
    normalized = 0
    for position in frame.index[frame["rank"].isin(["subspecies", "variety", "form"])]:
        name = " ".join(str(frame.at[position, "taxon_name"]).split()[:2])
        parent = parents.get(name)
        ancestry = str(frame.at[position, "ancestry"]).split("/")
        if parent is not None and str(int(parent)) in ancestry:
            frame.at[position, "taxon_id"] = parent
            frame.at[position, "taxon_name"] = name
            frame.at[position, "rank"] = "species"
            normalized += 1
    valid = (frame["rank"].eq("species")
             & frame.taxon_name.str.fullmatch(r"[A-Z][a-z]+ [a-z][a-z-]+", na=False)
             & ~frame.taxon_name.isin(DOMESTIC)
             & frame.date.notna() & frame.date.lt(END)
             & frame.lat.between(-90, 90) & frame.lon.between(-180, 180)
             & frame.taxon_id.gt(0) & frame.taxon_id.mod(1).eq(0))
    frame = frame.loc[valid].copy()
    frame["taxon_id"] = frame.taxon_id.astype(int)
    frame["row"], frame["col"] = grid.locate(frame.lat.values, frame.lon.values)
    x, y = grid.forward.transform(frame.lon.values, frame.lat.values)
    frame["fine_row"] = np.floor((grid.lattice.y1 - np.asarray(y)) / 5000).astype(int)
    frame["fine_col"] = np.floor((np.asarray(x) - grid.lattice.x0) / 5000).astype(int)
    frame.attrs["normalized_infraspecific_records"] = normalized
    frame.attrs["unresolved_or_excluded_records"] = len(raw.drop_duplicates("id")) - len(frame)
    return frame


def cell_bbox(frame, grid, key):
    """Viewer's geographic bounding rectangle of the projected square corners."""
    x, y = grid.lattice.centres([key[0]], [key[1]])
    half = grid.lattice.res / 2
    corners = [(x[0] - half, y[0] - half), (x[0] - half, y[0] + half),
               (x[0] + half, y[0] - half), (x[0] + half, y[0] + half)]
    lon, lat = grid.inverse.transform(*zip(*corners, strict=True))
    return frame.loc[frame.lat.between(min(lat), max(lat)) & frame.lon.between(min(lon), max(lon))]


def training_state(frame):
    """Full sampled pre-split local history and training-only eligibility/incidence."""
    train = frame.loc[frame.date < SPLIT].copy()
    published = train.loc[(train.row >= 0) & (train.col >= 0)]
    local = {key: set(group.taxon_id) for key, group in published.groupby(["row", "col"])}
    incidence = published.drop_duplicates(["row", "col", "taxon_id"]).groupby("taxon_id").size()
    cutoffs = np.quantile(incidence.values, [0.25, 0.5, 0.75]) if len(incidence) else np.array([])
    quartiles = {int(taxon): int(np.searchsorted(cutoffs, value, side="left") + 1)
                 for taxon, value in incidence.items()}
    # Metadata is attached only to training-dated rows, but its cached snapshot
    # was acquired later. Unknown/non-false threat status fails closed.
    threat_ok = train.threatened.map(lambda value: str(value).lower() == "false")
    metadata = train.assign(safe=threat_ok,
                            global_count=pd.to_numeric(train.obs_count, errors="coerce"))
    grouped = metadata.groupby("taxon_id")
    allowed = set(grouped.safe.all().loc[lambda value: value].index)
    global_min = grouped.global_count.min()
    allowed &= set(global_min.loc[global_min > 40].index)
    return train, local, allowed, quartiles, cutoffs


def within_radius(train, lat, lon, radius_m=40000):
    """Exact WGS84 geodesic circle, with a conservative bounding prefilter."""
    if not np.isfinite([lat, lon, radius_m]).all() or radius_m < 0:
        raise ValueError("centre and non-negative radius must be finite")
    # 110 km/degree is conservative for latitude; near a pole retain all longitudes.
    dlat = radius_m / 110000
    dlon = min(180, dlat / max(0.000001, np.cos(np.radians(min(89.999, abs(lat) + dlat)))))
    delta = np.abs((train.lon.values - lon + 180) % 360 - 180)
    positions = np.flatnonzero((np.abs(train.lat.values - lat) <= dlat) & (delta <= dlon))
    nearby = train.iloc[positions]
    if not len(nearby):
        return nearby
    _, _, distance = GEOD.inv(np.full(len(nearby), lon), np.full(len(nearby), lat),
                             nearby.lon.values, nearby.lat.values)
    return nearby.loc[np.asarray(distance) <= radius_m + 1e-7]


def candidate_pool(nearby, seen, allowed):
    """One full pool for every arm. One prior local record already prevents a gap."""
    rows = []
    for taxon, group in nearby.groupby("taxon_id", sort=True):
        if len(group) < 3 or taxon in seen or taxon not in allowed:
            continue
        fine_cells = len(group[["fine_row", "fine_col"]].drop_duplicates())
        dates = group.date.dt.normalize().nunique()
        rows.append({"taxon_id": int(taxon), "count": len(group),
                     "replicated": fine_cells >= 2 and dates >= 3})
    return rows


def rankings(pool):
    """Fixed orders, identical eligibility, four cards, deterministic ID ties."""
    return {
        "rarest": sorted(pool, key=lambda r: (r["count"], r["taxon_id"]))[:4],
        "frequency": sorted(pool, key=lambda r: (-r["count"], r["taxon_id"]))[:4],
        "replicated_rare": sorted(pool, key=lambda r: (not r["replicated"], r["count"], r["taxon_id"]))[:4],
    }


def expected_yield(selected, future, k=5):
    """Expected distinct selected taxa in k uniform record draws without replacement."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 0:
        raise ValueError("k must be a non-negative integer")
    counts = Counter(future)
    total = sum(counts.values())
    if total < k:
        return None
    return float(sum(-np.expm1(_log_probability_miss(counts[taxon], total, k))
                     for taxon in set(selected)))


def attainable_diagnostic(pool, future, quartiles):
    """Outcome-informed oracle diagnostic only, never used by the ranking arms."""
    ids = [candidate["taxon_id"] for candidate in pool]
    rare = [taxon for taxon in ids if quartiles.get(taxon) == 1]
    counts = Counter(future)
    oracle = sorted(ids, key=lambda taxon: (-counts[taxon], taxon))[:4]
    rare_oracle = sorted(rare, key=lambda taxon: (-counts[taxon], taxon))[:4]
    return {"candidates": len(ids), "q1_candidates": len(rare),
            "full_pool_total": expected_yield(ids, future),
            "full_pool_q1": expected_yield(rare, future),
            "outcome_informed_oracle_top4_total": expected_yield(oracle, future),
            "outcome_informed_oracle_top4_q1": expected_yield(rare_oracle, future)}


def block_diagnostic(rows, arm, metric, size_km, n_boot=1000):
    """Paired physical-block resampling, diagnostic under observational dependence."""
    groups = {}
    for row in rows:
        if row["future_records"] < 5:
            continue
        key = (int(np.floor(row["x"] / (size_km * 1000))),
               int(np.floor(row["y"] / (size_km * 1000))))
        difference = row[arm][metric] - row["rarest"][metric]
        groups.setdefault(key, []).append(difference)
    if not groups:
        return {"cells": 0, "blocks": 0, "mean_difference": None, "bootstrap_95_percentile": None}
    sums = np.array([sum(values) for values in groups.values()])
    counts = np.array([len(values) for values in groups.values()])
    rng = np.random.default_rng(20261015)
    indexes = rng.integers(0, len(groups), size=(n_boot, len(groups)))
    draws = sums[indexes].sum(1) / counts[indexes].sum(1)
    return {"cells": int(counts.sum()), "blocks": len(groups),
            "mean_difference": float(sums.sum() / counts.sum()),
            "bootstrap_95_percentile": np.quantile(draws, [0.025, 0.975]).tolist(),
            "fraction_blocks_positive": float(np.mean(sums > 0))}


def summarize(rows):
    revisits = [row for row in rows if row["future_records"] >= 5]
    result = {"target_cells": len(rows), "future_5_record_cells": len(revisits), "arms": {}}
    for arm in ARMS:
        result["arms"][arm] = {}
        for label, cohort in (("all_targets", rows), ("future_5_record_revisits", revisits)):
            values = [row[arm] for row in cohort]
            cards = [value["cards"] for value in values]
            count = len(values)
            result["arms"][arm][label] = {
                "cells": count, "cells_with_cards": sum(n > 0 for n in cards),
                "mean_cards": float(np.mean(cards)) if count else None,
                "card_count_distribution": {str(n): cards.count(n) for n in range(5)},
                "share_all_four_count_three": sum(value["all_four_three"] for value in values) / count if count else None,
                "expected_yield_at_5": {metric: float(np.mean([value[metric] for value in values]))
                                        if count and label != "all_targets" else None
                                        for metric in ("total", "q1", "q2", "q3", "q4", "unknown_quartile")},
            }
    metrics = ("full_pool_total", "full_pool_q1", "outcome_informed_oracle_top4_total",
               "outcome_informed_oracle_top4_q1")
    result["attainable_pool_diagnostic_only"] = {
        "all_targets": {"cells": len(rows),
                        "cells_with_q1_candidates": sum(row["pool"]["q1_candidates"] > 0 for row in rows),
                        "mean_candidates": float(np.mean([row["pool"]["candidates"] for row in rows])) if rows else None,
                        "mean_q1_candidates": float(np.mean([row["pool"]["q1_candidates"] for row in rows])) if rows else None},
        "future_5_record_revisits": {
            "cells": len(revisits),
            "cells_with_q1_candidates": sum(row["pool"]["q1_candidates"] > 0 for row in revisits),
            "cells_with_heldout_q1_pool_documentation": sum(row["pool"]["full_pool_q1"] > 0 for row in revisits),
            "expected_yield_at_5": {metric: float(np.mean([row["pool"][metric] for row in revisits]))
                                    if revisits else None for metric in metrics},
        },
    }
    result["paired_spatial_block_diagnostics"] = {
        str(size): {arm: {metric: block_diagnostic(rows, arm, metric, size)
                         for metric in ("q1", "total")}
                    for arm in ARMS if arm != "rarest"}
        for size in (50, 100, 200)}
    return result


def evaluate(raw, grid):
    frame = prepare(raw, grid)
    train, local, allowed, quartiles, cutoffs = training_state(frame)
    test = frame.loc[(frame.date >= SPLIT) & (frame.date < END) & (frame.row >= 0) & (frame.col >= 0)]
    rows, audit = [], []
    for key in sorted(local):
        seen = set(cell_bbox(train, grid, key).taxon_id)
        lat, lon = (float(a[0]) for a in grid.centres([key[0]], [key[1]]))
        x, y = grid.forward.transform(lon, lat)
        pool = candidate_pool(within_radius(train, lat, lon), seen, allowed)
        orders = rankings(pool)
        outcomes = list(cell_bbox(frame.loc[frame.date >= SPLIT], grid, key).taxon_id)
        row = {"x": x, "y": y, "future_records": len(outcomes),
               "pool": attainable_diagnostic(pool, outcomes, quartiles)}
        cell_audit = [list(map(int, key)), len(pool)]
        for arm, selected in orders.items():
            ids = [candidate["taxon_id"] for candidate in selected]
            row[arm] = {"cards": len(ids),
                        "all_four_three": len(ids) == 4 and all(candidate["count"] == 3 for candidate in selected),
                        "total": expected_yield(ids, outcomes)}
            for quartile in range(1, 5):
                row[arm][f"q{quartile}"] = expected_yield(
                    [taxon for taxon in ids if quartiles.get(taxon) == quartile], outcomes)
            row[arm]["unknown_quartile"] = expected_yield(
                [taxon for taxon in ids if taxon not in quartiles], outcomes)
            cell_audit.append([arm, ids, [candidate["count"] for candidate in selected]])
        audit.append(cell_audit)
        rows.append(row)
    result = summarize(rows)
    result["data"] = {"raw_records": len(raw), "valid_unique_species_records": len(frame),
                      **frame.attrs,
                      "training_records": len(train), "future_published_cell_records": len(test),
                      "training_eligible_taxa": len(allowed), "training_incidence_taxa": len(quartiles),
                      "incidence_quartile_cutoffs": cutoffs.tolist(),
                      "taxa_per_incidence_quartile": {str(q): sum(v == q for v in quartiles.values()) for q in range(1, 5)},
                      "min_observed_on": str(frame.date.min().date()) if len(frame) else None,
                      "max_observed_on": str(frame.date.max().date()) if len(frame) else None,
                      "ranking_audit_sha256": hashlib.sha256(json.dumps(audit, separators=(",", ":")).encode()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--grid-dir", type=Path, default=Path("cluster_results/ca"))
    parser.add_argument("--output", type=Path, default=Path("research/results/popup_ranking_eval.json"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; use a new path to preserve earlier exploratory artifacts")
    grid = AppGrid("All biodiversity", args.grid_dir)
    result = {
        "status": "exploratory, earlier 2025 results already inspected; not a confirmatory test",
        "methods": {
            "split": "observed_on < 2025-07-01; outcomes 2025-07-01 through 2025-09-30 inclusive",
            "targets": "published Canada-eligible 25km cells with >=1 valid pre-split species record in this slice",
            "pool": "all training species with >=3 WGS84 geodesic 40km-centre records and zero full sampled training records in geographic bbox of LAEA25km corners; no top-500 cutoff",
            "local_geometry": "viewer geographic bounding rectangles, not projected square membership; outcomes use same bbox and can overlap across neighboring cells",
            "taxonomy": "species rank and Latin binomial; subspecies/variety/form collapsed only when named parent appears among training species rows and parent ID matches ancestry; domestic list excluded; unique observation IDs",
            "metadata": "candidate taxa: all training-row threatened flags explicitly false and minimum cached global obs_count >40; future-row metadata unused",
            "arms_fixed_before_execution": {
                "rarest": "nearby count ascending, taxon ID ascending",
                "frequency": "nearby count descending, taxon ID ascending, reference only",
                "replicated_rare": ">=2 distinct LAEA5km cells AND >=3 distinct dates first, then nearby count ascending and taxon ID ascending; no change to eligible pool"},
            "cards": 4, "outcome_budget": 5,
            "attainable_diagnostic_only": "full shared candidate-pool expected yield plus outcome-informed top4 oracle for total and Q1 taxa; future counts used only in this labeled diagnostic, never arm ranking; all-target candidate coverage remains train-defined",
            "primary": "expected distinct suggested Q1 train-incidence taxa first documented in this cell in five held-out records, preference proxy",
            "rarity": "per-slice incidence across occupied published training 25km cells; lower quartile Q1 is rare preference proxy; ties kept together at empirical25/50/75 percentile cutoffs; all quartiles reported",
            "outcome": "exact hypergeometric expectation, uniform five records without replacement, only cells with >=5 held-out records; all-target coverage separate",
            "spatial": "paired block bootstrap at50/100/200km LAEA blocks,1000 resamples each with seed20261015;100km primary diagnostic; same physical cell shares block; no independence claim",
        },
        "limitations": [
            "Training means observation date only. Later cached identifications, threat flags, global counts and record availability cannot reconstruct historical API state.",
            "Project/province/season samples, not complete local or nearby iNaturalist inventories; sampled absence is not biological absence.",
            "Supported infraspecific parents are collapsed with ancestry plus training species-name lookup; unresolved parents are discarded and raw species_counts aggregation cannot be reconstructed exactly. Counts are a taxonomy proxy.",
            "Local geographic bounding rectangles overlap neighboring LAEA cells. Incidence rarity and target selection use projected membership; local absence and outcomes use viewer bounding rectangles.",
            "Photo availability, geoprivacy, taxon geoprivacy, observer IDs, effort and historical research grade are unavailable; not an exact live-popup replay.",
            "Future records reflect existing observer choices, not visits induced by suggestions or equal field effort. Five-record expectation is a retrospective documentation proxy.",
            "Replicate cells/dates do not establish independent observers, occupancy or findability. No model is trained or calibrated.",
            "Source coverage differs: vertebrates June-Sept2025, plants Apr-Sept2025. Unvisited cells and cells without sampled training records lack outcome validation.",
            "Rarity uses sampled train incidence, not conservation status, true regional rarity or the same nearby-count quantity used for ordering.",
            "Exploratory fixed-arm comparison on previously inspected2025 outcomes, spatial resampling does not supply independent ecological validation or establish global state of the art.",
        ],
        "grid": grid.metadata,
        "source_sha256": source_hashes(),
        "versions": {"python": sys.version.split()[0], "numpy": np.__version__, "pandas": pd.__version__},
        "inputs": {}, "slices": {},
    }
    for name, (prefix, classes) in SLICES.items():
        paths = [args.data_dir / f"inat_{prefix}_{group}.csv" for group in classes]
        for path in paths:
            result["inputs"][path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
        raw = pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)
        print(f"Evaluating {name}: {len(raw)} raw records", flush=True)
        result["slices"][name] = evaluate(raw, grid)
        print(f"  {result['slices'][name]['target_cells']} targets; {result['slices'][name]['future_5_record_cells']} revisits", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(args.output, flush=True)


if __name__ == "__main__":
    main()
