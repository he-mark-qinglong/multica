"""Signal builder for vpvr_reversion_1m_volume_profile_break_20260709.

Public API:
    build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame

Input columns:
    - ts (DatetimeIndex or 'ts' column): bar timestamp.
    - open, high, low, close: OHLC.
    - volume: traded volume.

Output columns:
    - signal: -1 / 0 / +1. -1 = fade the upside breakout (short).
                           +1 = fade the downside breakout (long).
    - vpvr_poc: rolling-window Point of Control.
    - vpvr_vah, vpvr_val: rolling-window Value Area High / Low.
    - atr: rolling ATR(14) on 1m.
    - vol_ratio: volume / rolling 6h median volume.
    - break_state: -1 / 0 / +1 — recent failed-break direction in lookback.
                    -1 means there was at least one upside break+spike and the
                       current bar is back inside the value area (failed upside).
                    +1 means a failed downside break (rebound off VAL).
                     0  no qualifying recent failed break.
    - poc_distance_atr: |close - vpvr_poc| / atr (diagnostic only).

Signal generation rules (see SPEC.md V5):
  short = (recent upside break with vol spike) AND now inside VA from above
        AND close > poc AND current vol_ratio < vol_spike_k
  long  = (recent downside break with vol spike) AND now inside VA from below
        AND close < poc AND current vol_ratio < vol_spike_k
The execution layer (strategy.py) also enforces cooldown and ATR>0.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Pure helpers — identical conventions to the project indicator library.
# ---------------------------------------------------------------------------
def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _vpvr_poc(close: pd.Series, volume: pd.Series, window: int, n_bins: int) -> pd.Series:
    """Rolling POC (highest-volume bin center) over `window` bars."""
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
    return pd.Series(poc, index=close.index)


def _vpvr_value_area(
    close: pd.Series,
    volume: pd.Series,
    window: int,
    n_bins: int,
    value_area_fraction: float,
) -> Tuple[pd.Series, pd.Series]:
    """Rolling VAH / VAL: expand outward from POC until `value_area_fraction`
    of the window's total volume is covered."""
    close_arr = close.values.astype(np.float64)
    vol_arr = volume.values.astype(np.float64)
    n = len(close_arr)
    vah = np.full(n, np.nan, dtype=np.float64)
    val = np.full(n, np.nan, dtype=np.float64)

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

        total = float(bin_vol.sum())
        if total <= 0:
            continue
        target = value_area_fraction * total

        weights = bin_vol.tolist()
        poc_bin = int(np.argmax(weights))
        lo = poc_bin
        hi = poc_bin
        cum = weights[poc_bin]
        while cum < target:
            can_lo = weights[lo - 1] if lo > 0 else -1.0
            can_hi = weights[hi + 1] if hi < n_bins - 1 else -1.0
            if can_lo < 0 and can_hi < 0:
                break
            if can_hi >= can_lo and hi < n_bins - 1:
                hi += 1
                cum += weights[hi]
            elif can_lo >= can_hi and lo > 0:
                lo -= 1
                cum += weights[lo]
            else:
                if hi < n_bins - 1:
                    hi += 1
                    cum += weights[hi]
                elif lo > 0:
                    lo -= 1
                    cum += weights[lo]
                else:
                    break
        val[i] = float(edges[lo])
        vah[i] = float(edges[hi + 1])

    val_s = pd.Series(val, index=close.index)
    vah_s = pd.Series(vah, index=close.index)
    return vah_s, val_s


def _vol_ratio(volume: pd.Series, lookback: int) -> pd.Series:
    v = volume.astype(np.float64)
    med = v.rolling(lookback, min_periods=lookback).median()
    return v / med.replace(0, np.nan)


# ---------------------------------------------------------------------------
# Failed-break detector.
# ---------------------------------------------------------------------------
def _recent_failed_break(
    close: pd.Series,
    vah: pd.Series,
    val_: pd.Series,
    vol_ratio: pd.Series,
    lookback: int,
    vol_spike_k: float,
) -> pd.Series:
    """Return a Series of -1 / 0 / +1 indicating a recent failed break.

      -1  : within the last `lookback` bars there was at least one bar where
            close > vah AND vol_ratio >= vol_spike_k, and the current close
            has re-entered the value area (<= vah).  → fade the upside.
      +1  : within the last `lookback` bars there was at least one bar where
            close < val AND vol_ratio >= vol_spike_k, and the current close
            has re-entered from below (>= val).  → fade the downside.
       0  : otherwise.

    Operates on shifted values per bar t: looks at indices [t-lookback+1, t]
    and compares the *current* close to vah/val.  Implemented as rolling
    max/min for vector speed.
    """
    above_vah = (close > vah) & (vol_ratio >= vol_spike_k)
    below_val = (close < val_) & (vol_ratio >= vol_spike_k)
    above_recent = above_vah.astype(np.int8).rolling(lookback, min_periods=1).max()
    below_recent = below_val.astype(np.int8).rolling(lookback, min_periods=1).max()

    # Current-bar re-entry check (current close vs current vah/val).
    reenter_down = (close <= vah).fillna(False)
    reenter_up = (close >= val_).fillna(False)

    short_break = (above_recent > 0) & reenter_down
    long_break = (below_recent > 0) & reenter_up

    state = pd.Series(0, index=close.index, dtype=np.int8)
    state[short_break.fillna(False)] = -1
    state[long_break.fillna(False)] = 1
    state = state.clip(-1, 1)
    return state


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Build entry/exit signals for vpvr_reversion_1m_volume_profile_break.

    Args:
        df: DataFrame with OHLCV columns.
        params: Strategy parameters from config.json.

    Returns:
        DataFrame indexed by ts with signal and diagnostic columns.
    """
    df = df.copy()
    if "ts" in df.columns:
        df = df.set_index("ts")

    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)

    poc = _vpvr_poc(close, volume, params["vpvr_window_bars"], params["vpvr_bins"])
    vah, val = _vpvr_value_area(
        close, volume,
        params["vpvr_window_bars"], params["vpvr_bins"],
        params["value_area_fraction"],
    )
    atr = _atr(df, params["atr_period"])
    vol_ratio = _vol_ratio(volume, params["vol_median_lookback_bars"])

    break_state = _recent_failed_break(
        close, vah, val, vol_ratio,
        params["break_lookback_bars"], params["vol_spike_k"],
    )

    atr_safe = atr.replace(0, np.nan)
    poc_distance_atr = (close - poc).abs() / atr_safe

    spike_k = float(params["vol_spike_k"])

    # Entry conditions.
    short_signal = (
        (break_state == -1)
        & (close > poc)
        & (vol_ratio < spike_k)
    )
    long_signal = (
        (break_state == 1)
        & (close < poc)
        & (vol_ratio < spike_k)
    )

    signal = pd.Series(0, index=close.index, dtype=np.int64)
    signal[long_signal] = 1
    signal[short_signal] = -1
    signal = signal.clip(-1, 1)

    out = pd.DataFrame({
        "signal": signal,
        "break_state": break_state,
        "vpvr_poc": poc,
        "vpvr_vah": vah,
        "vpvr_val": val,
        "atr": atr,
        "vol_ratio": vol_ratio,
        "poc_distance_atr": poc_distance_atr,
    })
    out.index.name = "ts"
    return out
