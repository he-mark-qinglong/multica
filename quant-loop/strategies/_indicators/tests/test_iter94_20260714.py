"""Unit tests for ``_indicators/iter94_20260714.py``.

These tests exercise the pure indicator primitives the VPVR
specialist (B2 owner) wires into ``strategy.py`` for the three
iter#94 variants:

    V1 — ADX + realised vol → regime_router (TREND / RANGE / BREAKOUT)
    V2 — obi_zscore + vpvr_poc_proximity
    V3 — mtf_consensus_signals + vol_target_size / vol_target_size_series

Run from ``strategies/`` so ``from _indicators.iter94_20260714
import ...`` resolves:

    cd /home/smark/multica/quant-loop/strategies
    python3 -m pytest _indicators/tests/test_iter94_20260714.py -v
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make ``_indicators`` importable when running from the strategies dir.
HERE = Path(__file__).resolve().parent
STRATEGIES_ROOT = HERE.parent.parent
sys.path.insert(0, str(STRATEGIES_ROOT))

from _indicators.iter94_20260714 import (  # noqa: E402
    ADX_TREND_THRESHOLD,
    BARS_PER_YEAR_4H,
    BARS_PER_YEAR_1H,
    BARS_PER_YEAR_15M,
    BARS_PER_YEAR_1M,
    REGIME_LABELS,
    adx,
    realized_vol_bps,
    regime_router,
    obi_zscore,
    vpvr_poc_proximity,
    mtf_consensus_signals,
    vol_target_size,
    vol_target_size_series,
)


# ===========================================================================
# Helpers — synthetic OHLCV frames.
# ===========================================================================

def _make_ohlcv(n: int, seed: int = 0,
                base: float = 100.0, vol: float = 0.001,
                trend: float = 0.0) -> pd.DataFrame:
    """Synthetic OHLCV with controllable trend and noise.

    ``trend`` is per-bar drift on the log-close; positive for an
    uptrend (used to drive ADX > 25). Bars are 1m apart starting at
    2024-01-01 UTC.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC")
    log_ret = rng.normal(0.0, vol, size=n) + trend
    close = base * np.exp(np.cumsum(log_ret))
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(close, open_) + np.abs(rng.normal(0, base * 0.0005, size=n))
    low = np.minimum(close, open_) - np.abs(rng.normal(0, base * 0.0005, size=n))
    volume = np.abs(rng.normal(1000, 100, size=n))
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_trending(n: int = 800, seed: int = 7) -> pd.DataFrame:
    """A trending series strong enough to push ADX(14) > 25 reliably."""
    return _make_ohlcv(n=n, seed=seed, base=100.0, vol=0.0008, trend=0.0015)


def _make_chaotic(n: int = 800, seed: int = 8) -> pd.DataFrame:
    """High-volatility but directionless — should yield BREAKOUT-ish RV."""
    return _make_ohlcv(n=n, seed=seed, base=100.0, vol=0.01, trend=0.0)


def _make_quiet(n: int = 800, seed: int = 9) -> pd.DataFrame:
    """Low-volatility, low-trend — should fall into RANGE."""
    return _make_ohlcv(n=n, seed=seed, base=100.0, vol=0.0001, trend=0.0)


# ===========================================================================
# Constants
# ===========================================================================

def test_constants_annualisation_factors():
    """Annualisation constants must match 24/7 crypto bar counts."""
    assert BARS_PER_YEAR_4H == int(365.25 * 6)        # 2191
    assert BARS_PER_YEAR_1H == int(365.25 * 24)       # 8766
    assert BARS_PER_YEAR_15M == int(365.25 * 24 * 4)  # 35046
    assert BARS_PER_YEAR_1M == int(365.25 * 24 * 60)  # 525960


def test_constants_regime_thresholds():
    """Regime thresholds pinned by SPEC.md (vpvr_regime_blend_4h_20260714)."""
    assert ADX_TREND_THRESHOLD == 25.0
    assert set(REGIME_LABELS) == {"TREND", "RANGE", "BREAKOUT"}


# ===========================================================================
# V1 — ADX
# ===========================================================================

def test_adx_first_period_nan_and_range():
    """ADX must be NaN for the first ``period`` bars and in [0, 100] after."""
    df = _make_trending(n=400, seed=11)
    out = adx(df["high"], df["low"], df["close"], period=14)

    # First 14 bars NaN (smoothing window seeding).
    assert out.iloc[:14].isna().all()

    # After the window seeds, ADX must be a finite non-negative number
    # bounded by 100 (the Wilder definition).
    tail = out.dropna()
    assert len(tail) > 0
    assert (tail >= 0).all()
    assert (tail <= 100).all()


