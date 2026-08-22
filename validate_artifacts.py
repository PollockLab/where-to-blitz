#!/usr/bin/env python3
"""Validate rebuilt site artifacts.

Checks:
 - numeric nesting: grid_25000m stacks are the (k x k) block mean of grid_5000m stacks
 - values PNGs: every group has one cell-colour PNG per (goal, tier) (build_grid_values.py),
   and none of them has piled its cells onto the top of the viridis ramp
 - writes a JSON report (default: validate_report.json) with per-group results
 - exits with non-zero code if any numeric-nesting checks fail or required files missing

Usage:
  python validate_artifacts.py /path/to/extracted/artifact_root \
      --output validate_report.json

The script is pure-Python (depends on rasterio and numpy).
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning

from goal_presets import PRESETS
from grid_lattice import mean_pool_block, sum_pool_block
from grid_schema import SUM_BAND_INDICES as SUM_BANDS  # 1-based; n_train is extensive

GOAL_SLUGS = [p["name"].lower().replace(" ", "_") for p in PRESETS]

# A goal PNG whose visible cells pile onto the very top of the viridis ramp has lost its
# top end: the blend ran past the clip and the map paints one flat colour across cells the
# popups still rank apart. Species discovery did exactly this on 55% of visible cells until
# each preset carried its own scale, and nothing in CI looked at a single pixel. Gate every
# PNG, so the next preset that outgrows its scale fails the build instead of shipping.
RAMP_TOP = (253, 231, 37)  # viridis(1.0), the colour every clipped cell collapses to
SATURATION_MAX_FRAC = 0.05


def top_of_ramp_fraction(png: Path) -> float:
    """Share of a values PNG's visible cells painted the very top viridis colour."""
    with warnings.catch_warnings():
        # the values PNGs are lattice index space, not a georeferenced raster; that is the point
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(png) as src:
            rgba = src.read()  # (4, h, w) uint8: RGB + validity alpha
    visible = rgba[3] == 255
    if not visible.any():
        return 0.0
    top = (rgba[0] == RAMP_TOP[0]) & (rgba[1] == RAMP_TOP[1]) & (rgba[2] == RAMP_TOP[2])
    return float((top & visible).sum() / visible.sum())


def compare_arrays(expected: np.ndarray, actual: np.ndarray, abs_tol: float, rel_tol: float) -> dict[str, Any]:
    """Compare two 2D arrays and return metrics and pass/fail using tolerances."""
    if expected.shape != actual.shape:
        return {"pass": False, "reason": "shape_mismatch", "expected_shape": expected.shape, "actual_shape": actual.shape}
    e_fin = np.isfinite(expected)
    a_fin = np.isfinite(actual)
    both = e_fin & a_fin
    metrics: dict[str, Any] = {"n_cells": int(np.prod(expected.shape)), "n_both_finite": int(both.sum())}
    if both.sum() == 0:
        # nothing to compare: treat as pass (no data overlap)
        metrics.update({"pass": True, "n_mismatch": 0, "max_abs_diff": None, "mean_abs_diff": None})
        return metrics
    diff = np.abs(actual[both] - expected[both])
    max_abs = float(np.nanmax(diff))
    mean_abs = float(np.nanmean(diff))
    median_abs = float(np.nanmedian(diff))
    thresh = abs_tol + rel_tol * np.where(np.abs(expected[both]) > 0, np.abs(expected[both]), 1.0)
    mismatches = (diff > thresh).sum()
    metrics.update({
        "pass": mismatches == 0,
        "n_mismatch": int(mismatches),
        "max_abs_diff": max_abs,
        "mean_abs_diff": mean_abs,
        "median_abs_diff": median_abs,
    })
    return metrics


def validate_group(fine_path: Path, coarse_path: Path, abs_tol: float, rel_tol: float) -> dict[str, Any]:
    rec: dict[str, Any] = {"fine": str(fine_path), "coarse": str(coarse_path)}
    try:
        with rasterio.open(fine_path) as src5, rasterio.open(coarse_path) as src25:
            # quick sanity checks
            if src5.count != src25.count:
                rec.update({"pass": False, "reason": "band_count_mismatch", "fine_bands": src5.count, "coarse_bands": src25.count})
                return rec
            # compute k from shapes
            h5, w5 = src5.height, src5.width
            h25, w25 = src25.height, src25.width
            if h25 == 0 or w25 == 0:
                rec.update({"pass": False, "reason": "coarse_empty"})
                return rec
            if h5 % h25 != 0 or w5 % w25 != 0:
                rec.update({"pass": False, "reason": "shape_not_integer_multiple", "fine_shape": (h5, w5), "coarse_shape": (h25, w25)})
                return rec
            k_h = h5 // h25
            k_w = w5 // w25
            if k_h != k_w:
                rec.update({"pass": False, "reason": "non_square_block", "k_h": k_h, "k_w": k_w})
                return rec
            k = int(k_h)
            rec.update({"k": k, "bands": {} })
            overall_pass = True
            for b in range(1, src5.count + 1):
                a5 = src5.read(b).astype(float)
                a25 = src25.read(b).astype(float)
                try:
                    if b in SUM_BANDS:
                        expected = sum_pool_block(a5, k)
                    else:
                        expected = mean_pool_block(a5, k)
                except (ValueError, RuntimeError) as e:
                    rec["bands"][str(b)] = {"pass": False, "reason": f"pool_error: {e}"}
                    overall_pass = False
                    continue
                # compare only where both finite
                metrics = compare_arrays(expected, a25, abs_tol=abs_tol, rel_tol=rel_tol)
                rec["bands"][str(b)] = metrics
                if not metrics.get("pass", False):
                    overall_pass = False
            rec["pass"] = overall_pass
            return rec
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        rec.update({"pass": False, "reason": f"exception: {exc}"})
        return rec


