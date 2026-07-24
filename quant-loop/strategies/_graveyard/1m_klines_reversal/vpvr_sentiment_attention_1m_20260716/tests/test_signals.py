"""Unit tests for vpvr_sentiment_attention_1m_20260716 build_signals."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_signals import build_signals


PARAMS = {
    "vpvr_window_bars": 1440,
    "vpvr_bins": 24,
    "atr_period": 14,
    "attention_z_lookback_bars": 360,
    "attention_z_threshold": 2.0,
    "poc_atr_buffer": 0.5,
    "take_profit_atr_k": 1.5,
    "hard_stop_atr_k": 1.0,
    "max_hold_bars": 30,
    "risk_target_pct": 0.005,
    "cooldown_bars": 5,
    "fee_bps_per_fill": 4.0,
    "slippage_bps_per_fill": 1.0,
}


def _make_df(n: int = 5000) -> pd.DataFrame:
    rng = np.random.default_rng(8)
    dt = pd.date_range("2023-01-01", periods=n, freq="1min")
    returns = rng.normal(0.0, 0.00008, size=n)
    attention = rng.lognormal(0.0, 0.3, size=n)
    close = 30000.0 * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0001, 0.0008, size=n))
    low = close * (1 - rng.uniform(0.0001, 0.0008, size=n))
    open_ = close * (1 + rng.normal(0.0, 0.00015, size=n))
    volume = rng.lognormal(0.0, 0.5, size=n) * 20.0
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "attention": attention,
    }, index=dt)


def test_build_signals_columns():
    df = _make_df(5000)
    out = build_signals(df, PARAMS)
    assert set(out.columns) >= {"signal", "vpvr_poc", "atr", "attention_z", "poc_distance_atr"}


def test_signal_values_valid():
    df = _make_df(5000)
    out = build_signals(df, PARAMS)
    assert set(out["signal"].unique()).issubset({-1, 0, 1})


def test_attention_spike_below_poc_generates_signal():
    # Controlled scenario: price oscillates tightly around 30k so POC ≈ 30k,
    # then attention spikes while price is near POC → signal should fire.
    n = 5000
    rng = np.random.default_rng(8)
    dt = pd.date_range("2023-01-01", periods=n, freq="1min")
    close = 30000.0 + rng.normal(0.0, 3.0, size=n)
    close[-200:-50] += rng.choice([-1, 1]) * 1.5  # still within ATR of POC
    high = close + rng.uniform(1.0, 5.0, size=n)
    low = close - rng.uniform(1.0, 5.0, size=n)
    open_ = close + rng.normal(0.0, 1.0, size=n)
    volume = rng.uniform(50.0, 150.0, size=n)
    attention = rng.lognormal(0.0, 0.1, size=n)
    df = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "attention": attention,
    }, index=dt)
    df.loc[df.index[-120:-60], "attention"] *= 15.0
    out = build_signals(df, PARAMS)
    assert out["signal"].iloc[-130:-50].abs().sum() > 0


def test_warmup_signals_zero():
    df = _make_df(5000)
    out = build_signals(df, PARAMS)
    warmup = max(PARAMS["vpvr_window_bars"], PARAMS["attention_z_lookback_bars"], PARAMS["atr_period"])
    assert (out["signal"].iloc[:warmup] == 0).all()
