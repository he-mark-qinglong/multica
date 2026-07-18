"""Signal builder for vpvr_tod_session_filter_15m_20260715.

Logic:
    1. Compute 15m ATR and intra-session VPVR POC (rolling window=1 session = 32 bars).
    2. Label each bar with its asia/london/us session.
    3. Only emit signals during `session_filter_names` (default london+us = high-volume windows).
    4. Long  when close is below POC  AND |close-POC| / ATR < `poc_atr_buffer`.
    5. Short when close is above POC AND |close-POC| / ATR < `poc_atr_buffer`.

Public API:
    build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tod_calendar import session_label


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _vpvr_poc(close: pd.Series, volume: pd.Series, window: int, n_bins: int) -> pd.Series:
    close_arr = close.to_numpy(dtype=np.float64)
    vol_arr = volume.to_numpy(dtype=np.float64)
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


def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = df.copy()
    if "ts" in df.columns:
        df = df.set_index("ts")

    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    idx = df.index

    poc = _vpvr_poc(close, volume, params["vpvr_window_bars"], params["vpvr_bins"])
    atr = _atr(df, params["atr_period"])
    atr_safe = atr.replace(0, np.nan)

    sess = session_label(idx)
    filter_names = tuple(params.get("session_filter_names", ("london", "us")))
    in_session = sess.isin(filter_names)

    poc_safe = poc.replace([np.nan, np.inf], np.nan)
    distance_atr = (close - poc_safe).abs() / atr_safe

    poc_thr = params["poc_atr_buffer"]
    long_signal = in_session & (close < poc_safe) & (distance_atr <= poc_thr)
    short_signal = in_session & (close > poc_safe) & (distance_atr <= poc_thr)

    signal = pd.Series(0, index=close.index, dtype=np.int64)
    signal[long_signal] = 1
    signal[short_signal] = -1
    signal = signal.clip(-1, 1)

    return pd.DataFrame({
        "signal": signal,
        "session": sess,
        "in_session": in_session,
        "vpvr_poc": poc,
        "atr": atr,
        "poc_distance_atr": distance_atr,
    })
