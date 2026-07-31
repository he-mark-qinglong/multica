"""Tests for tail_risk.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pytest

from _shared.market_making.tail_risk import (
    TailRiskResult, compute_tail_risk,
    historical_var, historical_cvar,
    max_consecutive_losses, cornish_fisher_var,
)


def test_var_basic():
    pnl = list(range(-50, 51))  # uniform -50..50
    var = historical_var(pnl, 0.95)
    assert var > 0  # positive = loss magnitude
    assert 40 < var < 50  # ~5th percentile


def test_var_empty():
    assert historical_var([], 0.95) == 0.0


def test_cvar_exceeds_var():
    pnl = list(range(-50, 51))
    var = historical_var(pnl, 0.95)
    cvar = historical_cvar(pnl, 0.95)
    assert cvar >= var  # tail average >= threshold


def test_cornish_fisher_normal():
    np.random.seed(42)
    pnl = np.random.normal(0, 10, 1000).tolist()
    cf = cornish_fisher_var(pnl, 0.95)
    hist = historical_var(pnl, 0.95)
    # For near-normal data, CF and historical should be close
    assert abs(cf - hist) / max(1, hist) < 0.5  # within 50%


def test_consecutive_losses():
    pnl = [1, -1, -1, -1, 2, -1, -1]
    assert max_consecutive_losses(pnl) == 3


def test_consecutive_losses_no_losses():
    assert max_consecutive_losses([1, 2, 3]) == 0


def test_compute_tail_risk_full():
    np.random.seed(42)
    pnl = np.random.normal(2, 10, 200).tolist()
    result = compute_tail_risk(pnl)
    assert isinstance(result, TailRiskResult)
    assert result.n_samples == 200
    assert result.var_95_bp > 0
    assert result.var_99_bp >= result.var_95_bp  # 99% more extreme
    assert result.cvar_95_bp >= result.var_95_bp
    assert result.worst_case_bp > 0


def test_compute_tail_risk_empty():
    result = compute_tail_risk([])
    assert result.n_samples == 0
    assert result.var_95_bp == 0.0
