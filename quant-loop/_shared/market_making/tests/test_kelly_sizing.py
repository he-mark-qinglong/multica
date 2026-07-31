"""Tests for kelly_sizing.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.market_making.kelly_sizing import (
    KellyParams, KellyResult, compute_kelly, adaptive_kelly_multiplier,
)


def test_insufficient_samples():
    result = compute_kelly([1.0, 2.0, 3.0])  # only 3 samples
    assert not result.is_valid
    assert result.sizing_multiplier == pytest.approx(0.1)  # min


def test_positive_edge_sizes_up():
    # 100 trades, mean +5bp, std ~2bp → strong positive edge
    pnl = [5.0 + (-1)**i * 2 for i in range(100)]
    result = compute_kelly(pnl)
    assert result.is_valid
    assert result.kelly_fraction > 0
    assert result.sizing_multiplier > 0.1


def test_negative_edge_zeros_out():
    pnl = [-5.0 + (-1)**i * 2 for i in range(100)]
    result = compute_kelly(pnl)
    assert result.is_valid
    assert result.kelly_fraction < 0 or result.mean_edge_bp < 1.0
    assert result.sizing_multiplier == pytest.approx(0.1)  # floored


def test_fractional_kelly_applied():
    pnl = [10.0 + (-1)**i * 3 for i in range(100)]
    full = compute_kelly(pnl, KellyParams(fraction=1.0))
    quarter = compute_kelly(pnl, KellyParams(fraction=0.25))
    assert quarter.applied_fraction < full.applied_fraction


def test_multiplier_capped():
    # Extreme positive edge → cap at max_multiplier
    pnl = [50.0] * 100  # constant huge edge, zero variance
    result = compute_kelly(pnl)
    assert result.sizing_multiplier <= 2.0  # capped


def test_convenience_function():
    pnl = [3.0 + (-1)**i * 1 for i in range(50)]
    mult = adaptive_kelly_multiplier(pnl)
    assert 0.1 <= mult <= 2.0
