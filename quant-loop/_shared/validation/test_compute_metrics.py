"""Tests for compute_metrics shared helper.

Per strategy-worker-2 gap #1 (SMA-34992 / 2026-07-20): no compute_metrics helper
exists, so each strategy hand-rolls the 9-key dict and risks per-strategy
metric drift (cf. SMA-34922 max_dd sentinel bug).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from _shared.validation.compute_metrics import compute_metrics


def _flat_equity(n: int, start: float = 100_000.0) -> pd.Series:
    """Zero-return equity curve for shape-only tests."""
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.Series([start] * n, index=idx, dtype=float)


def _equity_with_returns(returns: list[float], start: float = 100_000.0) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(returns), freq="D")
    eq = [start]
    for r in returns:
        eq.append(eq[-1] * (1.0 + r))
    return pd.Series(eq[1:], index=idx, dtype=float)


def test_compute_metrics_returns_all_nine_keys():
    """compute_metrics must return the 9 keys metrics_validator expects."""
    eq = _flat_equity(30)
    m = compute_metrics(eq, n_trades=0, freq_per_year=365)
    expected = {
        "sharpe_daily",
        "annualized_return",
        "max_drawdown_pct",
        "profit_factor",
        "n_trades",
        "n_bars",
        "win_rate",
        "calmar",
        "sortino",
    }
    assert expected.issubset(m.keys()), f"missing keys: {expected - set(m.keys())}"


def test_compute_metrics_flat_curve_has_zero_sharpe():
    """Zero-return equity → Sharpe=0, ann_ret=0, max_dd=0, win_rate=0, PF=0."""
    eq = _flat_equity(60)
    m = compute_metrics(eq, n_trades=0, freq_per_year=365)
    assert m["sharpe_daily"] == 0.0
    assert m["annualized_return"] == 0.0
    assert m["max_drawdown_pct"] == 0.0
    assert m["n_trades"] == 0
    assert m["n_bars"] == 60
    assert m["win_rate"] == 0.0
    assert m["profit_factor"] == 0.0
    # calmar / sortino should be finite; 0/0 → define convention
    assert math.isfinite(m["calmar"])
    assert math.isfinite(m["sortino"])


def test_compute_metrics_uptrend_has_positive_sharpe_and_ann():
    """Constant +0.1%/day equity → Sharpe > 0 and annualized return matches (1.001^365 - 1).

    Note on win_rate: pct_change drops the first bar (NaN→0), so on an all-up
    365-bar equity we get 364 positive bar returns out of 365 bars → win_rate
    ≈ 364/365 (the dropped first bar is treated as a zero-return, not a win).
    """
    rets = [0.001] * 365
    eq = _equity_with_returns(rets)
    m = compute_metrics(eq, n_trades=100, freq_per_year=365)
    expected_ann = (1.001 ** 365) - 1.0
    assert m["annualized_return"] == pytest.approx(expected_ann, rel=1e-9)
    assert m["sharpe_daily"] > 0.0
    assert m["max_drawdown_pct"] == 0.0
    assert m["profit_factor"] == 0.0  # no losing bars → PF undefined → 0 convention
    assert m["win_rate"] == pytest.approx(364 / 365, rel=1e-9)
    assert m["n_trades"] == 100
    assert m["n_bars"] == 365


def test_compute_metrics_drawdown_is_decimal():
    """max_drawdown_pct must be a fraction (e.g. -0.18), NOT a percent (-18)."""
    # 100k → 120k → 96k = 20% drawdown
    eq = _equity_with_returns([0.20, -0.20])
    m = compute_metrics(eq, n_trades=2, freq_per_year=365)
    assert m["max_drawdown_pct"] == pytest.approx(-0.20, abs=1e-6)
    # explicit guard: reject -20 sentinel
    assert m["max_drawdown_pct"] > -1.0


def test_compute_metrics_pf_uses_bar_returns_not_dollars():
    """Profit factor = sum(positive returns) / sum(|negative returns|) on bar pct_change.

    The first bar's pct_change is NaN and is filled to 0 (a zero-return bar, NOT
    a winner). So for 5 input returns we get 5 bars with pct_change = [0, -0.01,
    +0.03, -0.02, +0.01] → sum(pos) = 0.04, sum(|neg|) = 0.03 → PF = 1.333.
    """
    rets = [0.02, -0.01, 0.03, -0.02, 0.01]
    eq = _equity_with_returns(rets)
    m = compute_metrics(eq, n_trades=5, freq_per_year=365)
    assert m["profit_factor"] == pytest.approx(0.04 / 0.03, rel=1e-9)


def test_compute_metrics_handles_empty_returns():
    """Empty equity → no NaN, no inf, no crash."""
    eq = pd.Series(dtype=float)
    m = compute_metrics(eq, n_trades=0, freq_per_year=365)
    assert m["n_bars"] == 0
    assert math.isfinite(m["sharpe_daily"])
    assert math.isfinite(m["annualized_return"])
    assert math.isfinite(m["max_drawdown_pct"])
    assert math.isfinite(m["profit_factor"])
    assert math.isfinite(m["calmar"])
    assert math.isfinite(m["sortino"])


def test_compute_metrics_win_rate_per_trade_when_pnls_given():
    """With trade_pnls, win_rate = fraction of trades with pnl > 0.

    The equity curve here is all-up (bar-based win rate would be ~1.0), so a
    win_rate of 0.5 can only come from the per-trade path.
    """
    eq = _equity_with_returns([0.001] * 100)
    m = compute_metrics(eq, n_trades=4, freq_per_year=365,
                        trade_pnls=[0.01, -0.02, 0.03, -0.01])
    assert m["win_rate"] == pytest.approx(0.5, rel=1e-9)


def test_compute_metrics_win_rate_empty_trade_pnls_is_zero():
    """trade_pnls=[] (explicitly no trades) → win_rate 0.0, not bar fallback."""
    eq = _equity_with_returns([0.001] * 50)
    m = compute_metrics(eq, n_trades=0, freq_per_year=365, trade_pnls=[])
    assert m["win_rate"] == 0.0


def test_compute_metrics_win_rate_bar_fallback_when_pnls_omitted():
    """Without trade_pnls, keep the legacy bar-return win_rate convention."""
    eq = _equity_with_returns([0.01, -0.01, 0.01, 0.01])
    m = compute_metrics(eq, n_trades=3, freq_per_year=365)
    # 4 bars, pct_change = [0, -0.01, +0.01, +0.01] → 2 positive of 4 bars
    assert m["win_rate"] == pytest.approx(0.5, rel=1e-9)


def test_compute_metrics_max_dd_negative_aligned_with_enforce_g3():
    """max_drawdown_pct must be negative so enforce G3 (> -0.25) is comparable."""
    eq = _equity_with_returns([0.10, -0.30])  # 100k → 110k → 77k = 30% dd
    m = compute_metrics(eq, n_trades=2, freq_per_year=365)
    assert m["max_drawdown_pct"] == pytest.approx(-0.30, abs=1e-6)
    assert m["max_drawdown_pct"] <= 0.0