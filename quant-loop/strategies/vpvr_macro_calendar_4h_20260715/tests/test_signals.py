"""Unit tests for vpvr_macro_calendar_4h_20260715 build_signals."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_signals import build_signals
from macro_calendar import high_impact_event_dates, is_high_impact_event_date


PARAMS = {
    "vpvr_window_bars": 180,
    "vpvr_bins": 24,
    "atr_period": 14,
    "macro_buffer_bars": 2,
    "post_event_window_bars": 6,
    "post_event_atr_min_mult": 1.2,
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
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dt)


def test_build_signals_columns():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    expected = {"signal", "regime_ok", "vpvr_poc", "atr", "atr_ma", "poc_distance_atr", "macro_proximity_bars"}
    assert set(out.columns) >= expected


def test_signal_values_valid():
    df = _make_df(2000)
    out = build_signals(df, PARAMS)
    assert set(out["signal"].unique()).issubset({-1, 0, 1})


def test_macro_calendar_known_dates():
    # FOMC Mar 16 2022 is a well-known decision day.
    assert is_high_impact_event_date(pd.Timestamp("2022-03-16"))
    # Random mid-week should not be an event.
    assert not is_high_impact_event_date(pd.Timestamp("2022-03-17"))
    # Coverage spans 2022 .. 2026.
    dates = high_impact_event_dates()
    assert min(dates) >= pd.Timestamp("2022-01-01")
    assert max(dates) <= pd.Timestamp("2026-12-31")


def test_event_window_suppresses_signals():
    df = _make_df(2000)
    # Pick an event that lands inside our sample window.
    event_ts = pd.Timestamp("2023-03-22")  # FOMC Mar 22 2023
    # Force every bar in [-buffer_bars, +buffer_bars] around event_ts to be inside the sample.
    pos = df.index.get_indexer([event_ts], method="nearest")[0]
    assert abs(df.index[pos] - event_ts) <= pd.Timedelta(hours=12)

    out = build_signals(df, PARAMS)
    # regime_ok must be False for bars within +/- macro_buffer_bars of the event.
    for j in range(pos - PARAMS["macro_buffer_bars"], pos + PARAMS["macro_buffer_bars"] + 1):
        if 0 <= j < len(df):
            assert not bool(out["regime_ok"].iloc[j]), f"regime_ok unexpectedly True at j={j} ts={df.index[j]}"


def test_run_backtest_produces_trades():
    """Smoke test that run_backtest emits at least one trade on the 4h BTCUSDT data."""
    from strategy import run_backtest
    from data_loader import load_btcusdt_4h

    cfg = {
        "variant": "A",
        "strategy_key": "vpvr_macro_calendar_4h_20260715",
        "iteration": 75,
        "instruments": ["BTCUSDT"],
        "starting_capital_usd": 100000.0,
        "params": PARAMS,
    }
    df = load_btcusdt_4h()
    result = run_backtest(df, cfg)
    assert "trades" in result
    assert "equity" in result
    # len(trades) >= 4 is the evidence-gate threshold for variant detection.
    assert len(result["trades"]) >= 4, f"only {len(result['trades'])} trades"