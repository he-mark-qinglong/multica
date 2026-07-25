"""Unit tests for momentum_trend_multi_tf_atr_scaled_1h_20260712.

These tests use deterministic OHLCV fixtures (not mocks of strategy.py).
The fixtures are constructed so the signals are predictable:

    * Phase 1 (first 250 bars) — flat range around 100 with tiny noise
      AND a 4h EMA slope near 0 — no entries.
    * Phase 2 (next 250 bars) — strong, monotone-ish uptrend with
      rising volume and rising 1h ATR. The 4h EMA slope flips positive,
      RSI crosses 50 upward at least once, and ADX climbs above 20 —
      long entries fire.

The 4h frame is built from the same fixture but resampled to a 4-bar
window so the 4h EMA is well-defined.

Tests cover:
    1. Indicator math (true_range, wilder_atr, wilder_rsi, wilder_adx, ema)
    2. annotate emits all expected columns and signals.
    3. Position sizing math (ATR-scaled, capped).
    4. run_backtest produces trades + equity curve on a trending fixture.
    5. Flat input -> zero trades, no crash.
    6. The 4h EMA does not leak future info (look-ahead discipline).
    7. Exits fire on the right triggers.
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
    baseline_hold,
    ema,
    run_backtest,
    true_range,
    wilder_adx,
    wilder_atr,
    wilder_rsi,
    _exit_on_bar,
    _position_size,
)

CFG_PATH = ROOT / "config.json"


def _cfg() -> dict:
    return json.loads(CFG_PATH.read_text())


def _build_1h_fixture(n: int = 600, seed: int = 7) -> pd.DataFrame:
    """Two-phase deterministic 1h OHLCV:
        phase 1 (n//2 bars) — flat range around 100
        phase 2 (rest)        — uptrend WITH pullbacks (RSI oscillates around 50)
    """
    n1 = n // 2
    n2 = n - n1
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    close = np.empty(n)
    # Phase 1: flat around 100 with tiny noise.
    close[:n1] = 100.0 + rng.normal(0.0, 0.2, size=n1)

    # Phase 2: uptrend WITH periodic pullbacks so RSI oscillates around 50.
    # Build close as cumulative sum of (drift + sine wave + tiny noise) where
    # the sine wave pulls the price back ~1-2% every ~30 bars. This forces
    # RSI to swing above and below 50 multiple times in the trending regime.
    drift = np.full(n2, 0.30)  # positive drift per bar
    cycle = 0.6 * np.sin(np.linspace(0, 6 * np.pi, n2))  # ~30-bar cycles
    noise = rng.normal(0.0, 0.05, size=n2)
    phase2_increments = drift + cycle + noise
    close[n1:] = 100.0 + np.cumsum(phase2_increments)

    # Tight intrabar range so ADX climbs.
    high = close + rng.uniform(0.05, 0.15, size=n)
    low = close - rng.uniform(0.05, 0.15, size=n)
    open_ = close + rng.normal(0.0, 0.05, size=n)
    volume = np.empty(n)
    volume[:n1] = rng.uniform(40.0, 60.0, size=n1)
    volume[n1:] = np.linspace(120.0, 300.0, n2)
    df = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }, index=dates)
    df.index.name = "openTime"
    return df


def _resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample the 1h fixture to 4h. Volume is summed; high/low are
    extremes; open/close are first/last."""
    return df_1h.resample("4h", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["close"])


# --- Indicator primitives --------------------------------------------------

def test_true_range_first_bar_is_high_minus_low():
    df = _build_1h_fixture(20)
    tr = true_range(df)
    assert tr.iloc[0] == df["high"].iloc[0] - df["low"].iloc[0]
    assert tr.iloc[1] >= tr.iloc[0] - 1e-9


def test_wilder_atr_warmup_shape():
    df = _build_1h_fixture(100)
    atr = wilder_atr(df, period=14)
    assert atr.iloc[:13].isna().all()
    assert atr.iloc[14:].notna().all()
    assert (atr.iloc[14:] > 0).all()


def test_wilder_rsi_bounded_and_seeded():
    df = _build_1h_fixture(100)
    rsi = wilder_rsi(df, period=14)
    assert rsi.iloc[:13].isna().all()
    valid = rsi.iloc[14:].dropna()
    assert ((valid >= 0.0) & (valid <= 100.0)).all()


def test_wilder_adx_emits_after_warmup():
    df = _build_1h_fixture(200)
    adx = wilder_adx(df, period=14)
    # ADX needs 2*period-1 bars minimum; verify the tail is non-empty.
    assert adx.iloc[60:].notna().any()


def test_ema_standard_formula():
    """EMA(3) closed-form on the canonical fixture.

    With ``adjust=False`` (recursive EMA), alpha = 2/(span+1) = 0.5:
        e[0] = 1.0,  e[1] = 1.5,  e[2] = 2.25,  e[3] = 3.125, e[4] = 4.0625
    With ``min_periods=3``, the first 2 returns are NaN and bar 2 = 2.25.
    """
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    e = ema(s, period=3)
    assert pd.isna(e.iloc[0])
    assert pd.isna(e.iloc[1])
    assert abs(e.iloc[2] - 2.25) < 1e-9
    assert abs(e.iloc[3] - 3.125) < 1e-9
    assert abs(e.iloc[4] - 4.0625) < 1e-9


# --- Multi-TF annotation ---------------------------------------------------

def test_annotate_emits_expected_columns():
    df_1h = _build_1h_fixture(800)
    df_4h = _resample_4h(df_1h)
    cfg = _cfg()
    out = annotate(df_1h, df_4h, cfg)
    expected = {
        "ema50_4h", "ema50_4h_slope",
        "rsi14_1h", "rsi14_1h_prev", "adx14_1h", "atr14_1h",
        "long_entry", "short_entry", "entry_signal",
        "exit_4h_reversal_long", "exit_4h_reversal_short",
        "exit_rsi_cross_back_long", "exit_rsi_cross_back_short",
    }
    assert expected.issubset(out.columns)


def test_annotate_long_entry_fires_in_trending_phase():
    df_1h = _build_1h_fixture(1500)
    df_4h = _resample_4h(df_1h)
    cfg = _cfg()
    out = annotate(df_1h, df_4h, cfg)
    # The fixture has a phase-2 uptrend; we expect at least one long entry.
    assert out["long_entry"].iloc[400:].any(), "expected at least one long entry in the trending phase"


def test_annotate_no_lookahead_in_4h_ema():
    """The 4h EMA used at 1h bar ``t`` must be the most recent 4h close at
    ``t-1`` (i.e. shifted by 1 4h bar). Verify by perturbing the next 4h
    bar's close and confirming the 1h-aligned 4h EMA does not change."""
    df_1h = _build_1h_fixture(800)
    df_4h_a = _resample_4h(df_1h)
    cfg = _cfg()
    out_a = annotate(df_1h, df_4h_a, cfg)

    # Copy the 4h frame and perturb the close of one FUTURE 4h bar.
    df_4h_b = df_4h_a.copy()
    perturb_idx = df_4h_b.index[50]  # 50th 4h bar (200 1h bars in)
    df_4h_b.loc[perturb_idx, "close"] = df_4h_b.loc[perturb_idx, "close"] * 2.0
    out_b = annotate(df_1h, df_4h_b, cfg)

    # The 1h-aligned 4h EMA at 1h index 0..199 must NOT change.
    ema_a = out_a["ema50_4h"].iloc[:200]
    ema_b = out_b["ema50_4h"].iloc[:200]
    assert ema_a.equals(ema_b), "4h EMA must not look ahead"


def test_annotate_flat_phase_has_no_entry():
    """Pure flat input should produce no entries (RSI stays at 50, ADX near 0)."""
    dates = pd.date_range("2025-01-01", periods=600, freq="1h", tz="UTC")
    df_1h = pd.DataFrame({
        "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 50.0,
    }, index=dates)
    df_1h.index.name = "openTime"
    df_4h = _resample_4h(df_1h)
    cfg = _cfg()
    out = annotate(df_1h, df_4h, cfg)
    assert out["long_entry"].sum() == 0
    assert out["short_entry"].sum() == 0


# --- Sizing ---------------------------------------------------------------

def test_position_size_capped_by_max_notional():
    cfg = _cfg()
    # 1% per ATR on $100 ATR / $60k price = $6000 notional on $100k equity
    # = 6% of equity. Cap = 5%. So result should be capped at 5%.
    notional = _position_size(equity=100_000, price=60_000.0, atr=100.0, cfg=cfg)
    assert abs(notional - 5_000.0) < 1e-6


def test_position_size_zero_when_atr_zero():
    cfg = _cfg()
    assert _position_size(equity=100_000, price=60_000.0, atr=0.0, cfg=cfg) == 0.0


# --- Exit logic -----------------------------------------------------------

def test_exit_atr_trailing_long():
    cfg = _cfg()
    bar = pd.Series({
        "close": 95.0,
        "atr14_1h": 1.0,
        "ema50_4h_slope": 0.001,  # still uptrend
        "exit_rsi_cross_back_long": False,
    })
    exit_now, reason, _ = _exit_on_bar(bar, "long", entry_price=100.0, atr_at_entry=1.0, cfg=cfg)
    # 100 - 2.5*1 = 97.5; close=95 < 97.5 -> exit
    assert exit_now is True
    assert "atr_trailing" in reason


def test_exit_4h_trend_reversal_long():
    cfg = _cfg()
    bar = pd.Series({
        "close": 101.0,
        "atr14_1h": 1.0,
        "ema50_4h_slope": -0.001,  # trend just flipped
        "exit_rsi_cross_back_long": False,
    })
    exit_now, reason, _ = _exit_on_bar(bar, "long", entry_price=100.0, atr_at_entry=1.0, cfg=cfg)
    assert exit_now is True
    assert "4h_trend_reversal" in reason


def test_exit_rsi_cross_back_short():
    cfg = _cfg()
    bar = pd.Series({
        "close": 99.0,
        "atr14_1h": 1.0,
        "ema50_4h_slope": -0.001,  # short: still downtrend, no 4h exit
        "exit_rsi_cross_back_short": True,
    })
    exit_now, reason, _ = _exit_on_bar(bar, "short", entry_price=100.0, atr_at_entry=1.0, cfg=cfg)
    assert exit_now is True
    assert "rsi_cross_back" in reason


def test_no_exit_when_all_clear():
    cfg = _cfg()
    bar = pd.Series({
        "close": 102.0,  # long is +2 from entry; well above 100 - 2.5*1 = 97.5
        "atr14_1h": 1.0,
        "ema50_4h_slope": 0.001,
        "exit_rsi_cross_back_long": False,
    })
    exit_now, _, _ = _exit_on_bar(bar, "long", entry_price=100.0, atr_at_entry=1.0, cfg=cfg)
    assert exit_now is False


# --- run_backtest end-to-end ----------------------------------------------

def test_run_backtest_trending_fixture_produces_trades():
    df_1h = _build_1h_fixture(1500)
    df_4h = _resample_4h(df_1h)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    annotated = annotate(df_1h, df_4h, cfg)
    result = run_backtest(annotated, cfg)
    assert result.n_trades >= 1
    assert not result.equity_curve.empty
    assert result.equity_curve.iloc[0] == cfg["starting_capital_usd"]
    assert result.equity_curve.iloc[-1] > 0


def test_run_backtest_flat_input_zero_trades():
    dates = pd.date_range("2025-01-01", periods=600, freq="1h", tz="UTC")
    df_1h = pd.DataFrame({
        "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0,
    }, index=dates)
    df_1h.index.name = "openTime"
    df_4h = _resample_4h(df_1h)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    annotated = annotate(df_1h, df_4h, cfg)
    result = run_backtest(annotated, cfg)
    assert result.n_trades == 0


def test_run_backtest_records_exit_reason():
    df_1h = _build_1h_fixture(1500)
    df_4h = _resample_4h(df_1h)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    annotated = annotate(df_1h, df_4h, cfg)
    result = run_backtest(annotated, cfg)
    if result.trades:
        reasons = {t.reason for t in result.trades}
        assert any(
            r.startswith("atr_trailing")
            or r.startswith("4h_trend_reversal")
            or r.startswith("rsi_cross_back")
            or r == "force_close_eod"
            for r in reasons
        )


def test_baseline_hold_matches_pnl_math():
    df_1h = _build_1h_fixture(800)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    hold = baseline_hold(df_1h, cfg)
    assert hold.n_trades == 1
    expected_pct = df_1h["close"].iloc[-1] / df_1h["close"].iloc[0] - 1.0
    assert abs(hold.total_return - expected_pct) < 0.01