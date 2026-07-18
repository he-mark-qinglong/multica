"""Tests for the backtrader OOS cross-validation adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from framework_adapter_backtrader import (  # noqa: E402
    compute_trade_metrics,
    make_oos_folds,
    relative_divergence_pct,
)


def test_make_oos_folds_covers_second_half_without_gaps():
    folds = make_oos_folds(100, n_folds=4)

    assert folds == [(50, 63), (63, 75), (75, 88), (88, 100)]
    assert folds[0][0] == 50
    assert folds[-1][1] == 100
    assert all(left[1] == right[0] for left, right in zip(folds, folds[1:]))


def test_compute_trade_metrics_compounds_returns_and_drawdown():
    metrics = compute_trade_metrics([0.10, -0.20, 0.05], span_days=365.25)

    assert metrics["n_trades"] == 3
    assert metrics["total_return"] == pytest.approx((1.10 * 0.80 * 1.05) - 1.0)
    assert metrics["max_dd"] == pytest.approx(-0.20)
    assert metrics["sharpe"] < 0.0


def test_relative_divergence_uses_epsilon_for_zero_baseline():
    assert relative_divergence_pct(0.0, 0.0) == 0.0
    assert relative_divergence_pct(0.01, 0.0, epsilon=1e-6) == pytest.approx(1_000_000.0)
