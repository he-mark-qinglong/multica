"""Unit tests for vpvr_tod_session_filter_15m_20260715 build_signals + strategy."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_signals import build_signals
from tod_calendar import (
    SESSION_WINDOWS,
    session_for_timestamp,
    session_label,
    is_session_active,
)


PARAMS = {
    "vpvr_window_bars": 96,
    "vpvr_bins": 24,
    "atr_period": 14,
    "poc_atr_buffer": 0.6,
    "session_filter_names": ["london", "us"],
    "take_profit_atr_k": 1.5,
    "hard_stop_atr_k": 1.0,
    "max_hold_bars": 32,
    "risk_target_pct": 0.005,
    "cooldown_bars": 8,
    "fee_bps_per_fill": 4.0,
    "slippage_bps_per_fill": 2.0,
}


def _make_df(n: int = 1500) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    # 15-minute bars anchored to UTC; spread across multiple days/sessions.
    dt = pd.date_range("2025-01-01", periods=n, freq="15min")
    returns = rng.normal(0.0, 0.001, size=n)
    close = 30000.0 * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0002, 0.0020, size=n))
    low = close * (1 - rng.uniform(0.0002, 0.0020, size=n))
    open_ = close * (1 + rng.normal(0.0, 0.0006, size=n))
    volume = rng.lognormal(0.0, 0.5, size=n) * 100.0
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dt)


def test_tod_calendar_session_assignment():
    assert session_for_timestamp(pd.Timestamp("2025-01-01T00:30:00")) == "asia"
    assert session_for_timestamp(pd.Timestamp("2025-01-01T03:00:00")) == "asia"
    assert session_for_timestamp(pd.Timestamp("2025-01-01T08:00:00")) == "london"
    assert session_for_timestamp(pd.Timestamp("2025-01-01T11:30:00")) == "london"
    assert session_for_timestamp(pd.Timestamp("2025-01-01T16:00:00")) == "us"
    assert session_for_timestamp(pd.Timestamp("2025-01-01T20:30:00")) == "us"


def test_session_windows_complete_24h():
    covered = sum((e - s) for (s, e) in SESSION_WINDOWS.values())
    assert covered == 24


def test_build_signals_columns():
    df = _make_df(1500)
    out = build_signals(df, PARAMS)
    expected = {"signal", "session", "in_session", "vpvr_poc", "atr", "poc_distance_atr"}
    assert set(out.columns) >= expected


def test_signal_values_valid():
    df = _make_df(1500)
    out = build_signals(df, PARAMS)
    assert set(out["signal"].unique()).issubset({-1, 0, 1})


def test_session_filter_suppresses_asia_signals():
    df = _make_df(1500)
    out = build_signals(df, PARAMS)
    asia_bars = out["session"] == "asia"
    non_filtered_signal = out["signal"][asia_bars]
    # Where session==asia, in_session==False → signal must be 0.
    assert (non_filtered_signal == 0).all()


def test_run_backtest_produces_trades():
    """Smoke test that run_backtest emits trades on the 15m BTCUSDT data."""
    from strategy import run_backtest
    from data_loader import load_15m

    cfg = {
        "variant": "A",
        "strategy_key": "vpvr_tod_session_filter_15m_20260715",
        "iteration": 1,
        "instruments": ["BTCUSDT"],
        "starting_capital_usd": 100000.0,
        "params": PARAMS,
    }
    df = load_15m("BTCUSDT")
    result = run_backtest(df, cfg)
    assert "trades" in result
    assert "equity" in result
    # len(trades) >= 4 is the evidence-gate threshold for variant detection.
    assert len(result["trades"]) >= 4, f"only {len(result['trades'])} trades"


def test_is_session_active_default_names():
    # Default tuple is (asia, london, us) — covers all hours of the day.
    ts = pd.Timestamp("2025-01-01T13:00:00")
    assert is_session_active(ts)
    ts = pd.Timestamp("2025-01-01T02:00:00")
    assert is_session_active(ts)
