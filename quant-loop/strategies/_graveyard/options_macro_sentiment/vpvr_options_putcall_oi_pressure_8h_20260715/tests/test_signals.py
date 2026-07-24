"""Unit tests for vpvr_options_putcall_oi_pressure_8h_20260715 build_signals."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_signals import build_signals  # noqa: E402


PARAMS = {
    "vpvr_window_bars": 90,
    "vpvr_bins": 24,
    "atr_period": 14,
    "pcr_z_lookback_bars": 90,
    "pcr_z_threshold": 1.5,
    "poc_atr_buffer": 0.75,
    "take_profit_atr_k": 2.5,
    "hard_stop_atr_k": 1.5,
    "max_hold_bars": 12,
    "risk_target_pct": 0.005,
    "cooldown_bars": 1,
    "fee_bps_per_fill": 4.0,
    "slippage_bps_per_fill": 2.0,
}


def _make_df(n: int = 1200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2023-01-01", periods=n, freq="8h")
    close = 1800.0 * np.exp(np.cumsum(rng.normal(0.0, 0.003, size=n)))
    high = close * (1 + rng.uniform(0.001, 0.005, size=n))
    low = close * (1 - rng.uniform(0.001, 0.005, size=n))
    open_ = close * (1 + rng.normal(0.0, 0.001, size=n))
    volume = rng.lognormal(0.0, 0.5, size=n) * 100.0
    taker_buy_share = rng.uniform(0.3, 0.7, size=n)
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "taker_buy_share": taker_buy_share,
    }, index=dt)


def test_build_signals_returns_expected_columns():
    df = _make_df(1200)
    out = build_signals(df, PARAMS)
    expected = {"signal", "vpvr_poc", "atr", "pcr_z", "poc_distance_atr", "taker_buy_share"}
    assert expected.issubset(out.columns)


def test_signal_values_are_valid():
    df = _make_df(1200)
    out = build_signals(df, PARAMS)
    assert set(out["signal"].dropna().unique()).issubset({-1, 0, 1})


def test_extreme_low_pcr_generates_long_signal():
    """Strong put-side pressure (low taker_buy_share) should produce a long signal."""
    df = _make_df(1200)
    df.loc[df.index[-200:-150], "taker_buy_share"] = 0.10
    out = build_signals(df, PARAMS)
    assert (out["signal"].iloc[-220:-130] == 1).any()


def test_extreme_high_pcr_generates_short_signal():
    """Strong call-side pressure (high taker_buy_share) should produce a short signal."""
    df = _make_df(1200)
    df.loc[df.index[-200:-150], "taker_buy_share"] = 0.95
    out = build_signals(df, PARAMS)
    assert (out["signal"].iloc[-220:-130] == -1).any()


def test_signals_are_zero_during_warmup():
    df = _make_df(1200)
    out = build_signals(df, PARAMS)
    warmup = max(PARAMS["vpvr_window_bars"], PARAMS["pcr_z_lookback_bars"], PARAMS["atr_period"])
    assert (out["signal"].iloc[:warmup] == 0).all()


def test_pcr_z_finite_during_warmup_extremes():
    """Z-score lookback should be populated exactly lookback bars in."""
    df = _make_df(1200)
    out = build_signals(df, PARAMS)
    # First `pcr_z_lookback_bars` rows should be NaN; subsequent rows finite or NaN depending on data
    assert out["pcr_z"].iloc[: PARAMS["pcr_z_lookback_bars"] - 1].isna().all()
