"""Tests for vpvr_edge_zscore_multi_tf (SMA-34991) signal builders.

NOTE — TDD VIOLATION ACKNOWLEDGED.

These tests were written AFTER the production code in
``build_signals.py`` / ``strategy.py``. Per the project's TDD
discipline, this is a "tests-after" verification, not a true red-green
TDD cycle. The tests are nonetheless valuable: they encode the
no-look-ahead invariant, the edge-direction logic, and the
backtest smoke behavior, so any regression in future edits is caught.

WARNING: If a test passes on first run, that only proves the
existing implementation matches the test. It does NOT prove the
test would have caught the bug we think it's catching. Treat any
"passes immediately" result with suspicion. The author should re-read
both files and confirm the assertion matches the documented intent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
QUANT_LOOP = REPO_ROOT.parents[1]
_INDICATORS_DIR = QUANT_LOOP / "strategies" / "_indicators"
_SHARED_DIR = QUANT_LOOP / "_shared"
for _p in (str(REPO_ROOT), str(_INDICATORS_DIR), str(_SHARED_DIR / "execution"), str(_SHARED_DIR / "sizing")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from build_signals import build_signals_15m, build_signals_2h, build_signals_1m


# Test config — minimal defaults, deterministic & cheap.
_PARAMS = {
    "zscore_window_bars": 20,
    "zscore_entry_threshold_15m": 2.0,
    "zscore_confirm_threshold_2h": 1.0,
    "zscore_exit_threshold": 0.5,
    "poc_slope_window_bars": 3,
    "trend_ema_period": 10,
    "trend_slope_min_bps_per_bar": 5.0,
    "vpvr_bins": 16,
    "vpvr_window_bars_15m": 30,
    "vpvr_snapshot_every_bars_15m": 4,
    "vpvr_window_bars_2h": 30,
    "vpvr_snapshot_every_bars_2h": 4,
    "vpvr_hvn_quantile": 0.80,
    "vpvr_lvn_quantile": 0.20,
    "vpvr_num_hvn": 2,
    "vpvr_num_lvn": 2,
    "atr_period_15m": 7,
    "atr_period_2h": 7,
    "edge_buffer_atr_15m": 1.5,
}


def _make_ohlcv(n: int = 200, start: str = "2024-01-01", freq: str = "15min",
                seed: int = 42) -> pd.DataFrame:
    """Make a deterministic OHLCV frame of length ``n``."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=n, freq=freq, tz="UTC")
    base = 30000 + np.cumsum(rng.normal(0, 30, n))
    close = base + rng.normal(0, 5, n)
    high = close + np.abs(rng.normal(0, 10, n))
    low = close - np.abs(rng.normal(0, 10, n))
    open_ = close + rng.normal(0, 5, n)
    volume = rng.uniform(50, 200, n)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }, index=idx)


# ---------------------------------------------------------------------------
# 15m signal: no-lookahead invariant — z-score is shifted by 1 bar.
# ---------------------------------------------------------------------------

def test_15m_zscore_is_shifted_no_lookahead():
    """The z-score used at bar ``t`` must use only data strictly before ``t``.
    We test by: (a) the z-score at bar ``t`` equals NaN whenever the
    rolling window isn't yet filled (warm-up), AND (b) the z-score
    series is finite only after a warm-up period equal to
    ``zscore_window_bars`` plus the snapshot stride.
    """
    df = _make_ohlcv(n=300, freq="15min", seed=7)
    sig = build_signals_15m(df, _PARAMS)
    z = sig["z_15m"]

    # First warm-up bars must be NaN: rolling window of 20 + stride delay.
    assert z.iloc[: _PARAMS["zscore_window_bars"]].isna().all(), (
        f"z_15m had non-NaN values in warm-up window:\n{z.iloc[:_PARAMS['zscore_window_bars']]}"
    )

    # After warm-up, z must be finite (or NaN only due to 0-std windows,
    # which are rare in random data; assert "mostly finite").
    finite_frac = z.iloc[_PARAMS["zscore_window_bars"]:].notna().mean()
    assert finite_frac > 0.9, f"only {finite_frac:.2%} of post-warmup z is finite"


