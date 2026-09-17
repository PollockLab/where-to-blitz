"""Controls on the VOI backtest statistics themselves.

The backtest's headline is a rank correlation between a scarcity-driven priority and
a "species new to this cell" outcome. Both sides of that correlation are sensitive to
how much of the cell's record the statistic is allowed to see, so the tests here are
about what the statistic measures when there is nothing to measure.
"""
import numpy as np
import pandas as pd

import voi_backtest as vb

SPECIES_POOL = 300
N_CELLS = 60
N_TEST = 30


def _null_effect_frame(seed=7):
    """Observations where discovery is independent of prior sampling, by construction.

    Every cell draws train and test observations uniformly from one shared species
    pool, so at equal seen-set and equal test effort no cell can out-discover another.
    Only `n_train` varies across cells, which is exactly what scarcity ranks on.
    """
    rng = np.random.default_rng(seed)
    n_trains = np.linspace(12, 240, N_CELLS).astype(int)
    rows = []
    for i, n_train in enumerate(n_trains):
        lat = 49.0 + i * vb.RES          # one cell per row of the grid
        lon = -123.0
        for tid in rng.integers(0, SPECIES_POOL, size=n_train):
            rows.append((lat, lon, "2025-06-15", int(tid)))
        for tid in rng.integers(0, SPECIES_POOL, size=N_TEST):
            rows.append((lat, lon, "2025-08-15", int(tid)))
    df = pd.DataFrame(rows, columns=["lat", "lon", "observed_on", "taxon_id"])
    df["rank"] = "species"
    return df


def test_single_rarefaction_is_positive_when_the_true_effect_is_zero():
    """Equalizing test effort alone still rewards cells with a small seen set.

    scarcity = norm(1/n_train), so a low-n_train cell has fewer species already
    recorded and more of any fixed draw counts as new. That is an identity, not
    discovery, and it is what the doubly-rarefied statistic exists to remove.
    """
    cells = vb.build_cells(_null_effect_frame())
    aux = cells.attrs["aux"]
    cells["rare_newK"] = vb.rarefy_new_at_k(cells, aux, np.random.default_rng(0), K=5)
    rk = cells.dropna(subset=["rare_newK"])
    assert len(rk) == N_CELLS
    rho = vb.spearman(rk.scarcity.values, rk.rare_newK.values)
    assert rho > 0.5, f"expected the mechanical artifact, got rho={rho:.3f}"


def test_double_rarefaction_is_near_zero_when_the_true_effect_is_zero():
    """Equalizing the seen set too collapses that artifact to noise."""
    cells = vb.build_cells(_null_effect_frame())
    aux = cells.attrs["aux"]
    recs = vb.double_rarefied_records(cells, aux, K=5, Ms=(10,), reps=200, seed=0)
    (rec,) = recs
    assert rec["n_cells"] == N_CELLS
    rho = rec["scarcity"]["spearman"]
    assert abs(rho) < 0.25, f"expected no effect, got rho={rho:.3f}"
    assert rec["scarcity"]["perm_p"] > 0.05, f"perm p={rec['scarcity']['perm_p']}"


def test_double_rarefaction_is_reported_at_every_requested_m():
    cells = vb.build_cells(_null_effect_frame())
    recs = vb.double_rarefied_records(cells, cells.attrs["aux"], K=5,
                                      Ms=vb.DOUBLE_M, reps=50, seed=0)
    assert [r["M"] for r in recs] == list(vb.DOUBLE_M)
    for rec in recs:
        assert "scarcity" in rec or "note" in rec
