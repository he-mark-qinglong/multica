"""Tests for validation/metrics.py — thin wrapper over compute_metrics.

The wrapper keeps the pre-unification function signatures but delegates the
math to _shared/validation/compute_metrics.compute_metrics (single schema).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from validation import metrics as M


def _daily_returns(vals: list[float]) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(vals), freq="D")
    return pd.Series(vals, index=idx, dtype=float)


def _equity_from_bar_returns(rets: list[float], start: float = 100_000.0) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(rets) + 1, freq="h")
    eq = [start]
    for r in rets:
        eq.append(eq[-1] * (1.0 + r))
    return pd.Series(eq, index=idx, dtype=float)


# --- daily_returns -----------------------------------------------------------

def test_daily_returns_resamples_to_daily():
    eq = _equity_from_bar_returns([0.01] * 48)  # 49 hourly bars spanning 2 days
    dr = M.daily_returns(eq)
    assert len(dr) >= 1
    assert isinstance(dr.index, pd.DatetimeIndex)


def test_daily_returns_empty():
    assert M.daily_returns(pd.Series(dtype=float)).empty


# --- annualized_return --------------------------------------------------------

def test_annualized_return_matches_geometric_formula():
    rets = _daily_returns([0.001] * 365)
    expected = (1.001 ** 365) - 1.0
    assert M.annualized_return(rets) == pytest.approx(expected, rel=1e-9)


def test_annualized_return_total_wipeout_is_minus_one():
    rets = _daily_returns([0.5, -1.0, 0.1])
    assert M.annualized_return(rets) == -1.0


def test_annualized_return_empty_is_zero():
    assert M.annualized_return(pd.Series(dtype=float)) == 0.0


# --- annualized_sharpe --------------------------------------------------------

def test_annualized_sharpe_positive_drift_is_positive():
    rng = np.random.default_rng(7)
    rets = _daily_returns(list(rng.normal(0.002, 0.01, 200)))
    sharpe = M.annualized_sharpe(rets)
    # close to the direct daily formula (leading-bar convention shifts it <1%)
    direct = rets.mean() / rets.std(ddof=1) * np.sqrt(365)
    assert sharpe == pytest.approx(direct, rel=0.05)
    assert sharpe > 0


def test_annualized_sharpe_too_few_points_is_zero():
    assert M.annualized_sharpe(_daily_returns([0.01])) == 0.0
    assert M.annualized_sharpe(pd.Series(dtype=float)) == 0.0


def test_annualized_sharpe_zero_variance_is_zero():
    assert M.annualized_sharpe(_daily_returns([0.0] * 10)) == 0.0


# --- max_drawdown (negative convention) ---------------------------------------

def test_max_drawdown_is_negative_fraction():
    # 100k → 120k → 96k = 20% drawdown → -0.20
    eq = _equity_from_bar_returns([0.20, -0.20])
    assert M.max_drawdown(eq) == pytest.approx(-0.20, abs=1e-6)


def test_max_drawdown_empty_is_zero():
    assert M.max_drawdown(pd.Series(dtype=float)) == 0.0


# --- profit_factor / win_rate (per trade) --------------------------------------

def test_profit_factor_per_trade():
    assert M.profit_factor([0.04, -0.03]) == pytest.approx(0.04 / 0.03, rel=1e-9)
    assert M.profit_factor([0.01, 0.02]) == float("inf")
    assert M.profit_factor([]) == 0.0


def test_win_rate_per_trade():
    assert M.win_rate([0.01, -0.02, 0.03, -0.01]) == pytest.approx(0.5)
    assert M.win_rate([]) == 0.0


# --- metrics_from_run ----------------------------------------------------------

def test_metrics_from_run_schema_and_delegation():
    eq = _equity_from_bar_returns([0.002] * 96)  # 4 days of hourly bars
    pnls = [0.01, -0.005, 0.02, 0.015]
    m = M.metrics_from_run(eq, pnls)
    for key in ("sharpe", "annualized_return", "max_drawdown", "profit_factor",
                "win_rate", "n_trades", "total_return", "daily_returns"):
        assert key in m, f"missing key {key}"
    assert m["n_trades"] == 4
    assert m["win_rate"] == pytest.approx(0.75)  # per trade, not per bar
    assert m["sharpe"] > 0
    assert m["max_drawdown"] <= 0.0  # negative convention
    assert m["profit_factor"] == pytest.approx((0.01 + 0.02 + 0.015) / 0.005, rel=1e-9)
    assert m["total_return"] == pytest.approx(eq.iloc[-1] / eq.iloc[0] - 1.0, rel=1e-9)


def test_metrics_from_run_drawdown_matches_bar_level():
    eq = _equity_from_bar_returns([0.10, -0.30])  # 30% drawdown
    m = M.metrics_from_run(eq, [0.10, -0.30])
    assert m["max_drawdown"] == pytest.approx(-0.30, abs=1e-6)


def test_metrics_from_run_empty_equity():
    m = M.metrics_from_run(pd.Series(dtype=float), [])
    assert m["sharpe"] == 0.0
    assert m["annualized_return"] == 0.0
    assert m["max_drawdown"] == 0.0
    assert m["n_trades"] == 0
    assert m["total_return"] == 0.0


def test_public_metrics_strips_daily_returns():
    m = {"sharpe": 1.0, "daily_returns": pd.Series([0.01])}
    pub = M.public_metrics(m)
    assert "daily_returns" not in pub
    assert pub["sharpe"] == 1.0
