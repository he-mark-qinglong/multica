"""Signal builder for vpvr_macro_calendar_4h_20260715.

Public API:
    build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame

Input columns:
    - ts (index or column): bar timestamp (UTC, 4h-aligned)
    - open, high, low, close: OHLC
    - volume: traded volume

Output columns (index = df.index):
    - signal: -1 / 0 / +1
    - regime_ok: bool gate (True = no imminent macro event AND post-event vol normalised)
    - vpvr_poc: point of control
    - atr: average true range
    - atr_ma: ATR moving average (normalisation reference)
    - poc_distance_atr: distance from close to POC in ATR multiples
    - macro_proximity_bars: signed bars to nearest event (- if past, + if future, 0 on day)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from macro_calendar import high_impact_event_dates


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _vpvr_poc(close: pd.Series, volume: pd.Series, window: int, n_bins: int) -> pd.Series:
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


def _macro_proximity_bars(idx: pd.DatetimeIndex, event_dates: list, buffer_bars: int) -> pd.Series:
    """For each bar, signed bars distance to nearest event (negative = past)."""
    n = len(idx)
    out = np.full(n, buffer_bars + 1, dtype=np.int64)  # default: far from any event
    ev = sorted(event_dates)
    for i, ts in enumerate(idx):
        day = ts.normalize()
        # find nearest event
        # binary search would be faster; n=10k * 200 events is fine with linear.
        nearest = min(ev, key=lambda d: abs((d - day).days))
        delta_days = (day - nearest).days
        # 4h bar -> 6 bars/day; sign convention: future positive, past negative
        out[i] = int(np.sign(delta_days) * abs(delta_days) * 6) if delta_days != 0 else 0
    return pd.Series(out, index=idx)


def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Build entry/exit signals for the macro-calendar VPVR reversion variant."""
    df = df.copy()
    if "ts" in df.columns:
        df = df.set_index("ts")

    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    idx = df.index

    poc = _vpvr_poc(close, volume, params["vpvr_window_bars"], params["vpvr_bins"])
    atr = _atr(df, params["atr_period"])
    atr_ma = atr.rolling(params["atr_period"] * 5, min_periods=params["atr_period"] * 5).mean()

    event_dates = high_impact_event_dates()
    proximity = _macro_proximity_bars(idx, event_dates, params["macro_buffer_bars"])
    abs_prox = proximity.abs()
    in_event_buffer = abs_prox <= params["macro_buffer_bars"]

    # Post-event window: bars after an event with elevated ATR relative to its MA
    post_event_window = (proximity < 0) & (proximity >= -params["post_event_window_bars"])
    atr_safe = atr.replace(0, np.nan)
    vol_normalised = (atr / atr_ma) >= params["post_event_atr_min_mult"]
    post_event_blocked = post_event_window & ~vol_normalised

    regime_ok = (~in_event_buffer) & (~post_event_blocked)

    poc_safe = poc.replace([np.nan, np.inf], np.nan)
    poc_distance_atr = (close - poc_safe).abs() / atr_safe

    poc_thr = params["poc_atr_buffer"]
    long_signal = regime_ok & (close > poc_safe) & (poc_distance_atr <= poc_thr)
    short_signal = regime_ok & (close < poc_safe) & (poc_distance_atr <= poc_thr)

    signal = pd.Series(0, index=close.index, dtype=np.int64)
    signal[long_signal] = 1
    signal[short_signal] = -1
    signal = signal.clip(-1, 1)

    return pd.DataFrame({
        "signal": signal,
        "regime_ok": regime_ok,
        "vpvr_poc": poc,
        "atr": atr,
        "atr_ma": atr_ma,
        "poc_distance_atr": poc_distance_atr,
        "macro_proximity_bars": proximity,
    })