"""Fixed national ramps, so both zoom tiers paint the same colour for the same place (#87).

`discover`, `env`, the `staleness` proxy branch and `urgency` were percentile ranks or min-max
normalizations taken over the cells of whichever grid was being built. Those are population
statistics. The 25 km grid has 23,214 masked cells and the 5 km grid has 536,164, and the 5 km
set is not the 25 km set subdivided (partial-coverage coastal cells resolve in or out), so the
same place came out a different colour at a different zoom -- the one thing a two-tier map
must not do.

The 25 km tier is the reference. It fits, per taxon, the distribution of each *raw* quantity
(density per km2, climate surprisal in nats, forest-loss fraction) and writes the mapping to
breaks.json. Both tiers then map raw -> 0..1 through that one fixed mapping, so a cell's value
is a function of its own raw quantity and nothing else.

Two consequences worth being explicit about:

  - The ramps are per taxon. A single national ramp would put Mollusca, which has three orders
    of magnitude fewer records than All biodiversity, at one end of the colour scale
    everywhere. Each taxon spans the full ramp; the axes are not comparable across taxa, which
    was already true of the percentile ranks they replace.
  - The raw quantities nest exactly, the displayed values do not. A 25 km density is the mean
    of its 25 children's densities, but the ramp is nonlinear, so the parent's colour is not
    the mean of its children's colours. The nesting gate in build_grid_pmtiles.py therefore
    tests the raw band, not the painted one.

Density, not record count, is the raw quantity behind `discover`: counts are sums over the
cell, so they grow 25x between tiers, while density per km2 is scale-free.
"""
import json
import os

import numpy as np

PATH = "cluster_results/ca/breaks.json"
REF_RES = 25000     # the tier the ramps are fitted on; every other tier reuses them
K = 129             # quantile knots per ramp


def fit_knots(x, k=K):
    """Quantile knots of the finite values of `x`, or None if there is nothing to fit."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return None
    return [float(v) for v in np.quantile(x, np.linspace(0, 1, k))]


def to_unit(x, knots):
    """Map raw values onto 0..1 through a fitted quantile ramp, clamped at both ends.

    Knots that collapse onto one raw value (a tie block, e.g. a floor) share that block's mean
    position, so every cell holding the tied value gets the same output.
    """
    x = np.asarray(x, float)
    if not knots:
        return np.zeros(x.shape)
    xp = np.asarray(knots, float)
    ux, inv = np.unique(xp, return_inverse=True)
    if len(ux) < 2:
        return (x >= ux[0]).astype(float)
    fp = np.linspace(0.0, 1.0, len(xp))
    uf = np.bincount(inv, weights=fp) / np.bincount(inv)
    return np.interp(x, ux, uf)


def fit_minmax(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return {"lo": float(x.min()), "hi": float(x.max())} if len(x) else {"lo": 0.0, "hi": 0.0}


def to_unit_minmax(x, mm):
    """Min-max against fitted bounds. Values past the reference maximum saturate at 1."""
    x = np.asarray(x, float)
    lo, hi = mm["lo"], mm["hi"]
    if hi <= lo:
        return np.zeros(x.shape)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def discover(dens, env, ramp):
    """Under-sampling score: fewer records per km2 = higher; zero-record cells on top.

    Reproduces the lexicographic rank it replaces (density descending, then climate
    distinctiveness among the zero-record cells) as a fixed function: recorded cells occupy
    [0, 1-p_zero) by their density quantile, and the zero-record block occupies
    [1-p_zero, 1] ordered by `env`. `p_zero` is the reference tier's zero fraction, so the
    block boundary sits at the same colour at both zooms even though the 5 km grid has far more
    zero-record cells than the 25 km grid does.
    """
    p0 = ramp["p_zero"]
    d = to_unit(dens, ramp["density_knots"])
    return np.where(np.asarray(dens) > 0, (1.0 - d) * (1.0 - p0), (1.0 - p0) + p0 * np.asarray(env))


def new(res_m, n_cells):
    return {"res_m": int(res_m), "n_ref_cells": int(n_cells), "shared": {}, "taxa": {},
            "note": ("national 0..1 ramps fitted on this tier. Every other tier maps its raw "
                     "quantities through these, so a cell's value depends only on its own raw "
                     "quantity and not on the population of the grid it was built with (#87).")}


def save(breaks, path=PATH):
    with open(path, "w") as _fh:
        json.dump(breaks, _fh, separators=(",", ":"))
    return path


def load(res_m, groups, path=PATH):
    ref = f"GRID_RES={REF_RES} python build_fullgrid_ca.py"
    if not os.path.exists(path):
        raise SystemExit(f"{path} is missing. Build the {REF_RES // 1000} km tier first ({ref}); "
                         f"it fits the national ramps that the {res_m // 1000} km tier has to "
                         f"reuse. Refusing to fit a separate ramp -- the tiers would disagree.")
    with open(path) as _fh:
        b = json.load(_fh)
    if b.get("res_m") != REF_RES:
        raise SystemExit(f"{path} was fitted at {b.get('res_m')} m, expected {REF_RES} m. Rerun {ref}.")
    missing = [g for g in groups if g not in b["taxa"]]
    if missing or "urgency" not in b["shared"]:
        raise SystemExit(f"{path} is incomplete (missing taxa: {missing or 'none'}, "
                         f"shared: {sorted(b['shared'])}). Rerun {ref}.")
    return b