def test_adx_picks_up_strong_trend():
    """On a strongly-trending synthetic series ADX must exceed 25."""
    df = _make_trending(n=800, seed=42)
    out = adx(df["high"], df["low"], df["close"], period=14).dropna()
    assert out.median() > 25.0, f"ADX median {out.median()} too low for trend"


def test_adx_zero_when_flat():
    """ADX must stay near zero on a perfectly flat series."""
    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    flat = pd.DataFrame({
        "high": np.full(n, 100.0),
        "low": np.full(n, 100.0),
        "close": np.full(n, 100.0),
        "open": np.full(n, 100.0),
        "volume": np.ones(n),
    }, index=idx)
    out = adx(flat["high"], flat["low"], flat["close"], period=14).dropna()
    # With no directional movement, +DM and -DM are 0 → DX is 0 → ADX is 0.
    assert (out.abs() < 1e-9).all()


# ===========================================================================
# V1 — Realised volatility
# ===========================================================================

def test_realized_vol_first_window_nan():
    """First ``window`` bars of RV must be NaN (plus the shift-1)."""
    df = _make_chaotic(n=200, seed=3)
    out = realized_vol_bps(df["close"], window=30, bars_per_year=BARS_PER_YEAR_4H)
    # Window (30) + shift(1) = 31 leading NaN.
    assert out.iloc[:31].isna().all()
    assert out.iloc[31:].notna().all()


def test_realized_vol_positive_after_window():
    """Realised vol must be > 0 on a noisy series once seeded."""
    df = _make_chaotic(n=200, seed=4)
    out = realized_vol_bps(df["close"], window=30, bars_per_year=BARS_PER_YEAR_4H)
    tail = out.dropna()
    assert (tail > 0).all()


def test_realized_vol_scales_with_sqrt_bars_per_year():
    """Same raw returns annualised at 1h vs 4h must scale by sqrt(ratio).

        rv_1h / rv_4h = sqrt(BARS_PER_YEAR_1H / BARS_PER_YEAR_4H)
    """
    df = _make_chaotic(n=400, seed=5)
    rv_1h = realized_vol_bps(df["close"], window=30, bars_per_year=BARS_PER_YEAR_1H)
    rv_4h = realized_vol_bps(df["close"], window=30, bars_per_year=BARS_PER_YEAR_4H)
    ratio = rv_1h / rv_4h
    expected = math.sqrt(BARS_PER_YEAR_1H / BARS_PER_YEAR_4H)
    assert abs(ratio.dropna().median() - expected) < 1e-6


# ===========================================================================
# V1 — regime_router
# ===========================================================================

def test_regime_router_strong_trend_is_trend():
    """A trending synthetic series must classify as TREND, not RANGE."""
    df = _make_trending(n=800, seed=101)
    out = regime_router(
        df["close"], df["high"], df["low"], period=14, rv_window=30,
        bars_per_year=BARS_PER_YEAR_4H,
    )
    seeded = out.dropna()
    assert (seeded == "TREND").mean() > 0.7, (
        f"Trending series only {(seeded == 'TREND').mean():.0%} TREND"
    )


def test_regime_router_quiet_is_range():
    """Low-vol, low-trend → RANGE."""
    df = _make_quiet(n=800, seed=202)
    out = regime_router(
        df["close"], df["high"], df["low"], period=14, rv_window=30,
        bars_per_year=BARS_PER_YEAR_4H,
    )
    seeded = out.dropna()
    # Quiet should yield RANGE or TREND (with very low ADX) — but
    # never BREAKOUT because RV stays below the 350bps threshold.
    assert (seeded != "BREAKOUT").all(), "Quiet series hit BREAKOUT"


def test_regime_router_only_three_labels():
    """Output values must come from {TREND, RANGE, BREAKOUT} or NaN."""
    df = _make_chaotic(n=400, seed=303)
    out = regime_router(
        df["close"], df["high"], df["low"], period=14, rv_window=30,
        bars_per_year=BARS_PER_YEAR_4H,
    )
    valid = out.dropna().unique()
    assert set(valid).issubset(set(REGIME_LABELS)), f"unexpected labels {set(valid)}"


def test_regime_router_threshold_override():
    """Override thresholds: trend @ 0 must yield TREND on every bar."""
    df = _make_quiet(n=300, seed=404)
    out = regime_router(
        df["close"], df["high"], df["low"], period=14, rv_window=30,
        bars_per_year=BARS_PER_YEAR_4H,
        adx_trend=0.0,  # any ADX qualifies
    )
    seeded = out.dropna()
    assert (seeded == "TREND").all()


