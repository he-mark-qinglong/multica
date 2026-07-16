"""Signal builder for vpvr_stable_depeg_regime_4h_20260716.

Public API:
    build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame

Input columns:
    - ts (index or column): bar timestamp
    - open, high, low, close: OHLC
    - volume: traded volume
    - premium: stablecoin depeg premium (close-relative, e.g. 0.001 = 10 bps)

Output columns:
    - ts: timestamp
    - signal: -1 / 0 / +1
    - regime_ok: bool gate (True = premium below threshold)
    - vpvr_poc: point of control
    - atr: average true range
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


def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Build entry/exit signals for the stable-depeg VPVR reversion variant.

    Args:
        df: DataFrame with columns [open, high, low, close, volume, premium].
        params: Strategy parameters from config.json.

    Returns:
        DataFrame indexed by ts with signal and diagnostic columns.
    """
    df = df.copy()
    if "ts" in df.columns:
        df = df.set_index("ts")

    close = df["close"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    premium = df["premium"].astype(np.float64)

    poc = _vpvr_poc(close, volume, params["vpvr_window_bars"], params["vpvr_bins"])
    atr = _atr(df, params["atr_period"])
    regime_ok = premium < params["depeg_premium_threshold"]

    atr_safe = atr.replace(0, np.nan)
    poc_distance_atr = (close - poc).abs() / atr_safe

    poc_thr = params["poc_atr_buffer"]

    long_signal = regime_ok & (close < poc) & (poc_distance_atr <= poc_thr)
    short_signal = regime_ok & (close > poc) & (poc_distance_atr <= poc_thr)

    signal = pd.Series(0, index=close.index, dtype=np.int64)
    signal[long_signal] = 1
    signal[short_signal] = -1
    signal = signal.clip(-1, 1)

    return pd.DataFrame({
        "signal": signal,
        "regime_ok": regime_ok,
        "vpvr_poc": poc,
        "atr": atr,
        "premium": premium,
        "poc_distance_atr": poc_distance_atr,
    })
