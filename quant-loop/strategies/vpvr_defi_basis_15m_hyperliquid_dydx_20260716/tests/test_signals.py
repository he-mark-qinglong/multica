"""Unit tests for vpvr_defi_basis_15m_hyperliquid_dydx_20260716 build_signals."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_signals import build_signals


PARAMS = {
    "vpvr_window_bars": 480,
    "vpvr_bins": 24,
    "atr_period": 14,
    "basis_z_lookback_bars": 168,
    "basis_z_threshold": 1.5,
    "poc_atr_buffer": 0.75,
    "take_profit_atr_k": 2.5,
    "hard_stop_atr_k": 1.5,
    "max_hold_bars": 60,
    "risk_target_pct": 0.005,
    "cooldown_bars": 12,
    "fee_bps_per_fill": 4.0,
    "slippage_bps_per_fill": 2.0,
}


def _make_df(n: int = 2000) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dt = pd.date_range("2023-01-01", periods=n, freq="15min")
    close = 30000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.0004, size=n)))
    high = close * (1 + rng.uniform(0.0002, 0.0010, size=n))
    low = close * (1 - rng.uniform(0.0002, 0.0010, size=n))
    open_ = close * (1 + rng.normal(0.0, 0.0002, size=n))
    volume = rng.lognormal(0.0, 0.5, size=n) * 100.0
    basis = rng.normal(0.0, 0.0003, size=n)
    # Insert a strong negative basis extreme near the end.
    basis[-50:-30] -= 0.004
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "basis": basis,
    }, index=dt)


def test_build_signals_returns_expected_columns():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    assert "signal" in out.columns
    assert "vpvr_poc" in out.columns
    assert "atr" in out.columns
    assert "basis_z" in out.columns
    assert "poc_distance_atr" in out.columns


def test_signal_values_are_valid():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    assert set(out["signal"].unique()).issubset({-1, 0, 1})


def test_extreme_negative_basis_generates_long_signal():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    # The artificial negative basis extreme should produce at least one long.
    assert (out["signal"].iloc[-60:-20] == 1).any()


def test_extreme_positive_basis_generates_short_signal():
    df = _make_df(2000)
    df.loc[df.index[-60:-40], "basis"] += 0.004
    out = build_signals(df, PARAMS)
    assert (out["signal"].iloc[-70:-30] == -1).any()


def test_signals_are_zero_during_warmup():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    warmup = max(PARAMS["vpvr_window_bars"], PARAMS["basis_z_lookback_bars"], PARAMS["atr_period"])
    assert (out["signal"].iloc[:warmup] == 0).all()
