"""Signal builder for vpvr_defi_basis_15m_hyperliquid_dydx_20260716.

Public API:
    build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame

Input columns:
    - ts (index or column): bar timestamp
    - open, high, low, close: OHLC
    - volume: traded volume
    - basis: DeFi-CEX combined basis (close-relative, e.g. 0.001 = 10 bps)

Output columns:
    - ts: timestamp
    - signal: -1 / 0 / +1
    - vpvr_poc: point of control
    - atr: average true range
    - basis_z: z-score of DeFi-CEX basis
    - poc_distance_atr: distance from close to POC in ATR multiples
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
    """Build entry/exit signals for the DeFi basis VPVR reversion variant.

    Args:
        df: DataFrame with columns [open, high, low, close, volume, basis].
        params: Strategy parameters from config.json.

    Returns:
        DataFrame indexed by ts with signal and diagnostic columns.
    """
    df = df.copy()
    if "ts" in df.columns:
        df = df.set_index("ts")

    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    basis = df["basis"].astype(np.float64)

    poc = _vpvr_poc(close, volume, params["vpvr_window_bars"], params["vpvr_bins"])
    atr = _atr(df, params["atr_period"])
    basis_z = _z_score(basis, params["basis_z_lookback_bars"])

    atr_safe = atr.replace(0, np.nan)
    poc_distance_atr = (close - poc).abs() / atr_safe

    z_thr = params["basis_z_threshold"]
    poc_thr = params["poc_atr_buffer"]

    long_signal = (poc_distance_atr <= poc_thr) & (basis_z < -z_thr)
    short_signal = (poc_distance_atr <= poc_thr) & (basis_z > z_thr)

    signal = pd.Series(0, index=close.index, dtype=np.int64)
    signal[long_signal] = 1
    signal[short_signal] = -1

    # Flatten overlapping signals (shouldn't happen by construction, but safety).
    signal = signal.clip(-1, 1)

    out = pd.DataFrame({
        "signal": signal,
        "vpvr_poc": poc,
        "atr": atr,
        "basis_z": basis_z,
        "poc_distance_atr": poc_distance_atr,
    })
    out.index.name = "ts"
    return out