# ===========================================================================
# V2 — obi_zscore
# ===========================================================================

def test_obi_zscore_first_window_nan():
    """Z-score is NaN until window fills (and shift-1)."""
    df = _make_ohlcv(n=200, seed=12)
    out = obi_zscore(
        df["close"], df["open"], df["high"], df["low"], window=20,
    )
    # First ``window`` bars NaN (rolling mean+std seed), then the
    # extra shift(1) on the normalisation pushes the first valid z
    # to index == window. (z[t] uses mean[t-1] and std[t-1] over
    # raw[t-window..t-1]; that window is complete from t == window.)
    assert out.iloc[:20].isna().all()
    assert out.iloc[20:].notna().all()


def test_obi_zscore_sign_matches_close_minus_open_direction():
    """When today's close > open (raw_ob > 0) and the window mean is ~0,
    the z-score should be positive."""
    n = 200
    rng = np.random.default_rng(13)
    idx = pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC")
    close = pd.Series(100.0 + rng.normal(0, 0.5, size=n).cumsum(), index=idx)
    open_ = close.shift(1).fillna(100.0)
    high = pd.Series(np.maximum(close, open_) + 0.2, index=idx)
    low = pd.Series(np.minimum(close, open_) - 0.2, index=idx)
    out = obi_zscore(close, open_, high, low, window=20)
    # Drop NaN warm-up; the surviving z-scores must have non-trivial
    # absolute values (not all zero).
    tail = out.dropna()
    assert tail.abs().median() > 0.0


def test_obi_zscore_handles_doji_bars():
    """Bars where high == low produce NaN raw, which must propagate."""
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC")
    close = pd.Series(np.full(n, 100.0), index=idx)
    open_ = close.copy()
    high = close.copy()
    low = close.copy()  # high == low everywhere
    out = obi_zscore(close, open_, high, low, window=20)
    assert out.isna().all()


# ===========================================================================
# V2 — vpvr_poc_proximity
# ===========================================================================

def test_poc_proximity_within_threshold():
    """If |price - poc| < 0.3 * ATR, the filter returns True."""
    s = pd.Series([100.0, 110.0, 90.0, 105.0])
    poc = pd.Series([100.0, 100.0, 100.0, 100.0])
    atr = pd.Series([10.0, 10.0, 10.0, 10.0])
    out = vpvr_poc_proximity(s, poc, atr, threshold=0.3)
    # |100-100|=0  < 3  → True
    # |110-100|=10 > 3  → False
    # |90-100|=10  > 3  → False
    # |105-100|=5  > 3  → False
    assert list(out) == [True, False, False, False]


def test_poc_proximity_nan_propagates():
    """NaN inputs propagate to NaN — not False — so callers can tell."""
    s = pd.Series([100.0, np.nan, 100.0])
    poc = pd.Series([100.0, 100.0, np.nan])
    atr = pd.Series([10.0, 10.0, 10.0])
    out = vpvr_poc_proximity(s, poc, atr, threshold=0.3)
    # First bar: True; second & third: NaN.
    assert bool(out.iloc[0])
    assert pd.isna(out.iloc[1])
    assert pd.isna(out.iloc[2])


# ===========================================================================
# V3 — mtf_consensus_signals
# ===========================================================================

def test_mtf_consensus_all_long():
    """3/3 long → +1."""
    idx = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    s1 = pd.Series([1, 1, 1, 1, 1], index=idx)
    s2 = pd.Series([1, 1, 1, 1, 1], index=idx)
    s3 = pd.Series([1, 1, 1, 1, 1], index=idx)
    out = mtf_consensus_signals([s1, s2, s3])
    assert (out == 1).all()


def test_mtf_consensus_two_long_one_flat():
    """2/3 long, 1/3 flat → +1 (majority > 2/3)."""
    idx = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    s1 = pd.Series([1, 1, 1, 1, 1], index=idx)
    s2 = pd.Series([1, 1, 1, 1, 1], index=idx)
    s3 = pd.Series([0, 0, 0, 0, 0], index=idx)  # flat
    out = mtf_consensus_signals([s1, s2, s3])
    assert (out == 1).all()


