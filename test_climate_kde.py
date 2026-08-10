"""Gate on the binned climate KDE (#87): it must agree with the exact pairwise sum.

`kde_binned` replaces an O(queries x references) evaluation that is unaffordable on the 5 km
lattice. The substitution is only legitimate if it moves the `env` axis by less than the app
can show, so the assertions below are on the ranked axis the app actually paints, measured on
the real 25 km climate field -- not on the intermediate density.

    .venv/bin/python -m pytest -q test_climate_kde.py
"""
import os

import numpy as np
import pytest

from climate_kde import kde_binned, kde_exact, surprisal_rank

REAL_Z = "cluster_results/ca/grid_25000m/climate_z.npy"


def spearman(a, b):
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


@pytest.fixture(scope="module")
def synthetic():
    rng = np.random.default_rng(0)
    ref = np.concatenate([rng.normal(0, 1, (4000, 3)), rng.normal(2.5, 0.4, (1000, 3))])
    w = rng.lognormal(0, 1.5, len(ref))
    q = rng.normal(0, 1.6, (3000, 3))
    return ref, w, q


def test_binned_matches_exact_density(synthetic):
    """Accuracy where the density is resolvable, i.e. within four orders of magnitude of the
    peak. Further out the binned estimate diverges by large factors, but that is a surprisal of
    9+ nats either way and the cell ranks at the top of the axis regardless -- which is what
    `test_binned_preserves_ordering` and the real-field gate below actually bound."""
    ref, w, q = synthetic
    got, want = kde_binned(ref, w, q), kde_exact(ref, w, q)
    live = want > want.max() * 1e-4
    assert live.mean() > 0.99, live.mean()
    rel = np.abs(got[live] - want[live]) / want[live]
    assert np.median(rel) < 0.005, np.median(rel)
    assert np.percentile(rel, 99) < 0.03, np.percentile(rel, 99)


def test_binned_preserves_ordering(synthetic):
    ref, w, q = synthetic
    assert spearman(kde_binned(ref, w, q), kde_exact(ref, w, q)) > 0.9999


def test_far_tail_is_finite_and_monotone(synthetic):
    """Queries past the binning span must not blow up or invert -- surprisal is read there."""
    ref, w, _ = synthetic
    far = np.stack([np.linspace(3, 12, 40)] * 3, axis=1)
    got = kde_binned(ref, w, far)
    assert np.all(np.isfinite(got)) and np.all(got >= 0)
    assert np.all(np.diff(got[:20]) <= 1e-12)     # still decreasing inside the span


def test_empty_reference_scores_zero():
    Z = np.random.default_rng(1).normal(0, 1, (200, 3))
    assert np.all(surprisal_rank(Z, np.zeros(200)) == 0)


def test_nan_climate_cells_score_zero():
    rng = np.random.default_rng(2)
    Z = rng.normal(0, 1, (500, 3))
    Z[:20] = np.nan
    out = surprisal_rank(Z, rng.random(500))
    assert np.all(out[:20] == 0)
    assert out[20:].max() == pytest.approx(1.0)


@pytest.mark.skipif(not os.path.exists(REAL_Z), reason="run build_fullgrid_ca.py first")
def test_real_climate_field_env_axis_agrees():
    """The load-bearing gate: the ranked `env` axis on Canada's real 25 km climate field."""
    Z = np.load(REAL_Z).astype(float)
    rng = np.random.default_rng(3)
    w = np.where(rng.random(len(Z)) < 0.45, rng.lognormal(0, 2.0, len(Z)), 0.0)
    fast = surprisal_rank(Z, w, kde=kde_binned)
    slow = surprisal_rank(Z, w, kde=kde_exact)
    assert spearman(fast, slow) > 0.99999
    assert np.abs(fast - slow).max() < 0.01      # worst cell moves < 1 percentile point
