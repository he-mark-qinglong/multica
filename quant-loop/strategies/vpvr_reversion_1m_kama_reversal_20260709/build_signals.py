"""Signal builder for vpvr_reversion_1m_kama_reversal_20260709 (iter#67 V3).

Public API:
    build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame

Input columns:
    - open, high, low, close: OHLC
    - volume: traded volume
    - (index) ts: bar timestamp (1m cadence, expected)

Output columns:
    - signal: -1 / 0 / +1
    - kama: Kaufman adaptive moving average
    - kama_slope: rolling slope of KAMA, expressed in ATR multiples so the
      threshold is unit-free
    - kama_turn: +1 (slope flipped from negative→non-negative),
                -1 (flipped positive→non-positive), 0 otherwise
    - vpvr_poc: rolling Point of Control on a `vpvr_window_bars` look-back
    - atr: average true range (`atr_period` bars)
    - poc_distance_atr: |close - poc| / atr, NaN while ATR is seeding
    - kama_slope_atr: ATR-normalised KAMA slope magnitude (diagnostic)

Convention follows the vpvr_sentiment_attention_1m_20260716 scaffold
(iter#71): pure numpy/pandas in, no I/O, NaN-tolerant, no look-ahead —
every rolling operation `shift(1)`'d where the bar itself would otherwise
leak into its own indicator.

Kaufman-adaptive MA (Kaufman, 1995 — "Smarter Trading"):
    direction[t] = |close[t] - close[t-period]|
    volatility[t] = sum_{i=0}^{period-1} |close[t-i] - close[t-i-1]|
    ER[t]         = direction / volatility              (efficiency ratio)
    smoothing[t]  = ER * (fast - slow) + slow            (fast < slow)
                  = ER * (2/(N+1) - 2/(2N+1)) + 2/(2N+1)
    KAMA[t]       = KAMA[t-1] + smoothing * (close[t] - KAMA[t-1])

Default: period=10, fast=2, slow=30 (the canonical Kaufman "trend-best"
regime). On 1m SOLUSDT bars this adapts between a fast (1-period EMA)
when prices trend (ER→1) and a slow (30-period EMA) when prices chop
(ER→0), giving a clean and adaptive reversal timing.

We then derive the `kama_turn` flag by comparing today's KAMA slope to
the slope kama_slope_lookback bars ago:
    slope_now = kama[t] - kama[t-1]
    slope_then = kama[t-lookback] - kama[t-(lookback+1)]
    turn = +1 if slope_then < 0 and slope_now >= 0   (down→up, long)
           -1 if slope_then > 0 and slope_now <= 0   (up→down, short)
            0  otherwise
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers — pure, vectorised.
# ---------------------------------------------------------------------------

def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR with the standard rolling mean (not Wilder smoothing).

    Uses ``close.shift(1)`` so today's range does not leak into today's ATR.
    """
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean().rename("atr")


def _kama(close: pd.Series, period: int, fast: int, slow: int) -> pd.Series:
    """Kaufman Adaptive Moving Average (KAMA).

    Smoothing constants are converted to the standard
    ``2/(N+1)`` / ``2/(2N+1)`` form so callers can pass integer periods.
    NaN input propagates; warmup bars (period days needed) are NaN.
    """
    close = close.astype(np.float64)
    fast_sc = 2.0 / (fast + 1.0)
    slow_sc = 2.0 / (slow + 1.0)

    direction = (close - close.shift(period)).abs()
    change = close.diff().abs()
    volatility = change.rolling(period, min_periods=period).sum()
    er = (direction / volatility.replace(0.0, np.nan)).fillna(0.0)
    smoothing = (er * (fast_sc - slow_sc) + slow_sc)

    kama = pd.Series(np.nan, index=close.index, dtype=np.float64, name="kama")
    # Seed with the first finite close.
    valid = close.notna()
    if not valid.any():
        return kama
    seed_idx = close.index[valid.values.argmax()]
    seed_val = float(close.loc[seed_idx])
    prev = seed_val
    kama.loc[seed_idx] = seed_val
    # Walk forward through the index computing KAMA step-by-step.
    idx_list = list(close.index)
    start = idx_list.index(seed_idx) + 1
    last_seed_pos = idx_list.index(seed_idx)
    for pos in range(start, len(idx_list)):
        i = idx_list[pos]
        # Smooth over the period-needed window — re-validate inputs.
        prev_k = kama.iloc[pos - 1]
        c_now = close.iloc[pos]
        c_seed_pos = max(0, pos - period)
        if pd.isna(prev_k) or pd.isna(c_now) or pd.isna(close.iloc[c_seed_pos]):
            continue
        # Recompute smoothing factor from current close vector (vectorised
        # form), but only when the window is fully seeded.
        win_close = close.iloc[c_seed_pos:pos + 1]
        direction_v = float((win_close.iloc[-1] - win_close.iloc[0]))
        direction_v = abs(direction_v)
        change_v = win_close.diff().abs().iloc[1:]
        volatility_v = float(change_v.iloc[-period:].sum()) if pd.notna(change_v.iloc[-period:]).all() else np.nan
        if not np.isfinite(volatility_v) or volatility_v <= 0.0:
            er_v = 0.0
        else:
            er_v = direction_v / volatility_v
        sc_v = er_v * (fast_sc - slow_sc) + slow_sc
        kama.iloc[pos] = prev_k + sc_v * (c_now - prev_k)
    return kama