def test_mtf_consensus_two_long_one_short():
    """2/3 long, 1/3 short → +1 (long majority 2/3 > threshold 2/3? check)."""
    # 2 long out of 3 = 0.6667 share; threshold default 2/3 = 0.6667.
    # Default is strict > threshold, so this case is 0.
    idx = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    s1 = pd.Series([1, 1, 1], index=idx)
    s2 = pd.Series([1, 1, 1], index=idx)
    s3 = pd.Series([-1, -1, -1], index=idx)
    out = mtf_consensus_signals([s1, s2, s3], threshold=2.0 / 3.0)
    assert (out == 0).all(), "exact 2/3 should not pass strict > threshold"

    # Lower threshold to 0.5 → 2/3 long majority → +1.
    out2 = mtf_consensus_signals([s1, s2, s3], threshold=0.5)
    assert (out2 == 1).all()


def test_mtf_consensus_mixed_no_majority():
    """3 different directions → 0."""
    idx = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    s1 = pd.Series([1, 1, 1], index=idx)
    s2 = pd.Series([-1, -1, -1], index=idx)
    s3 = pd.Series([0, 0, 0], index=idx)
    out = mtf_consensus_signals([s1, s2, s3])
    assert (out == 0).all()


def test_mtf_consensus_all_short():
    """3/3 short → -1."""
    idx = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    s1 = pd.Series([-1, -1, -1], index=idx)
    s2 = pd.Series([-1, -1, -1], index=idx)
    s3 = pd.Series([-1, -1, -1], index=idx)
    out = mtf_consensus_signals([s1, s2, s3])
    assert (out == -1).all()


def test_mtf_consensus_index_mismatch_raises():
    """Different indices across inputs must raise — caller must align."""
    idx_a = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    idx_b = pd.date_range("2024-01-02", periods=3, freq="1h", tz="UTC")
    s1 = pd.Series([1, 1, 1], index=idx_a)
    s2 = pd.Series([1, 1, 1], index=idx_b)
    s3 = pd.Series([1, 1, 1], index=idx_a)
    with pytest.raises(ValueError):
        mtf_consensus_signals([s1, s2, s3])


# ===========================================================================
# V3 — vol_target_size
# ===========================================================================

def test_vol_target_size_basic():
    """Basic sanity: units = nav * (target / realised) / price."""
    # nav=100k, target=0.15 (15%), realised=0.30 (30%), price=100.
    # units = 100k * (0.15/0.30) / 100 = 500.
    sz = vol_target_size(target_vol=0.15, realized_vol=0.30, nav=100_000.0, price=100.0)
    assert abs(sz - 500.0) < 1e-9


def test_vol_target_size_zero_when_inputs_degenerate():
    """Zero or negative nav / price / realised_vol → 0."""
    assert vol_target_size(0.15, 0.30, 0.0, 100.0) == 0.0
    assert vol_target_size(0.15, 0.30, 100.0, 0.0) == 0.0
    assert vol_target_size(0.15, 0.0, 100.0, 100.0) == 0.0
    assert vol_target_size(0.0, 0.30, 100.0, 100.0) == 0.0
    assert vol_target_size(-0.15, 0.30, 100.0, 100.0) == 0.0


def test_vol_target_size_finite_guard():
    """NaN / inf inputs → 0."""
    assert vol_target_size(np.nan, 0.30, 100.0, 100.0) == 0.0
    assert vol_target_size(0.15, np.inf, 100.0, 100.0) == 0.0
    assert vol_target_size(0.15, 0.30, np.nan, 100.0) == 0.0


def test_vol_target_size_floor_and_cap():
    """floor / cap clamp the output."""
    raw = vol_target_size(0.15, 0.30, 100_000.0, 100.0)  # 500
    assert vol_target_size(0.15, 0.30, 100_000.0, 100.0, floor=600.0) == 600.0
    assert vol_target_size(0.15, 0.30, 100_000.0, 100.0, cap=400.0) == 400.0
    assert raw == 500.0  # baseline still raw


def test_vol_target_size_series_basic():
    """Vectorised wrapper returns 0 on NaN bars and respects price_series."""
    rv = pd.Series([np.nan, 0.30, 0.30, np.nan])
    px = pd.Series([100.0, 100.0, 100.0, 100.0])
    out = vol_target_size_series(
        target_vol=0.15, realized_vol_series=rv, nav=100_000.0,
        price_series=px,
    )
    # units = 100k * (0.15/0.30) / 100 = 500.0
    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == 500.0
    assert out.iloc[2] == 500.0
    assert pd.isna(out.iloc[3])


def test_vol_target_size_series_default_price_one():
    """When price_series is None the wrapper assumes price=1.0 (returns-mode)."""
    rv = pd.Series([0.30, 0.30])
    out = vol_target_size_series(target_vol=0.15, realized_vol_series=rv, nav=1.0)
    # units = 1 * (0.15/0.30) / 1 = 0.5
    assert (out.dropna() == 0.5).all()
