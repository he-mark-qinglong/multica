"""Tests for portfolio/attribution.py (I10, I15)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.portfolio.attribution import (
    BrinsonResult, ContributionResult, DrawdownAttribution,
    brinson_decomposition, drawdown_attribution, pnl_contribution,
    time_contribution,
)


# ---- Brinson ----

def test_brinson_effects_sum_to_active_return():
    res = brinson_decomposition(
        portfolio_weights={"A": 0.6, "B": 0.4},
        portfolio_returns={"A": 0.10, "B": 0.05},
        benchmark_weights={"A": 0.5, "B": 0.5},
        benchmark_returns={"A": 0.08, "B": 0.04},
    )
    assert isinstance(res, BrinsonResult)
    port_total = 0.6 * 0.10 + 0.4 * 0.05
    bench_total = 0.5 * 0.08 + 0.5 * 0.04
    assert res.total_active_return == pytest.approx(port_total - bench_total)
    assert res.allocation["A"] == pytest.approx(0.1 * 0.08)
    assert res.selection["A"] == pytest.approx(0.5 * 0.02)
    assert res.interaction["A"] == pytest.approx(0.1 * 0.02)


def test_brinson_identical_weights_zero_allocation():
    res = brinson_decomposition(
        {"A": 0.5, "B": 0.5}, {"A": 0.1, "B": 0.1},
        {"A": 0.5, "B": 0.5}, {"A": 0.1, "B": 0.1},
    )
    assert res.total_active_return == pytest.approx(0.0)
    assert all(v == pytest.approx(0.0) for v in res.allocation.values())


# ---- PnL contribution ----

def test_pnl_contribution_shares():
    res = pnl_contribution({"alpha": 300.0, "beta": -100.0, "gamma": 200.0})
    assert isinstance(res, ContributionResult)
    assert res.total_pnl == pytest.approx(400.0)
    assert res.share_of_total["alpha"] == pytest.approx(0.75)
    assert res.share_of_gross["beta"] == pytest.approx(-100.0 / 600.0)
    assert res.top_contributor == "alpha"
    assert res.worst_contributor == "beta"


def test_pnl_contribution_empty_and_zero_total():
    with pytest.raises(ValueError):
        pnl_contribution({})
    res = pnl_contribution({"a": 1.0, "b": -1.0})
    assert res.share_of_total["a"] == 0.0  # zero total -> defined as 0


# ---- Drawdown attribution ----

def _returns_frame():
    idx = pd.date_range("2026-01-01", periods=10, freq="D")
    # 'bad' drives a mid-window crash; 'good' drifts up.
    bad = [0.01, 0.01, -0.10, -0.10, -0.05, 0.01, 0.02, 0.02, 0.02, 0.02]
    good = [0.02] * 10
    return pd.DataFrame({"bad": bad, "good": good}, index=idx)


def test_drawdown_attribution_identifies_window_and_detractor():
    res = drawdown_attribution(_returns_frame(), initial_equity=1000.0)
    assert isinstance(res, DrawdownAttribution)
    assert res.max_drawdown < 0.0
    assert res.peak < res.trough
    assert res.top_detractor == "bad"
    assert res.contributions["bad"] < 0.0
    assert res.contributions["good"] > 0.0
    # Loser shares sum to 1 among losers (only 'bad' here).
    assert res.contribution_shares["bad"] == pytest.approx(1.0)
    assert res.contribution_shares["good"] == 0.0


def test_drawdown_attribution_weighted():
    rets = _returns_frame()
    eq = drawdown_attribution(rets, weights={"bad": 1.0, "good": 0.0})
    w = {"bad": 1.0, "good": 0.0}
    port = rets.mul(pd.Series(w), axis=1).sum(axis=1)
    equity = 1000.0 * (1 + port).cumprod()
    expected_dd = float((equity / equity.cummax() - 1.0).min())
    assert eq.max_drawdown == pytest.approx(expected_dd)
    # 'good' has zero weight -> zero contribution.
    assert eq.contributions["good"] == pytest.approx(0.0)


def test_drawdown_attribution_empty():
    with pytest.raises(ValueError):
        drawdown_attribution(pd.DataFrame())


# ---- Time contribution ----

def test_time_contribution_period_slicing():
    idx = pd.date_range("2026-01-01", periods=62, freq="D")
    rets = pd.DataFrame({"a": 0.001, "b": -0.001}, index=idx)
    tc = time_contribution(rets, freq="ME")
    assert "__total__" in tc.columns
    assert len(tc) == 3  # Jan, Feb, Mar(1 day)
    jan_a = (1.001 ** 31) - 1.0
    assert tc["a"].iloc[0] == pytest.approx(jan_a, rel=1e-9)
    assert tc["__total__"].iloc[0] == pytest.approx(
        (jan_a + ((0.999 ** 31) - 1.0)) / 2.0
    )


def test_time_contribution_empty():
    with pytest.raises(ValueError):
        time_contribution(pd.DataFrame())
