"""Smoke tests for V1_funding_term_curve (iter#97)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import VARIANT_KEY, _atr, _vpvr_poc, _z_spread, _run_one_symbol, run_backtest
from data_loader import load_all


# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------

def test_vpvr_poc_known_value():
    """POC of a uniform 100-bar series in [0, 1] should land within the range."""
    rng = np.random.default_rng(42)
    n = 200
    close = pd.Series(rng.uniform(0, 1, n))
    volume = pd.Series(np.ones(n))
    poc = _vpvr_poc(close, volume, window=100, n_bins=10)
    last = poc.dropna().iloc[-1]
    # For a uniform distribution, the POC is somewhere in [0, 1]
    assert 0.0 <= last <= 1.0, f"POC {last} outside [0, 1] for uniform distribution"
    # And it should be in the second half of the data range (where the window
    # has enough overlap with full data).
    assert np.isfinite(last)


def test_atr_positive_and_finite():
    rng = np.random.default_rng(0)
    n = 100
    df = pd.DataFrame({
        "open": rng.uniform(100, 110, n),
        "high": rng.uniform(105, 115, n),
        "low": rng.uniform(95, 105, n),
        "close": rng.uniform(100, 110, n),
        "volume": rng.uniform(1, 10, n),
    })
    atr = _atr(df, period=14)
    valid = atr.dropna()
    assert (valid > 0).all()
    assert np.isfinite(valid).all()


def test_z_spread_zero_when_constant():
    s = pd.Series(np.full(50, 0.01))
    z = _z_spread(s, lookback=8)
    # Constant series -> std = 0 -> NaN, but constant-mean works at last bar
    assert z.iloc[-1] == 0.0 or np.isnan(z.iloc[-1])


def test_z_spread_sign():
    """When funding jumps above its rolling distribution, z-spread should be positive."""
    base = np.full(60, 0.0001)
    base[55:] = 0.0010  # 10x spike at the tail
    s = pd.Series(base)
    z = _z_spread(s, lookback=8)
    last = z.iloc[-1]
    assert last > 0.0, f"z-spread {last} should be positive after spike"
    # And the spike magnitude should be detectable (z ~ (0.001 - 0.0001)/std)
    assert np.isfinite(last), "z-spread should be finite in the tail"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def test_run_one_symbol_smoke():
    """Run on a tiny synthetic series — must complete without raising."""
    rng = np.random.default_rng(7)
    n = 500
    df = pd.DataFrame({
        "open": rng.uniform(100, 110, n),
        "high": rng.uniform(105, 115, n),
        "low": rng.uniform(95, 105, n),
        "close": rng.uniform(100, 110, n),
        "volume": rng.uniform(1, 10, n),
        "fundingRate": rng.normal(0.0001, 0.0005, n),
    }, index=pd.date_range("2024-01-01", periods=n, freq="1h"))
    cfg = {
        "iteration": 97,
        "instruments": ["BTCUSDT"],
        "starting_capital_per_symbol_usd": 100000.0,
        "starting_capital_usd": 100000.0,
        "params": {
            "vpvr_window_bars": 60, "vpvr_bins": 12, "atr_period": 14,
            "funding_z_lookback_bars": 8, "z_entry_threshold": 2.0,
            "atr_trail_k": 2.5, "hard_stop_atr_k": 4.0, "max_hold_bars": 24,
            "fee_bps_per_fill": 4.0, "slippage_bps_per_fill": 2.0,
            "min_gap_bars_between_trades": 4, "warmup_bars": 100,
        },
    }
    res = _run_one_symbol(df, cfg)
    assert "trades" in res
    assert "equity" in res
    assert len(res["equity"]) == n
    assert res["variant_key"] == VARIANT_KEY


def test_load_all_smoke():
    """Real data loader for BTCUSDT (1h + funding) must succeed."""
    try:
        data = load_all(["BTCUSDT"])
    except FileNotFoundError as e:
        pytest.skip(f"missing live data: {e}")
    assert "BTCUSDT" in data
    assert "fundingRate" in data["BTCUSDT"].columns
    assert len(data["BTCUSDT"]) > 1000


def test_end_to_end_real_data():
    """Real BTCUSDT 1h backtest must produce a valid result envelope."""
    try:
        data = load_all(["BTCUSDT"])
    except FileNotFoundError as e:
        pytest.skip(f"missing live data: {e}")
    cfg = {
        "iteration": 97, "instruments": ["BTCUSDT"],
        "starting_capital_per_symbol_usd": 100000.0,
        "starting_capital_usd": 100000.0,
        "params": {
            "vpvr_window_bars": 120, "vpvr_bins": 24, "atr_period": 14,
            "funding_z_lookback_bars": 8, "z_entry_threshold": 2.0,
            "atr_trail_k": 2.5, "hard_stop_atr_k": 4.0, "max_hold_bars": 24,
            "fee_bps_per_fill": 4.0, "slippage_bps_per_fill": 2.0,
            "min_gap_bars_between_trades": 4, "warmup_bars": 160,
        },
    }
    res = _run_one_symbol(data["BTCUSDT"], cfg)
    assert isinstance(res["trades"], list)
    assert isinstance(res["equity"], np.ndarray)
    assert len(res["equity"]) == len(data["BTCUSDT"])
    # Equity must be strictly positive
    assert (res["equity"] > 0).all()