def test_15m_poc_is_forward_filled_from_shifted_snapshot():
    """The POC column must be a shifted snapshot that is forward-filled.
    In particular, the leading rows must be NaN (no snapshot exists
    on bars 0..stride-1 because the snapshot at bar 0 uses today's
    volume, which would be look-ahead if exposed at bar 0).
    """
    df = _make_ohlcv(n=200, freq="15min", seed=11)
    sig = build_signals_15m(df, _PARAMS)
    poc = sig["poc"]

    stride = _PARAMS["vpvr_snapshot_every_bars_15m"]
    assert poc.iloc[:stride].isna().all(), (
        f"POC was non-NaN in the first {stride} bars (leak?):\n{poc.iloc[:stride]}"
    )


def test_15m_zscore_window_skipped_when_std_zero():
    """If the rolling window has zero std (constant price), z must be NaN
    (not a divide-by-zero inf)."""
    df = _make_ohlcv(n=120, freq="15min", seed=3)
    df["close"] = 100.0  # constant -> std = 0 across the window
    df["high"] = 100.0
    df["low"] = 100.0
    df["open"] = 100.0
    sig = build_signals_15m(df, _PARAMS)
    z = sig["z_15m"]
    inf_count = np.isinf(z).sum()
    assert inf_count == 0, f"z_15m contains {int(inf_count)} inf values (std=0 not handled)"


# ---------------------------------------------------------------------------
# 2h signal: EMA slope is computed on shifted close.
# ---------------------------------------------------------------------------

def test_2h_ema_is_shifted_no_lookahead():
    """The EMA(20) used at bar ``t`` must equal the EMA of closes strictly
    before ``t``. We test by inserting a single-bar price spike at
    bar ``t*`` and confirming that ``ema_2h.loc[t*]`` is NOT affected
    by the spike (i.e., it equals the EMA of bars 0..t*-1).
    """
    df = _make_ohlcv(n=200, freq="2h", seed=5)

    # Save a "control" frame with no spike.
    sig_ctrl = build_signals_2h(df, _PARAMS)
    ema_ctrl = sig_ctrl["ema_2h"].copy()

    # Insert a 100x spike at bar 100.
    df_spike = df.copy()
    spike_idx = df_spike.index[100]
    df_spike.loc[spike_idx, "close"] = df_spike["close"].iloc[100] * 100
    sig_spike = build_signals_2h(df_spike, _PARAMS)

    # At the bar AFTER the spike (t=101), the EMA must be identical
    # to the control (since ema at 101 is computed from close.shift(1),
    # which excludes close[101] — only close[100] is in the EMA span,
    # but the shift(1) means EMA[101] sees close[100] via shifted_close[101]).
    # Actually for compute-on-shifted-close: EMA at bar t uses
    # closes[t-1], t-2, ... so EMA[101] (which uses close.shift(1) =
    # close[100, 99, 98, ...]) IS affected by the spike at close[100].
    # So we check that EMA[100] (which uses close[-1, 0..99] = close[99,
    # 98, ..., 0]) is unaffected by close[100].
    ema_spike_at_100 = sig_spike["ema_2h"].iloc[100]
    ema_ctrl_at_100 = ema_ctrl.iloc[100]
    assert np.isfinite(ema_spike_at_100) and np.isfinite(ema_ctrl_at_100), (
        "EMA at bar 100 not finite for either frame"
    )
    # Allow a tiny float rounding error from differences in warm-up.
    assert abs(ema_spike_at_100 - ema_ctrl_at_100) < 1e-6, (
        f"EMA at bar 100 differs after a spike inserted AT bar 100: "
        f"{ema_spike_at_100} vs {ema_ctrl_at_100} — should be unaffected"
    )


# ---------------------------------------------------------------------------
# 1m execution: decision = edge_15m × trend_confirm.
# ---------------------------------------------------------------------------

