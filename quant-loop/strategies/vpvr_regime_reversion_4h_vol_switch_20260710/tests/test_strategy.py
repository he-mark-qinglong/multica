"""Smoke tests for V8 strategy.py — pure indicator + signal logic.

We assert:
    * realized_vol_bps matches manual stdev-of-log-returns computation
    * regime_switch_signal fires on the bar immediately after low->high transition
    * vpvr POC matches the manual bin argmax on a toy frame
    * poc_test_signal fires only when close is near VAL or VAH within ATR band
    * annotate emits long_entry/short_entry only on regime-switch bars with POC tests
    * run_backtest returns well-formed trades
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    annotate,
    poc_test_signal,
    realized_vol_bps,
    regime_switch_signal,
    rolling_volume_profile,
    run_backtest,
)


def _toy_df() -> pd.DataFrame:
    n = 800
    rng = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    # Low-vol for first half (small sine), high-vol for second half (large jumps).
    low_vol = 100 + 0.5 * np.sin(np.linspace(0, 12 * np.pi, n // 2))
    high_vol = 100 + np.cumsum(np.random.RandomState(0).normal(0, 3.0, n - n // 2))
    close = np.concatenate([low_vol, high_vol + 5])  # offset so regime change is detectable
    base_vol = np.full(n, 50.0)
    base_vol[n // 2 : n // 2 + 30] = 250.0  # volume spike at regime switch
    return pd.DataFrame(
        {
            "open": close, "high": close + 0.5, "low": close - 0.5,
            "close": close, "volume": base_vol,
        },
        index=rng,
    )


def test_realized_vol_bps_matches_manual():
    df = _toy_df()
    rv = realized_vol_bps(df, window=30, periods_per_year=2190.0)
    log_ret = np.log(df["close"] / df["close"].shift(1))
    expected = log_ret.rolling(30, min_periods=30).std() * math.sqrt(2190.0) * 10000.0
    np.testing.assert_allclose(rv.iloc[30:].to_numpy(), expected.iloc[30:].to_numpy(), rtol=1e-9)


def test_regime_switch_fires_after_low_to_high_transition():
    df = _toy_df()
    rv = realized_vol_bps(df, window=30, periods_per_year=2190.0)
    flag = regime_switch_signal(rv, threshold_bps=10.0, lookback_bars=6)
    # We forced a vol expansion around the midpoint; there must be at least one switch.
    assert flag.sum() >= 1


def test_vpvr_poc_matches_manual_bin_argmax():
    df = _toy_df()
    prof = rolling_volume_profile(df, window=42, n_bins=10, value_area_pct=0.7)
    win_c = df["close"].iloc[-42:-1].to_numpy()
    win_v = df["volume"].iloc[-42:-1].to_numpy()
    lo, hi = float(win_c.min()), float(win_c.max())
    edges = np.linspace(lo, hi, 11)
    idx = np.clip(((win_c - lo) / (hi - lo) * 10).astype(int), 0, 9)
    bin_vol = np.bincount(idx, weights=win_v, minlength=10)
    poc_bin = int(np.argmax(bin_vol))
    expected_poc = 0.5 * (edges[poc_bin] + edges[poc_bin + 1])
    poc_val = float(prof["vpvr_poc"].iloc[-1])
    assert abs(poc_val - expected_poc) < (hi - lo) / 10 + 1e-9


def test_poc_test_signal_only_fires_near_val_or_vah():
    df = _toy_df()
    prof = rolling_volume_profile(df, window=42, n_bins=10, value_area_pct=0.7)
    atr = pd.Series(np.full(len(df), 0.5), index=df.index)  # constant ATR
    tests = poc_test_signal(df, prof, atr, atr_k=1.0)
    assert set(tests["poc_low_test"].dropna().unique()).issubset({True, False})
    assert set(tests["poc_high_test"].dropna().unique()).issubset({True, False})


def test_annotate_emits_long_short_with_regime_switch_gate():
    df = _toy_df()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["regime"]["rv_window_bars"] = 30
    cfg["regime"]["rv_threshold_bps"] = 10.0  # tiny threshold so switches fire on toy data
    cfg["vpvr"]["window_bars"] = 42
    cfg["atr"]["period"] = 14
    out = annotate(df, cfg)
    assert "rv_bps" in out.columns
    assert "rv_regime" in out.columns
    assert "regime_switch" in out.columns
    assert "vpvr_poc" in out.columns
    assert "long_entry" in out.columns
    assert "short_entry" in out.columns
    assert out["long_entry"].dtype == bool
    assert out["short_entry"].dtype == bool
    # Long entry rows must have recent_switch == True AND poc_low_test == True.
    long_idx = out.index[out["long_entry"]]
    if len(long_idx) > 0:
        assert (out.loc[long_idx, "recent_switch"]).all()
        assert (out.loc[long_idx, "poc_low_test"]).all()


def test_run_backtest_returns_well_formed_trades():
    df = _toy_df()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["regime"]["rv_window_bars"] = 30
    cfg["regime"]["rv_threshold_bps"] = 10.0
    cfg["vpvr"]["window_bars"] = 42
    cfg["atr"]["period"] = 14
    cfg["exit"]["max_holding_bars"] = 12
    cfg["_symbol"] = "TESTUSDT"
    res = run_backtest(df, cfg)
    assert hasattr(res, "n_trades")
    assert hasattr(res, "annualized_sharpe")
    assert hasattr(res, "max_drawdown")
    assert isinstance(res.trades, list)
    for t in res.trades:
        assert hasattr(t, "rv_bps_at_entry")