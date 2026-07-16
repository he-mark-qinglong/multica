"""Signal builder for vpvr_options_putcall_oi_pressure_8h_20260715.

Public API:
    build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame

Input columns (df indexed by 8h bar ts):
    - open, high, low, close: OHLC
    - volume: bar volume
    - taker_buy_share: in [0, 1], buy-aggressive volume fraction (PCR proxy)

Output columns (df indexed by ts):
    - signal: -1 / 0 / +1
    - vpvr_poc: point-of-control from rolling volume profile
    - atr: ATR (period bars)
    - pcr_z: z-score of taker_buy_share over the rolling lookback
    - poc_distance_atr: |close - poc| / atr
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _vpvr_poc(close: pd.Series, volume: pd.Series, window: int, n_bins: int) -> pd.Series:
    """Rolling POC: price level with the highest traded volume in `window` bars."""
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


def _z_score(series: pd.Series, lookback: int) -> pd.Series:
    s = series.astype(np.float64)
    mean = s.rolling(lookback, min_periods=lookback).mean()
    std = s.rolling(lookback, min_periods=lookback).std(ddof=0)
    return (s - mean) / std.replace(0, np.nan)


def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Build entry/exit signals for the put-call OI pressure VPVR reversion variant.

    Args:
        df: DataFrame with columns [open, high, low, close, volume, taker_buy_share].
        params: Strategy parameters from config.json.

    Returns:
        DataFrame indexed by ts with signal + diagnostic columns.
    """
    df = df.copy()
    if "ts" in df.columns:
        df = df.set_index("ts")

    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    taker_buy_share = df["taker_buy_share"].astype(np.float64).clip(0.0, 1.0)

    poc = _vpvr_poc(close, volume, params["vpvr_window_bars"], params["vpvr_bins"])
    atr = _atr(df, params["atr_period"])
    pcr_z = _z_score(taker_buy_share, params["pcr_z_lookback_bars"])

    atr_safe = atr.replace(0, np.nan)
    poc_distance_atr = (close - poc).abs() / atr_safe

    z_thr = params["pcr_z_threshold"]
    poc_thr = params["poc_atr_buffer"]

    # Contrarian interpretation:
    #   pcr_z > +z_thr  -> call-side pressure extreme -> contrarian short
    #   pcr_z < -z_thr  -> put-side pressure extreme  -> contrarian long
    long_signal = (poc_distance_atr <= poc_thr) & (pcr_z < -z_thr)
    short_signal = (poc_distance_atr <= poc_thr) & (pcr_z > z_thr)

    signal = pd.Series(0, index=close.index, dtype=np.int64)
    signal[long_signal] = 1
    signal[short_signal] = -1
    signal = signal.clip(-1, 1)

    out = pd.DataFrame({
        "signal": signal,
        "vpvr_poc": poc,
        "atr": atr,
        "pcr_z": pcr_z,
        "poc_distance_atr": poc_distance_atr,
        "taker_buy_share": taker_buy_share,
    })
    out.index.name = "ts"
    return out
