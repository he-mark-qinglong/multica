"""Unit tests for vpvr_stable_depeg_regime_4h_20260716 build_signals."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_signals import build_signals


PARAMS = {
    "vpvr_window_bars": 180,
    "vpvr_bins": 24,
    "atr_period": 14,
    "depeg_premium_threshold": 0.0015,
    "poc_atr_buffer": 1.0,
    "take_profit_atr_k": 3.0,
    "hard_stop_atr_k": 1.5,
    "max_hold_bars": 60,
    "risk_target_pct": 0.005,
    "cooldown_bars": 8,
    "fee_bps_per_fill": 4.0,
    "slippage_bps_per_fill": 2.0,
}


def _make_df(n: int = 2000) -> pd.DataFrame:
    rng = np.random.default_rng(9)
    dt = pd.date_range("2023-01-01", periods=n, freq="4h")
    returns = rng.normal(0.0, 0.0015, size=n)
    close = 30000.0 * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0005, 0.0040, size=n))
    low = close * (1 - rng.uniform(0.0005, 0.0040, size=n))
    open_ = close * (1 + rng.normal(0.0, 0.0008, size=n))
    volume = rng.lognormal(0.0, 0.5, size=n) * 100.0
    premium = np.zeros(n)
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "premium": premium,
    }, index=dt)


def test_build_signals_columns():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    assert set(out.columns) >= {"signal", "regime_ok", "vpvr_poc", "atr", "premium", "poc_distance_atr"}


def test_signal_values_valid():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    assert set(out["signal"].unique()).issubset({-1, 0, 1})


def test_high_premium_suppresses_signals():
    df = _make_df(2000)
    df.loc[df.index[-200:-50], "premium"] = 0.0050  # above 15 bps threshold
    out = build_signals(df, PARAMS)
    assert not out["regime_ok"].iloc[-200:-50].any()
    assert (out["signal"].iloc[-200:-50] == 0).all()


def test_low_premium_allows_signals():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    # With zero premium regime_ok is always True; after warmup some signal should fire.
    warmup = max(PARAMS["vpvr_window_bars"], PARAMS["atr_period"])
    assert out["signal"].iloc[warmup:].abs().sum() > 0


def test_warmup_signals_zero():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    warmup = max(PARAMS["vpvr_window_bars"], PARAMS["atr_period"])
    assert (out["signal"].iloc[:warmup] == 0).all()
