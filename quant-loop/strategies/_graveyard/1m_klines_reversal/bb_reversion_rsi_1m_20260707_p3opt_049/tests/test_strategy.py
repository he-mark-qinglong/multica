"""Unit tests for the BB + RSI 1m reversion strategy.

These tests use a tiny deterministic OHLCV fixture, not mocks of the
strategy. The fixture is constructed so the signals are predictable:

    * bars 0..29      — quiet, low-vol, ranging  → indicators seed, no entries
    * bars 30..59     — sharp drop below the lower band with RSI < oversold
                        → at least one long entry fires
    * bars 60..89     — slow drift back toward the middle band
                        → exit triggers as price reverts

The point of the tests is the *logic*, not the magnitude of returns.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Make the strategy dir importable when running ``pytest`` from any cwd.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    annotate,
    bb_bands,
    baseline_hold,
    run_backtest,
    true_range,
    wilder_atr,
    wilder_rsi,
)

CFG_PATH = ROOT / "config.json"


def _cfg() -> dict:
    return json.loads(CFG_PATH.read_text())


def _build_fixture(n: int = 200, seed: int = 7) -> pd.DataFrame:
    """Deterministic 1m OHLCV.

    Phase 1 (first 30 bars) is flat around 100 with tiny noise so all
    indicators have time to seed (BB(20), RSI(14), ATR(14), vol_ma(30)).
    Phase 2 (bars 30..59) drops sharply so close < bb_lower and rsi <
    rsi_oversold → long_entry fires.
    Phase 3 (bars 60..89) drifts back up toward the prior band level so the
    exit rules trigger.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="1min", tz="UTC")
    close = np.empty(n)
    # Phase boundaries; capped to n so small fixtures still build.
    p1 = min(30, n)
    p2 = min(60, n)
    p3 = min(120, n)
    close[:p1] = 100.0 + rng.normal(0.0, 0.05, size=p1)
    if p2 > p1:
        n_drop = p2 - p1
        close[p1:p2] = 100.0 - np.cumsum(rng.uniform(0.4, 0.6, size=n_drop)) * 0.5
    if p3 > p2:
        n_rise = p3 - p2
        close[p2:p3] = close[p1] + np.cumsum(rng.uniform(0.05, 0.2, size=n_rise))
    if n > p3:
        close[p3:] = close[p3 - 1] + rng.normal(0.0, 0.2, size=n - p3)

    high = close + rng.uniform(0.01, 0.08, size=n)
    low = close - rng.uniform(0.01, 0.08, size=n)
    open_ = close + rng.normal(0.0, 0.02, size=n)

    volume = np.full(n, 50.0)
    if p2 > p1:
        volume[p1:p2] = 80.0  # participation during the drop

    df = pd.DataFrame(
        {
            "open": open_, "high": high, "low": low,
            "close": close, "volume": volume,
        },
        index=dates,
    )
    df.index.name = "openTime"
    return df


def test_true_range_first_bar_equals_high_minus_low():
    df = _build_fixture(20)
    tr = true_range(df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy())
    assert tr[0] == df["high"].iloc[0] - df["low"].iloc[0]
    assert tr[1] >= tr[0] - 1e-9


def test_wilder_atr_matches_expected_shape():
    df = _build_fixture(120)
    atr = wilder_atr(df, period=14)
    assert atr.isna().iloc[:13].all()
    assert not atr.isna().iloc[14:].any()
    assert (atr.iloc[14:] > 0).all()


def test_wilder_rsi_bounds():
    df = _build_fixture(120)
    rsi = wilder_rsi(df["close"], period=14)
    valid = rsi.dropna()
    assert (valid >= 0.0).all()
    assert (valid <= 100.0).all()
    assert valid.iloc[14:].notna().any()


def test_bb_bands_shape_and_relationship():
    df = _build_fixture(120)
    mid, up, lo = bb_bands(df["close"], period=20, k=2.0)
    assert mid.isna().iloc[:18].all()
    assert not mid.isna().iloc[19:].any()
    assert (up.iloc[19:] >= mid.iloc[19:]).all()
    assert (lo.iloc[19:] <= mid.iloc[19:]).all()


def test_annotate_emits_expected_columns():
    df = _build_fixture(120)
    cfg = _cfg()
    out = annotate(df, cfg)
    expected = {
        "bb_mid", "bb_upper", "bb_lower",
        "rsi", "atr", "vol_ma",
        "long_entry", "short_entry", "entry_signal",
    }
    assert expected.issubset(out.columns)


def test_long_entry_fires_when_close_below_lower_band_and_rsi_oversold():
    df = _build_fixture(120)
    cfg = _cfg()
    out = annotate(df, cfg)
    # Phase 2 (bars 30..59) drops sharply so the long_entry gate fires.
    assert out["long_entry"].iloc[30:60].any(), "expected at least one long entry in the drop phase"


def test_run_backtest_produces_trade_and_equity_curve():
    df = _build_fixture(200)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    result = run_backtest(df, cfg)
    assert result.n_trades >= 1
    assert len(result.trades) >= 1
    assert not result.equity_curve.empty
    assert result.equity_curve.iloc[0] == cfg["starting_capital_usd"]
    assert result.equity_curve.iloc[-1] > 0


def test_baseline_hold_matches_pnl_math():
    df = _build_fixture(40)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    hold = baseline_hold(df, cfg)
    assert hold.n_trades == 1
    expected_pct = df["close"].iloc[-1] / df["close"].iloc[0] - 1.0
    # Cost-adjust tolerance
    assert abs(hold.total_return - expected_pct) < 0.01


def test_run_backtest_records_exit_reason():
    df = _build_fixture(200)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    result = run_backtest(df, cfg)
    reasons = {t.reason for t in result.trades}
    assert any(r != "" for r in reasons)
    # At least one of the strategy's exit reasons must appear.
    assert any(
        r.startswith("rsi_cross_mid")
        or r.startswith("close")
        or r.startswith("stop_loss")
        or r.startswith("take_profit")
        or r.startswith("time_stop")
        or r.startswith("slow_revert")
        or r == "force_close_eod"
        for r in reasons
    )


def test_run_backtest_handles_flat_input():
    """Pure-flat input must not crash and must produce zero trades."""
    dates = pd.date_range("2025-01-01", periods=40, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0,
        },
        index=dates,
    )
    df.index.name = "openTime"
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    result = run_backtest(df, cfg)
    assert result.n_trades == 0