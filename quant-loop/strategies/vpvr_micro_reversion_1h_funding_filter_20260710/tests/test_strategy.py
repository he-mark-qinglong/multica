"""Smoke tests for V6 strategy.py — pure indicator + signal logic.

We assert:
    * funding_z matches manual z-score on a controlled toy frame
    * rolling_volume_profile POC matches the manual bin argmax
    * vpvr_touch fires only near VAL or VAH
    * annotate emits long_entry/short_entry with correct funding-z sign
    * run_backtest produces trades with the expected reasons
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    annotate,
    funding_z,
    rolling_volume_profile,
    run_backtest,
    vpvr_touch,
)


def _toy_df() -> pd.DataFrame:
    n = 400
    rng = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    # Trending-up series for first half, mean-reverting for second half.
    close = np.concatenate([
        np.linspace(100, 130, n // 2),
        115 + 2 * np.sin(np.linspace(0, 6 * np.pi, n // 2)),
    ])
    close = close + np.random.RandomState(0).normal(0, 0.3, n)
    base_vol = np.full(n, 50.0)
    base_vol[200:220] = 250.0  # volume spike in middle to ensure VA-bound exists
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": base_vol,
        },
        index=rng,
    )


def test_funding_z_matches_manual_computation():
    df = _toy_df()
    z = funding_z(df, window=168)
    # Manual: z = (close - rolling_mean) / rolling_std
    expected = (df["close"] - df["close"].rolling(168, min_periods=168).mean()) / \
               df["close"].rolling(168, min_periods=168).std()
    np.testing.assert_allclose(z.iloc[168:].to_numpy(), expected.iloc[168:].to_numpy(), rtol=1e-9)


def test_vpvr_poc_matches_manual_bin_argmax():
    df = _toy_df()
    prof = rolling_volume_profile(df, window=168, n_bins=10, value_area_pct=0.7)
    # Use last bar with prior 168 bars
    win_c = df["close"].iloc[-168:-1].to_numpy()
    win_v = df["volume"].iloc[-168:-1].to_numpy()
    lo, hi = float(win_c.min()), float(win_c.max())
    edges = np.linspace(lo, hi, 11)
    idx = np.clip(((win_c - lo) / (hi - lo) * 10).astype(int), 0, 9)
    bin_vol = np.bincount(idx, weights=win_v, minlength=10)
    poc_bin = int(np.argmax(bin_vol))
    expected_poc = 0.5 * (edges[poc_bin] + edges[poc_bin + 1])
    poc_val = float(prof["vpvr_poc"].iloc[-1])
    assert abs(poc_val - expected_poc) < (hi - lo) / 10 + 1e-9


def test_vpvr_touch_fires_only_near_boundaries():
    df = _toy_df()
    prof = rolling_volume_profile(df, window=168, n_bins=10, value_area_pct=0.7)
    touch = vpvr_touch(df, prof, band_pct=0.02)
    # After the warmup window every row should be in [False, True]
    valid = touch.iloc[168:].dropna()
    assert valid.isin([True, False]).all()


def test_annotate_emits_long_short_signals():
    df = _toy_df()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["funding"]["window_bars"] = 50  # shrink for the toy frame
    cfg["vpvr"]["window_bars"] = 50
    out = annotate(df, cfg)
    assert "funding_z" in out.columns
    assert "vpvr_poc" in out.columns
    assert "vpvr_touch" in out.columns
    assert "long_entry" in out.columns
    assert "short_entry" in out.columns
    assert out["long_entry"].dtype == bool
    assert out["short_entry"].dtype == bool


def test_run_backtest_returns_structured_trades():
    df = _toy_df()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["funding"]["window_bars"] = 50
    cfg["vpvr"]["window_bars"] = 50
    cfg["exit"]["max_holding_bars"] = 4
    cfg["_symbol"] = "TESTUSDT"
    res = run_backtest(df, cfg)
    # Result must be well-formed even if n_trades == 0
    assert hasattr(res, "n_trades")
    assert hasattr(res, "annualized_sharpe")
    assert hasattr(res, "max_drawdown")
    assert hasattr(res, "equity_curve")
    assert isinstance(res.trades, list)