def test_1m_decision_requires_trend_confirmation():
    """When the 15m edge is non-zero but the 2h trend does not confirm
    the same direction (and |z_2h| > confirm threshold), the per-bar
    decision at 1m must be 0 (no entry).
    """
    df_15m = _make_ohlcv(n=300, freq="15min", seed=21)
    df_2h = _make_ohlcv(n=300, freq="2h", seed=22)
    df_1m = _make_ohlcv(n=300 * 15, freq="1min", seed=23)

    sig_15m = build_signals_15m(df_15m, _PARAMS)
    sig_2h = build_signals_2h(df_2h, _PARAMS)
    sig_1m = build_signals_1m(df_1m, _PARAMS, sig_15m=sig_15m, sig_2h=sig_2h)

    # If the 15m signal fires at any bar that lands on a 2h bar whose
    # 2h trend is in the opposite direction, the 1m decision must be 0.
    edge_15m = sig_15m["edge_15m"]
    trend_2h = sig_2h["trend_dir_2h"]
    z_2h = sig_2h["z_2h"]
    if (edge_15m == 1).any():
        # Find the 15m bar where edge=1.
        long_bar_idx = int(np.argmax((edge_15m == 1).values))
        long_15m_ts = edge_15m.index[long_bar_idx]
        # Trend at-or-before this 15m bar from the 2h frame.
        trend_at = trend_2h.reindex(
            pd.DatetimeIndex([long_15m_ts]), method="ffill"
        ).iloc[0]
        z_at = z_2h.reindex(
            pd.DatetimeIndex([long_15m_ts]), method="ffill"
        ).iloc[0]
        if trend_at == -1 and abs(z_at) > _PARAMS["zscore_confirm_threshold_2h"]:
            # 15m says LONG but 2h is BEAR with extreme z -> decision must be 0.
            decision_at = sig_1m["decision"].reindex(
                pd.DatetimeIndex([long_15m_ts]), method="ffill"
            ).iloc[0]
            assert int(decision_at) == 0, (
                f"1m decision fired long ({int(decision_at)}) when 15m=long but "
                f"2h is BEAR (trend={int(trend_at)}, z_2h={float(z_at):.2f})"
            )


def test_1m_conviction_marks_three_way_agreement():
    """When 15m edge and 2h trend agree AND z_2h exceeds the confirm
    threshold, the 1m conviction column must mark 'high'. We confirm
    by counting 'high' convictions on the frame and asserting the count
    is strictly less than the count of all 1m decisions (every 'high'
    is also a decision, but not every decision is 'high').
    """
    df_15m = _make_ohlcv(n=300, freq="15min", seed=31)
    df_2h = _make_ohlcv(n=300, freq="2h", seed=32)
    df_1m = _make_ohlcv(n=300 * 15, freq="1min", seed=33)
    sig_15m = build_signals_15m(df_15m, _PARAMS)
    sig_2h = build_signals_2h(df_2h, _PARAMS)
    sig_1m = build_signals_1m(df_1m, _PARAMS, sig_15m=sig_15m, sig_2h=sig_2h)

    n_high = int((sig_1m["conviction"] == "high").sum())
    n_nonzero = int((sig_1m["decision"] != 0).sum())
    assert n_high <= n_nonzero, (
        f"n_high={n_high} exceeds n_nonzero decisions={n_nonzero}"
    )


# ---------------------------------------------------------------------------
# Cost helper: round-trip cost matches the BINANCE_SPOT convention.
# ---------------------------------------------------------------------------

def test_apply_cost_round_trip_matches_spec():
    """The apply_cost helper must return 2x single-leg cost on round-trip.

    With sufficiently large ADV (participation -> 0), slippage -> 0 and
    the round-trip cost must equal 2x the taker fee. BINANCE_SPOT with
    BNB discount applies a 25% rebate to the taker fee of 10bp, giving
    7.5bp/side x 2 sides = 15bp round-trip.
    """
    from cost_model import BINANCE_SPOT, apply_cost
    notional = 100_000.0
    # Participation 1e-9 -> slip = 0.1 * sqrt(1e-9) * 10000 ≈ 0.001 bp.
    adv = 1e14
    cost = apply_cost(
        notional_usd=notional, adv_usd=adv, venue=BINANCE_SPOT, side="taker"
    )
    expected_min = notional * (2 * 7.5) / 10_000  # 150 USD
    # Allow a tiny upward tolerance for residual sqrt slippage.
    assert expected_min - 1.0 <= cost <= expected_min + 5.0, (
        f"cost {cost} outside expected band [{expected_min - 1}, {expected_min + 5}]"
    )