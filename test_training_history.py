"""Analytic training-history sensitivity matches independent subset enumeration."""

from itertools import combinations, product

import numpy as np
import pandas as pd
import pytest

import voi_backtest as vb


def evaluate(train, test, m, k):
    cells = pd.DataFrame({"gi": [0], "gj": [0]})
    aux = {(0, 0): (set(train), list(test), list(train))}
    return vb.expected_standardized_new_at_k(cells, aux, M=m, K=k)[0]


def enumeration(train, test, m, k):
    pairs = product(combinations(range(len(train)), m), combinations(range(len(test)), k))
    return np.mean([len({test[i] for i in ti} - {train[i] for i in tr}) for tr, ti in pairs])


@pytest.mark.parametrize("train,test", [([1, 1, 2], [1, 2, 2, 3]), ([1], [1]), ([], [1, 1]),
                                        ([1, 2], []), ([], [])])
def test_standardized_expectation_matches_all_independent_subsets(train, test):
    for m in range(len(train) + 1):
        for k in range(len(test) + 1):
            assert evaluate(train, test, m, k) == pytest.approx(enumeration(train, test, m, k), abs=1e-12)


def test_randomized_small_train_test_pools_match_enumeration_and_full_history():
    rng = np.random.default_rng(0)
    for _ in range(100):
        train = rng.integers(0, 4, size=int(rng.integers(0, 7))).tolist()
        test = rng.integers(0, 4, size=int(rng.integers(0, 7))).tolist()
        m = int(rng.integers(0, len(train) + 1))
        k = int(rng.integers(0, len(test) + 1))
        exact = evaluate(train, test, m, k)
        assert exact == pytest.approx(enumeration(train, test, m, k), abs=1e-12)
        assert 0 <= exact <= k
        assert evaluate(train[::-1], test[::-1], m, k) == exact
        cells = pd.DataFrame({"gi": [0], "gj": [0]})
        full = vb.expected_new_at_k(cells, {(0, 0): (set(train), test, train)}, K=k)[0]
        assert exact + 1e-12 >= full
        assert evaluate(train, test, len(train), k) == pytest.approx(full, abs=1e-12)


def test_insufficient_train_and_test_budgets_are_unavailable():
    assert np.isnan(evaluate([1], [2, 2], 2, 1))
    assert np.isnan(evaluate([1, 1], [2], 1, 2))
    assert evaluate([], [], 0, 0) == 0
    assert evaluate([1], [1], 0, 1) == 1


@pytest.mark.parametrize("m,k", [(-1, 1), (1, -1), (1.5, 1), (1, 1.5), (True, 1), (1, np.bool_(False))])
def test_invalid_standardization_budget_fails_clearly(m, k):
    with pytest.raises(ValueError, match="non-negative integers"):
        evaluate([1], [1], m, k)


def test_missing_auxiliary_cell_is_unavailable():
    cells = pd.DataFrame({"gi": [0], "gj": [0]})
    assert np.isnan(vb.expected_standardized_new_at_k(cells, {}, M=1, K=1)[0])
