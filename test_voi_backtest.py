"""Controls on the VOI backtest statistics themselves.

The backtest's headline is a rank correlation between a scarcity-driven priority and
a "species new to this cell" outcome. Both sides of that correlation are sensitive to
how much of the cell's record the statistic is allowed to see, so the tests here are
about what the statistic measures when there is nothing to measure.
"""
import numpy as np
import pandas as pd
import pytest

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


def test_density_is_the_exact_rank_inverse_of_scarcity():
    """`density` cannot serve as a baseline: it is `scarcity` reversed, by construction.

    density = norm(n_train) and scarcity = norm(1/n_train) are the same ordering of the
    same cells, one reversed, so their Spearman correlations against any outcome sum to
    zero exactly. A bar that must come out oppositely signed tests nothing.
    """
    cells = vb.build_cells(_null_effect_frame())
    cells["rare_newK"] = vb.rarefy_new_at_k(cells, cells.attrs["aux"],
                                            np.random.default_rng(0), K=5)
    rk = cells.dropna(subset=["rare_newK"])
    a = vb.spearman(rk.scarcity.values, rk.rare_newK.values)
    b = vb.spearman(rk.density.values, rk.rare_newK.values)
    assert round(a + b, 12) == 0.0, f"{a} + {b}"


def test_travel_time_baseline_is_reported_when_the_surface_is_supplied():
    """Travel time is an independent ranking, so it is free to disagree with priority."""
    df = _null_effect_frame()
    travel = pd.DataFrame({"lat": df.lat.unique()})
    travel["lon"] = -123.0
    travel["travel_min"] = np.arange(len(travel), dtype=float) * 7.0
    res, cells = vb.analyse("synthetic", df, K=5, travel=travel, Ms=())
    assert cells.travel_min.notna().all()
    assert res["rate_travel_min"] is not None
    assert res["rarefied_travel_min"] is not None
    assert -1.0 <= res["rarefied_travel_min"]["spearman"] <= 1.0


def test_travel_baseline_is_absent_rather_than_invented():
    res, _ = vb.analyse("synthetic", _null_effect_frame(), K=5, travel=None, Ms=())
    assert res["rate_travel_min"] is None
    assert res["rarefied_travel_min"] is None


def test_load_travel_minutes_returns_none_when_the_build_is_missing(tmp_path):
    assert vb.load_travel_minutes(tmp_path / "nope.json") is None


class CountingPermutation:
    def __init__(self, identity=False):
        self.calls = 0
        self.identity = identity
    def permutation(self, values):
        self.calls += 1
        return values.copy() if self.identity else np.roll(values, 1)


def test_zero_exceedances_have_nonzero_finite_permutation_probability(monkeypatch):
    monkeypatch.setattr(vb, "N_PERM", 9)
    rng = CountingPermutation()
    rho, p, _, _ = vb.perm_test(np.arange(6), np.arange(6), rng)
    assert rho == 1
    assert rng.calls == 9
    assert p == 1 / 10


def test_all_exceedances_have_probability_one_and_explicit_count_overrides_runtime(monkeypatch):
    monkeypatch.setattr(vb, "N_PERM", 9)
    rng = CountingPermutation(identity=True)
    _, p, _, _ = vb.perm_test(np.arange(6), np.arange(6), rng, n=4)
    assert rng.calls == 4
    assert p == 1


@pytest.mark.parametrize("n", [1, 5, 20])
def test_finite_permutation_probabilities_stay_in_their_valid_range(n):
    _, p, _, _ = vb.perm_test(np.arange(6), np.array([4, 3, 0, 2, 5, 1]),
                            np.random.default_rng(7), n=n)
    assert 1 / (n + 1) <= p <= 1


@pytest.mark.parametrize("n", [0, -1, 1.5])
def test_invalid_permutation_counts_fail_clearly(n):
    with pytest.raises(ValueError, match="positive integer"):
        vb.perm_test(np.arange(6), np.arange(6), np.random.default_rng(0), n=n)


def _legacy_perm_test(score, outcome, rng, n):
    """Independent pre-cache algorithm, including float conversion and NaN filtering."""

    def rho(x, y):
        rx = pd.Series(x).rank().values
        ry = pd.Series(y).rank().values
        if np.std(rx) == 0 or np.std(ry) == 0:
            return np.nan
        return float(np.corrcoef(rx, ry)[0, 1])

    obs = rho(score, outcome)
    score = np.asarray(score, float)
    null = np.array([rho(rng.permutation(score), outcome) for _ in range(n)])
    null = null[~np.isnan(null)]
    if np.isnan(obs) or null.size == 0:
        return obs, float("nan"), float("nan"), float("nan")
    exceedances = int((np.abs(null) >= abs(obs)).sum())
    return obs, float((exceedances + 1) / (null.size + 1)), float(null.mean()), float(null.std())


@pytest.mark.parametrize(
    "score,outcome",
    [
        ([0.3, 0.1, 0.5, 0.2, 0.4], [4.5, 2.1, 1.0, 3.2, 5.4]),
        ([1.0, 1.0, 2.0, 3.0, 3.0, 4.0], [4, 2, 2, 1, 3, 3]),
        ([1.0] * 5, [1, 2, 3, 4, 5]),
        ([1, 2, 3, 4, 5], [2.0] * 5),
        ([1.0, np.nan, 2.0, 3.0], [4, 3, 2, 1]),
        ([1.0, 2.0, 3.0, 4.0], [4, 3, np.nan, 1]),
        ([np.inf, -np.inf, 2.0, 3.0], [4, 3, 2, 1]),
        ([2**53, 2**53 + 1, 2**53 + 2, 2**53 + 3], [4, 3, 1, 2]),
        ([1.0], [2.0]),
        ([], []),
    ],
)
@pytest.mark.parametrize("seed", [0, 7, 53])
@pytest.mark.parametrize("n", [None, 1, 31])
def test_permutation_cache_preserves_legacy_results_and_rng(score, outcome, seed, n, monkeypatch):
    monkeypatch.setattr(vb, "N_PERM", 19)
    actual_rng, legacy_rng = np.random.default_rng(seed), np.random.default_rng(seed)
    actual = vb.perm_test(score, outcome, actual_rng, n=n)
    expected = _legacy_perm_test(score, outcome, legacy_rng, 19 if n is None else n)
    assert np.asarray(actual).tobytes() == np.asarray(expected).tobytes()
    assert actual_rng.random(8).tobytes() == legacy_rng.random(8).tobytes()


@pytest.mark.parametrize("size", [73, 144, 228])
@pytest.mark.parametrize("tied", [False, True])
def test_permutation_cache_preserves_seeded_cell_rank_boundaries(size, tied):
    inputs = np.random.default_rng(size)
    score, outcome = inputs.random(size), inputs.random(size)
    if tied:
        score, outcome = np.round(score, 1), np.round(outcome, 1)
    actual_rng, legacy_rng = np.random.default_rng(13), np.random.default_rng(13)
    actual = vb.perm_test(score, outcome, actual_rng, n=127)
    expected = _legacy_perm_test(score, outcome, legacy_rng, 127)
    assert np.asarray(actual).tobytes() == np.asarray(expected).tobytes()
    assert actual_rng.random(8).tobytes() == legacy_rng.random(8).tobytes()