def find_tif_groups(dir_path: Path) -> dict[str, Path]:
    if not dir_path.exists():
        return {}
    out = {}
    for p in sorted(dir_path.glob("*.tif")):
        out[p.stem] = p
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("root", nargs="?", default=".", help="extracted artifact root containing cluster_results/ca")
    p.add_argument("--output", default="validate_report.json", help="JSON output path")
    p.add_argument("--abs-tol", type=float, default=1e-6, help="absolute tolerance for numeric nesting")
    p.add_argument("--rel-tol", type=float, default=1e-6, help="relative tolerance for numeric nesting")
    args = p.parse_args(argv)

    root = Path(args.root)
    outpath = Path(args.output)
    report: dict[str, Any] = {"root": str(root), "groups": {}, "values": {}, "summary": {}}
    failures = 0

    ca = root / "cluster_results" / "ca"
    grid5000 = ca / "grid_5000m"
    grid25000 = ca / "grid_25000m"
    values_dir = ca / "values"

    g25 = find_tif_groups(grid25000)
    g5 = find_tif_groups(grid5000)

    report["meta"] = {"found_5km": list(g5.keys()), "found_25km": list(g25.keys())}

    groups = sorted(set(list(g25.keys()) + list(g5.keys())))
    if not groups:
        print(f"ERROR: no grid stacks found under {ca} - artifact missing or malformed",
              file=sys.stderr)
        return 2
    for grp in groups:
        rec: dict[str, Any] = {"group": grp}
        coarse = g25.get(grp)
        fine = g5.get(grp)
        if coarse is None:
            rec.update({"present_25km": False, "present_5km": bool(fine), "pass": False, "reason": "missing_25km"})
            report["groups"][grp] = rec
            failures += 1
            continue
        if fine is None:
            rec.update({"present_25km": True, "present_5km": False, "pass": False, "reason": "missing_5km"})
            report["groups"][grp] = rec
            failures += 1
            continue
        rec_result = validate_group(fine, coarse, abs_tol=args.abs_tol, rel_tol=args.rel_tol)
        rec.update({"present_25km": True, "present_5km": True, "validation": rec_result, "pass": rec_result.get("pass", False)})
        if not rec.get("pass", False):
            failures += 1
        report["groups"][grp] = rec

    # values PNGs: the webapp paints cells from these; a missing one is a broken (group, goal, tier)
    missing_values = []
    saturated = {}
    for grp in groups:
        for slug in GOAL_SLUGS:
            for res in (25000, 5000):
                name = f"{grp}_{slug}_grid_{res}m.png"
                png = values_dir / name
                if not png.exists():
                    missing_values.append(name)
                    continue
                frac = top_of_ramp_fraction(png)
                if frac > SATURATION_MAX_FRAC:
                    saturated[name] = round(frac, 4)
    if missing_values:
        report["values"]["missing"] = missing_values
        failures += len(missing_values)
    if saturated:
        report["values"]["saturated"] = saturated
        failures += len(saturated)
    report["values"]["n_found"] = len(list(values_dir.glob("*.png"))) if values_dir.exists() else 0

    # summary
    n_total = len(groups)
    n_pass = sum(1 for g in report["groups"].values() if g.get("pass"))
    n_fail = n_total - n_pass + len(missing_values) + len(saturated)
    report["summary"].update({"n_groups": n_total, "n_pass": n_pass, "n_fail": n_fail,
                              "missing_values": len(missing_values), "saturated_values": len(saturated)})

    # write report (numpy scalars leak in from the metrics; coerce them)
    def _jsonable(o):
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(f"not JSON serializable: {type(o)}")

    try:
        outpath.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=_jsonable))
    except OSError as e:
        print(f"ERROR writing report: {e}", file=sys.stderr)
        return 2

    # print short summary
    print(json.dumps({"n_groups": n_total, "n_pass": n_pass, "n_fail": n_fail,
                      "missing_values": len(missing_values), "saturated_values": len(saturated)}))

    return 0 if n_fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
