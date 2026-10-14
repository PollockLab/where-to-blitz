"""Exact sampled-record novelty agrees with exhaustive without-replacement draws."""

from itertools import combinations

import numpy as np
import pandas as pd
import pytest

import voi_backtest as vb


def evaluate(pool, seen, k):
    cells = pd.DataFrame({"gi": [0], "gj": [0]})
    aux = {(0, 0): (set(seen), list(pool), [])}
    return vb.expected_new_at_k(cells, aux, K=k)[0]


def enumeration(pool, seen, k):
    return np.mean([len({pool[i] for i in indices} - set(seen))
                    for indices in combinations(range(len(pool)), k)])


@pytest.mark.parametrize("pool,seen", [([1, 1, 2, 3], [1]), ([1, 2, 3, 4], []),
                                       ([1, 2, 3, 4], [1, 2, 3, 4]), ([1], []), ([], [])])
def test_exact_expectation_matches_every_small_subset(pool, seen):
    for k in range(len(pool) + 1):
        assert evaluate(pool, seen, k) == pytest.approx(enumeration(pool, seen, k), abs=1e-12)


def test_randomized_small_pools_match_exhaustive_enumeration():
    rng = np.random.default_rng(0)
    for _ in range(150):
        n = int(rng.integers(0, 10))
        pool = rng.integers(0, 5, size=n).tolist()
        seen = rng.integers(0, 5, size=int(rng.integers(0, 6))).tolist()
        k = int(rng.integers(0, n + 1))
        exact = evaluate(pool, seen, k)
        assert exact == pytest.approx(enumeration(pool, seen, k), abs=1e-12)
        assert 0 <= exact <= k
        assert evaluate(pool[::-1], seen[::-1], k) == exact


def test_insufficient_records_are_unavailable_and_zero_budget_is_zero():
    assert np.isnan(evaluate([], [], 1))
    assert np.isnan(evaluate([1, 2], [], 3))
    assert evaluate([], [], 0) == 0
    assert evaluate([1, 1], [1], 0) == 0


@pytest.mark.parametrize("k", [-1, 1.5, True, np.bool_(False)])
def test_invalid_sample_budget_fails_clearly(k):
    with pytest.raises(ValueError, match="non-negative integer"):
        evaluate([1, 2], [], k)


def test_singletons_and_seen_only_pools():
    assert evaluate([1], [], 1) == 1
    assert evaluate([1], [1], 1) == 0
    assert evaluate([1, 1, 1], [1], 2) == 0
    assert evaluate([1, 1, 2, 2], [], 4) == 2


def test_rare_taxon_probability_avoids_large_pool_cancellation():
    pool = [1] * 999999 + [2]
    assert evaluate(pool, [1], 1) == pytest.approx(1e-6, rel=1e-12)


def test_missing_auxiliary_cell_retains_insufficient_record_behavior():
    cells = pd.DataFrame({"gi": [0, 1], "gj": [0, 1]})
    result = vb.expected_new_at_k(cells, {(0, 0): ({1}, [1, 2, 2], [])}, K=2)
    assert result[0] == pytest.approx(1)
    assert np.isnan(result[1])
