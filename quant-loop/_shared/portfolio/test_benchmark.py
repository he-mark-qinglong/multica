"""Tests for portfolio/benchmark.py (I17)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.portfolio.benchmark import (
    BenchmarkComparison, buy_and_hold, compare_to_benchmark, equal_weight,
)


def test_buy_and_hold_normalized():
    close = pd.Series([100.0, 110.0, 99.0, 121.0])
    eq = buy_and_hold(close)
    assert eq.iloc[0] == 1.0
    assert eq.iloc[-1] == pytest.approx(1.21)
    assert eq.iloc[1] == pytest.approx(1.10)


def test_buy_and_hold_validation():
    with pytest.raises(ValueError):
        buy_and_hold(pd.Series(dtype=float))
    with pytest.raises(ValueError):
        buy_and_hold(pd.Series([100.0, 0.0]))


def test_equal_weight_mean_of_returns():
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    prices = pd.DataFrame({"A": [100.0, 110.0, 121.0],
                           "B": [100.0, 90.0, 99.0]}, index=idx)
    eq = equal_weight(prices)
    # Bar 1: mean ret = (0.10 + (-0.10))/2 = 0 -> eq 1.0
    # Bar 2: mean ret = (0.10 + 0.10)/2 = 0.10 -> eq 1.1
    assert eq.iloc[0] == pytest.approx(1.0)
    assert eq.iloc[-1] == pytest.approx(1.1)


def test_equal_weight_validation():
    with pytest.raises(ValueError):
        equal_weight(pd.DataFrame())


def test_compare_beta_alpha_closed_form():
    rng = np.random.default_rng(7)
    n = 500
    b = rng.normal(0.001, 0.02, n)
    # Strategy = 1.5 * bench + small idiosyncratic return.
    s = 1.5 * b + 0.0005 + rng.normal(0.0, 0.005, n)
    cmp = compare_to_benchmark(pd.Series(s), pd.Series(b))
    assert isinstance(cmp, BenchmarkComparison)
    assert cmp.beta == pytest.approx(1.5, abs=0.1)
    assert cmp.alpha == pytest.approx(0.0005 * 365, abs=0.1)
    assert cmp.correlation > 0.9
    assert cmp.tracking_error > 0.0
    assert cmp.n_periods == n


def test_compare_identical_series():
    r = pd.Series(np.random.default_rng(1).normal(0.001, 0.01, 100))
    cmp = compare_to_benchmark(r, r)
    assert cmp.beta == pytest.approx(1.0)
    assert cmp.tracking_error == pytest.approx(0.0)
    assert cmp.information_ratio == 0.0  # TE == 0 -> defined as 0
    assert cmp.correlation == pytest.approx(1.0)


def test_compare_alignment_and_min_obs():
    a = pd.Series([0.01, 0.02, 0.03], index=pd.date_range("2026-01-01", periods=3))
    b = pd.Series([0.01, 0.02], index=pd.date_range("2026-01-02", periods=2))
    cmp = compare_to_benchmark(a, b)
    assert cmp.n_periods == 2
    with pytest.raises(ValueError):
        compare_to_benchmark(pd.Series([0.01]), pd.Series([0.01]))


def test_up_down_capture():
    b = pd.Series([0.02, -0.02, 0.02, -0.02])
    s = pd.Series([0.01, -0.01, 0.01, -0.01])  # half the move each way
    cmp = compare_to_benchmark(s, b)
    assert cmp.up_capture == pytest.approx(0.5)
    assert cmp.down_capture == pytest.approx(0.5)
