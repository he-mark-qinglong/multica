"""Tests for cash_carry_combo_v1 strategy."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")
sys.path.insert(0, "/Users/mark/multica/quant-loop/strategies/cash_carry_combo_v1_20260731")

import numpy as np
import pandas as pd
import pytest

from strategy import (
    CarryConfig, symbol_equity_curve, compute_metrics,
)


def _series(vals, start="2024-01-01", freq="8h"):
    idx = pd.date_range(start, periods=len(vals), freq=freq, tz="UTC")
    return pd.Series(vals, index=idx)


def test_equity_flat_when_zero_funding():
    fund = _series([0.0] * 10)
    basis = _series([1.0] * 10)
    eq = symbol_equity_curve(fund, basis, leverage=1.0, entry_exit_cost_bp=0.0)
    assert (eq == 0.0).all()


def test_equity_accumulates_funding():
    fund = _series([1.0] * 10)          # +1bp per event
    basis = _series([0.0] * 10)         # flat basis
    eq = symbol_equity_curve(fund, basis, leverage=1.0, entry_exit_cost_bp=0.0)
    assert eq.iloc[-1] == pytest.approx(10.0)


def test_leverage_scales():
    fund = _series([1.0] * 10)
    basis = _series([0.0] * 10)
    eq1 = symbol_equity_curve(fund, basis, leverage=1.0, entry_exit_cost_bp=0.0)
    eq2 = symbol_equity_curve(fund, basis, leverage=2.0, entry_exit_cost_bp=0.0)
    assert eq2.iloc[-1] == pytest.approx(2 * eq1.iloc[-1])


def test_basis_narrowing_pays_short_perp():
    fund = _series([0.0] * 5)
    basis = _series([10.0, 8.0, 6.0, 4.0, 2.0])  # perp premium narrows
    eq = symbol_equity_curve(fund, basis, leverage=1.0, entry_exit_cost_bp=0.0)
    # short perp + long spot: basis_in - basis_t > 0 → profit
    assert eq.iloc[-1] == pytest.approx(10.0 - 2.0)


def test_filter_stops_negative_funding():
    # 100 events: first 90 sum positive, then 30 negative → filter closes
    fund = _series([1.0] * 90 + [-5.0] * 30)
    basis = _series([0.0] * 120)
    eq = symbol_equity_curve(fund, basis, leverage=1.0,
                             entry_exit_cost_bp=0.0,
                             use_filter=True, filter_window=90)
    # income stops accumulating once trailing sum goes ≤ 0
    assert eq.iloc[-1] <= 90.0


def test_costs_deducted():
    fund = _series([10.0] * 5)
    basis = _series([0.0] * 5)
    eq = symbol_equity_curve(fund, basis, leverage=1.0, entry_exit_cost_bp=30.0)
    assert eq.iloc[-1] == pytest.approx(50.0 - 30.0)


def test_metrics_basic():
    idx = pd.date_range("2024-01-01", periods=100, freq="8h", tz="UTC")
    eq = pd.Series(np.linspace(0, 100, 100))  # straight up, no DD
    ts = pd.Series(idx)
    m = compute_metrics(eq, ts)
    assert m["total_return_bp"] == pytest.approx(100.0)
    assert m["max_drawdown_bp"] == pytest.approx(0.0)
    assert m["annualized_return"] > 0
    assert m["calmar"] == float("inf")


def test_metrics_drawdown():
    idx = pd.date_range("2024-01-01", periods=100, freq="8h", tz="UTC")
    eq = pd.Series([0, 100, 50, 80, 30] + [30] * 95)
    ts = pd.Series(idx)
    m = compute_metrics(eq, ts)
    assert m["max_drawdown_bp"] == pytest.approx(-70.0)  # 30 - 100
