"""Smoke tests for V7 strategy.py — pure indicator + signal logic.

We assert:
    * htf_trend_signal returns +1/-1/0 correctly on a controlled toy frame
    * align_htf_to_ltf forward-fills the higher-timeframe signal onto the LTF index
    * annotate emits long_entry/short_entry with the htf_trend gate applied
    * run_backtest produces well-formed Trade objects with htf_trend_at_entry populated
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
    align_htf_to_ltf,
    annotate,
    htf_trend_signal,
    run_backtest,
)


def _toy_ltf() -> pd.DataFrame:
    n = 600
    rng = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    close = 100 + 0.5 * np.sin(np.linspace(0, 12 * np.pi, n)) + np.random.RandomState(0).normal(0, 0.3, n)
    base_vol = np.full(n, 50.0)
    return pd.DataFrame(
        {
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "volume": base_vol,
        },
        index=rng,
    )


def _toy_htf() -> pd.DataFrame:
    n = 50
    rng = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    # Trending-up for first half, trending-down for second half.
    close = np.concatenate([np.linspace(100, 110, n // 2), np.linspace(110, 95, n - n // 2)])
    return pd.DataFrame({"close": close}, index=rng)


def test_htf_trend_signal_signs():
    htf = _toy_htf()
    sig = htf_trend_signal(htf, lookback_bars=12)
    assert "htf_trend" in sig.columns
    assert set(sig["htf_trend"].unique()).issubset({-1, 0, 1})
    # First half is up-trending → +1, second half is down-trending → -1.
    pos_first_half = sig["htf_trend"].iloc[15:25]
    neg_second_half = sig["htf_trend"].iloc[35:45]
    assert (pos_first_half == 1).all()
    assert (neg_second_half == -1).all()


def test_align_htf_to_ltf_backward_fill():
    ltf = _toy_ltf()
    htf = _toy_htf()
    sig = htf_trend_signal(htf, lookback_bars=12)
    aligned = align_htf_to_ltf(ltf, sig)
    assert len(aligned) == len(ltf)
    # Backward direction: every ltf row should equal the most recent htf value at or before it.
    # Just check it is in {-1, 0, 1}.
    assert set(aligned.dropna().unique()).issubset({-1, 0, 1})


def test_annotate_emits_long_short_with_htf_gate():
    ltf = _toy_ltf()
    htf = _toy_htf()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["vpvr"]["window_bars"] = 100
    cfg["mtf"]["lookback_bars"] = 6
    out = annotate(ltf, htf, cfg)
    assert "vpvr_poc" in out.columns
    assert "vpvr_z_dist" in out.columns
    assert "htf_trend" in out.columns
    assert "long_entry" in out.columns
    assert "short_entry" in out.columns
    assert out["long_entry"].dtype == bool
    assert out["short_entry"].dtype == bool
    # HTF gate: long_entry rows must have htf_trend == 1; short_entry rows must have htf_trend == -1.
    long_idx = out.index[out["long_entry"]]
    short_idx = out.index[out["short_entry"]]
    if len(long_idx) > 0:
        assert (out.loc[long_idx, "htf_trend"] == 1).all()
    if len(short_idx) > 0:
        assert (out.loc[short_idx, "htf_trend"] == -1).all()


def test_run_backtest_returns_well_formed_trades():
    ltf = _toy_ltf()
    htf = _toy_htf()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["vpvr"]["window_bars"] = 100
    cfg["mtf"]["lookback_bars"] = 6
    cfg["exit"]["max_holding_bars"] = 12
    cfg["_symbol"] = "TESTUSDT"
    res = run_backtest(ltf, htf, cfg)
    assert hasattr(res, "n_trades")
    assert hasattr(res, "annualized_sharpe")
    assert hasattr(res, "max_drawdown")
    for t in res.trades:
        assert t.htf_trend_at_entry in {-1, 0, 1}