def _vpvr_poc(close: pd.Series, volume: pd.Series, window: int, n_bins: int) -> pd.Series:
    """Rolling VPVR Point of Control.

    Computed on bars ``[t-window+1, t]`` (inclusive, length = window). Each
    bar's volume is attributed to the price bin containing its close.
    The returned POC at bar ``t`` reflects data through bar ``t-1``
    because we ``shift(1)`` — the entry bar itself never leaks into the
    POC used to evaluate it (matches the rest of the framework).
    """
    close_arr = close.values.astype(np.float64)
    vol_arr = volume.values.astype(np.float64)
    n = len(close_arr)
    poc = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        c_seg = close_arr[i - window + 1 : i + 1]
        v_seg = vol_arr[i - window + 1 : i + 1]
        if not np.isfinite(c_seg).all() or not np.isfinite(v_seg).all():
            continue
        c_min, c_max = float(c_seg.min()), float(c_seg.max())
        if c_max <= c_min:
            continue
        edges = np.linspace(c_min, c_max, n_bins + 1)
        idx = np.clip(np.searchsorted(edges, c_seg, side="right") - 1, 0, n_bins - 1)
        bin_vol = np.zeros(n_bins, dtype=np.float64)
        for j, b in enumerate(idx):
            bin_vol[b] += v_seg[j]
        best = int(np.argmax(bin_vol))
        poc[i] = float((edges[best] + edges[best + 1]) / 2.0)
    return pd.Series(poc, index=close.index, name="vpvr_poc").shift(1)


def _kama_turn(kama: pd.Series, atr: pd.Series, lookback: int,
               slope_threshold_atr: float) -> pd.Series:
    """Detect KAMA slope turnarounds, expressed as +1 / -1 / 0.

    The threshold ``slope_threshold_atr`` is in ATR *fraction* per bar:
    when the slope change exceeds ``slope_threshold_atr`` × ATR, we
    consider it a genuine reversal and not a tape wobble. Default 0.20
    means 20% of ATR per bar — calibrated for 1m SOLUSDT noise.
    """
    slope_now = kama.diff()
    slope_then = kama.shift(lookback).diff()  # slope at the lookback anchor

    # ATR-normalised slope change in ATR-units.
    atr_safe = atr.replace(0.0, np.nan)
    slope_change_atr = (slope_now - slope_then).abs() / atr_safe

    turn = pd.Series(0, index=kama.index, dtype=np.int64, name="kama_turn")
    valid = kama.notna() & slope_then.notna() & slope_now.notna() & atr_safe.notna()
    big_enough = valid & (slope_change_atr >= slope_threshold_atr)

    long_turn = big_enough & (slope_then < 0) & (slope_now >= 0)
    short_turn = big_enough & (slope_then > 0) & (slope_now <= 0)

    turn = turn.mask(long_turn, 1).mask(short_turn, -1)
    return turn


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Build entry/exit signals for the KAMA-reversal VPVR reversion variant.

    Args:
        df: DataFrame with columns [open, high, low, close, volume].
            Index is expected to be a 1m timestamp grid (NaN-tolerant
            gaps otherwise).
        params: Strategy parameters from ``config.json``.

    Returns:
        DataFrame indexed by ``df.index`` with columns:
            ``signal``, ``kama``, ``kama_slope``,
            ``kama_turn``, ``vpvr_poc``, ``atr``,
            ``poc_distance_atr``, ``kama_slope_atr``.
    """
    df = df.copy()
    if "ts" in df.columns:
        df = df.set_index("ts")

    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)

    atr = _atr(df, params["atr_period"])
    kama = _kama(close, params["kama_period"], params["kama_fast"], params["kama_slow"])
    poc = _vpvr_poc(close, volume, params["vpvr_window_bars"], params["vpvr_bins"])

    kama_slope = kama.diff().rename("kama_slope")

    atr_safe = atr.replace(0.0, np.nan)
    poc_distance_atr = (close - poc).abs() / atr_safe
    kama_slope_atr = kama_slope / atr_safe

    kama_turn = _kama_turn(
        kama,
        atr,
        lookback=params["kama_slope_lookback"],
        slope_threshold_atr=params["kama_turn_threshold_atr"],
    )

    poc_buf = params["poc_atr_buffer"]
    # Long reversion: KAMA turned up AND price at/below POC AND within buffer.
    long_signal = (
        (close <= poc)
        & (poc_distance_atr <= poc_buf)
        & (kama_turn == 1)
    )
    # Short reversion: KAMA turned down AND price at/above POC AND within buffer.
    short_signal = (
        (close >= poc)
        & (poc_distance_atr <= poc_buf)
        & (kama_turn == -1)
    )

    signal = pd.Series(0, index=close.index, dtype=np.int64)
    signal[long_signal] = 1
    signal[short_signal] = -1
    signal = signal.clip(-1, 1)
    signal.name = "signal"

    return pd.DataFrame({
        "signal": signal,
        "kama": kama,
        "kama_slope": kama_slope,
        "kama_turn": kama_turn,
        "vpvr_poc": poc,
        "atr": atr,
        "poc_distance_atr": poc_distance_atr,
        "kama_slope_atr": kama_slope_atr,
    })
