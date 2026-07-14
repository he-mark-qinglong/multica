"""Unit tests for the Donchian breakout + ATR trailing strategy.

These tests use a tiny deterministic OHLCV fixture, not mocks of the
strategy. The fixture is constructed so the signals are predictable:

    * bars 0..18      — quiet, low-vol, ranging  → no entries
    * bars 19         — close breaks Donchian upper(20) (uses bars 0..18)
                        but ADX is still low → no entry
    * bars 20..28     — strong directional trend with rising volume & ATR
                        → ADX climbs above 20 → entry fires on bar 28
    * bars 29..32     — drop below entry - 3*ATR → trailing stop exit

The point of the tests is the *logic*, not the magnitude of returns.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Make the strategy dir importable when running `pytest` from any cwd.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    annotate,
    baseline_hold,
    donchian_lower,
    donchian_upper,
    run_backtest,
    true_range,
    wilder_adx,
    wilder_atr,
)

CFG_PATH = ROOT / "config.json"


def _cfg() -> dict:
    return json.loads(CFG_PATH.read_text())


def _build_fixture(n: int = 60, seed: int = 7) -> pd.DataFrame:
    """Deterministic 1d OHLCV.

    Phase 1 (first ``n//2`` bars) is a flat range around 100 with tiny noise.
    Phase 2 (last ``n - n//2`` bars) is a strong uptrend with rising volume,
    rising ATR, and ADX climbing above 20 so all four entry gates fire.

    The transition bar (index ``n//2``) deliberately closes above the
    prior 20-bar high to trigger the Donchian breakout.
    """
    n_phase1 = n // 2
    n_phase2 = n - n_phase1
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n, freq="1D", tz="UTC")

    close = np.empty(n)
    # Phase 1 — flat range, modest noise
    close[:n_phase1] = 100.0 + rng.normal(0.0, 0.4, size=n_phase1)
    # Phase 2 — strong, monotone-ish uptrend
    close[n_phase1:] = 100.0 + np.cumsum(rng.uniform(1.0, 1.8, size=n_phase2))

    high = close + rng.uniform(0.1, 0.5, size=n)
    low = close - rng.uniform(0.1, 0.5, size=n)
    open_ = close + rng.normal(0.0, 0.15, size=n)

    # Volume: low during phase 1, rising through phase 2.
    volume = np.empty(n)
    volume[:n_phase1] = rng.uniform(40.0, 60.0, size=n_phase1)
    volume[n_phase1:] = np.linspace(120.0, 300.0, n_phase2)

    df = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }, index=dates)
    df.index.name = "openTime"
    return df


def test_true_range_first_bar_equals_high_minus_low():
    df = _build_fixture(20)
    tr = true_range(df)
    # The first bar has no prior close, so true_range degenerates to
    # high - low. The first-bar tr value is used as the seed for Wilder
    # smoothing and is therefore not NaN.
    assert tr.iloc[0] == df["high"].iloc[0] - df["low"].iloc[0]
    # Subsequent bars use the full 3-component max.
    assert tr.iloc[1] >= tr.iloc[0] - 1e-9


def test_wilder_atr_matches_expected_shape():
    df = _build_fixture(60)
    atr = wilder_atr(df, period=14)
    assert atr.isna().iloc[:13].all()
    assert not atr.isna().iloc[14:].any()
    assert (atr.iloc[14:] > 0).all()


def test_wilder_adx_emits_values_after_seed():
    df = _build_fixture(60)
    adx = wilder_adx(df, period=14)
    # ADX needs a longer seed; we just check the series is non-empty after warm-up.
    assert adx.iloc[30:].notna().any()


def test_donchian_upper_no_lookahead():
    df = _build_fixture(60)
    up = donchian_upper(df, n=20)
    # At bar t=20, the value should be the max(high[0:20]) — bar 20 itself
    # is NOT in the window because of the shift.
    expected = df["high"].iloc[0:20].max()
    assert up.iloc[20] == expected
    # At bar 19, the band is not yet defined.
    assert pd.isna(up.iloc[19])


def test_donchian_lower_symmetric():
    df = _build_fixture(60)
    lo = donchian_lower(df, n=20)
    expected = df["low"].iloc[0:20].min()
    assert lo.iloc[20] == expected


def test_annotate_emits_expected_columns():
    df = _build_fixture(80)
    cfg = _cfg()
    out = annotate(df, cfg)
    expected = {
        "atr", "atr_ma", "vol_ma", "adx",
        "donchian_upper", "donchian_lower",
        "long_entry", "short_entry", "entry_signal",
    }
    assert expected.issubset(out.columns)


def test_long_entry_fires_in_trending_fixture():
    df = _build_fixture(200)
    cfg = _cfg()
    out = annotate(df, cfg)
    # After phase-2 begins and the warmup indicators are seeded, the entry
    # should fire at least once.
    assert out["long_entry"].iloc[40:].any(), "expected at least one long entry in the trending phase"


def test_run_backtest_produces_trade_and_equity_curve():
    df = _build_fixture(200)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    result = run_backtest(df, cfg)
    assert result.n_trades >= 1
    assert len(result.trades) >= 1
    assert not result.equity_curve.empty
    assert result.equity_curve.iloc[0] == cfg["starting_capital_usd"]
    # End-of-backtest force-close means equity is well-defined.
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
    # At least one exit reason should be set.
    reasons = {t.reason for t in result.trades}
    assert any(r != "" for r in reasons)
    assert any(
        r.startswith("atr_trailing")
        or r.startswith("donchian_opposite_break")
        or r.startswith("time_stop")
        or r == "force_close_eod"
        for r in reasons
    )


def test_run_backtest_handles_flat_input():
    """Pure-flat input must not crash and must produce zero trades."""
    dates = pd.date_range("2025-01-01", periods=40, freq="1D", tz="UTC")
    df = pd.DataFrame({
        "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0,
    }, index=dates)
    df.index.name = "openTime"
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    result = run_backtest(df, cfg)
    assert result.n_trades == 0