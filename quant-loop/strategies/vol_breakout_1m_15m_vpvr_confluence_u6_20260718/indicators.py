"""Indicators for U6 vol_breakout_1m_15m_vpvr_confluence (TF-dependent).

Single-TF design: bar is the native TF (15m or 1m). All indicators live
on that bar's frame — no merge_asof.

Look-ahead discipline (matches iter#84):

  - realized_vol / vol_median / vol_regime : rolling on log returns,
    ``shift(1)``
  - ATR (Wilder)                            : rolling TR with prior close
  - VPVR POC                                : rolling volume bins,
    ``shift(1)`` so the value at bar ``t`` reflects bars ``[t-W, t-1]``
  - Donchian range_high / range_low         : rolling max/min + ``shift(1)``

VPVR is computed on a snapshot grid (every ``vpvr_snapshot_every_bars``
bars) and forward-filled, then ``shift(1)``. For 1m/15m with VPVR
windows of thousands of bars, the snapshot pattern keeps the run
under ~minutes instead of hours, matching the LOID+VPVR harness
convention (SMA-34802).
"""
from __future__ import annotations

import json
import math
from typing import Dict, Optional

import numpy as np
import pandas as pd

CONFIG_PATH = None  # set lazily so this module is test-importable

BARS_PER_YEAR: Dict[str, int] = {
    "1m": 60 * 24 * 365,  # 525 600
    "15m": 4 * 24 * 365,  # 35 040
    "4h": 6 * 365,         # 2 190 (kept for cross-check)
}


def _cfg() -> dict:
    if CONFIG_PATH is None:
        from pathlib import Path
        return json.loads((Path(__file__).parent / "config.json").read_text())
    return json.loads(CONFIG_PATH.read_text())


def sqrt_bars_per_year(tf: str) -> float:
    return math.sqrt(BARS_PER_YEAR[tf])


# ---------------------------------------------------------------------------
# Realized vol / regime — pure functions, shift(1) only.
# ---------------------------------------------------------------------------

def realized_vol(df: pd.DataFrame, n: int) -> pd.Series:
    log_close = np.log(df["close"])
    log_ret = log_close.diff()
    return log_ret.rolling(window=n, min_periods=n).std().shift(1)


def vol_median(df: pd.DataFrame, n: int, rv_n: int) -> pd.Series:
    rv = realized_vol(df, rv_n)
    return rv.rolling(window=n, min_periods=n).median()


def vol_regime(df: pd.DataFrame, rv_n: int, med_n: int) -> pd.Series:
    rv = realized_vol(df, rv_n)
    med = vol_median(df, med_n, rv_n)
    return rv / med


# ---------------------------------------------------------------------------
# ATR — Wilder smoothing.
# ---------------------------------------------------------------------------

def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    hi_lo = df["high"] - df["low"]
    hi_pc = (df["high"] - prev_close).abs()
    lo_pc = (df["low"] - prev_close).abs()
    tr = pd.concat([hi_lo, hi_pc, lo_pc], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------------
# Donchian range — breakout entry / trend-fail exit.
# ---------------------------------------------------------------------------

def range_high(df: pd.DataFrame, n: int) -> pd.Series:
    return df["close"].rolling(window=n, min_periods=n).max().shift(1)


def range_low(df: pd.DataFrame, n: int) -> pd.Series:
    return df["close"].rolling(window=n, min_periods=n).min().shift(1)


# ---------------------------------------------------------------------------
# VPVR POC on snapshot grid + forward-fill, then shift(1).
# ---------------------------------------------------------------------------

def _price_bin_edges(lo: float, hi: float, n_bins: int) -> np.ndarray:
    if hi <= lo:
        return np.linspace(lo, lo + 1e-9, n_bins + 1)
    return np.linspace(lo, hi, n_bins + 1)


def _vpvr_poc_at(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    end: int,
    window: int,
    n_bins: int,
) -> float:
    start = max(0, end - window)
    win_lo = float(np.nanmin(low[start:end]))
    win_hi = float(np.nanmax(high[start:end]))
    if not np.isfinite(win_lo) or not np.isfinite(win_hi) or win_hi <= win_lo:
        return float("nan")
    edges = _price_bin_edges(win_lo, win_hi, n_bins)
    bin_idx = np.clip(
        np.searchsorted(edges, close[start:end], side="right") - 1,
        0,
        n_bins - 1,
    )
    bin_vol = np.bincount(bin_idx, weights=volume[start:end], minlength=n_bins)
    poc_bin = int(np.argmax(bin_vol))
    return 0.5 * (edges[poc_bin] + edges[poc_bin + 1])


def vpvr_poc_snapshot(
    df: pd.DataFrame,
    window: int,
    n_bins: int,
    snapshot_every: int,
) -> pd.Series:
    """Compute VPVR POC on a snapshot grid, ffill to per-bar cadence,
    then ``shift(1)``.

    Inner-loop Python for clarity + correctness on a 30-day window. For
    the 1m/15m configs in this strategy (~3k to ~43k bars) a snapshot
    grid every ``snapshot_every`` bars keeps wall-time modest while
    preserving the bias-vs-variance trade-off the LOID harness uses.
    """
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values
    n = len(df)
    snap_idx = list(range(window, n, max(1, snapshot_every)))
    if snap_idx[-1] != n - 1:
        snap_idx.append(n - 1)
    poc_vals = np.full(len(snap_idx), np.nan)
    for k, t in enumerate(snap_idx):
        poc_vals[k] = _vpvr_poc_at(close, high, low, volume, t, window, n_bins)
    snap_index = df.index[snap_idx]
    snap_series = pd.Series(poc_vals, index=snap_index, name="vpvr_poc")
    per_bar = snap_series.reindex(df.index).ffill()
    return per_bar.shift(1)


# ---------------------------------------------------------------------------
# Annotated frame — TF-parameterised.
# ---------------------------------------------------------------------------

def annotate(df: pd.DataFrame, tf: str, cfg: dict) -> pd.DataFrame:
    """Annotate a single-TF frame with the TF's indicators and ``long_entry``.

    ``cfg["indicators_<tf>"]`` selects the parameter block.
    """
    ind = cfg[f"indicators_{tf}"]
    out = df.copy()
    out["realized_vol"] = realized_vol(out, ind["realized_vol_n"])
    out["vol_median"] = vol_median(out, ind["vol_median_m"], ind["realized_vol_n"])
    out["vol_regime"] = vol_regime(out, ind["realized_vol_n"], ind["vol_median_m"])
    out["atr"] = wilder_atr(out, ind["atr_period"])
    out["vpvr_poc"] = vpvr_poc_snapshot(
        out,
        window=ind["vpvr_window_bars"],
        n_bins=ind["vpvr_bins"],
        snapshot_every=ind["vpvr_snapshot_every_bars"],
    )
    out["vpvr_dist_atr"] = (out["close"] - out["vpvr_poc"]).abs() / out["atr"]
    out["range_high"] = range_high(out, ind["range_n"])
    out["range_low"] = range_low(out, ind["range_n"])

    have = (
        out["range_high"].notna()
        & out["range_low"].notna()
        & out["vol_regime"].notna()
        & out["vpvr_dist_atr"].notna()
    )
    long_break = out["close"] > out["range_high"]
    regime_exp = out["vol_regime"] > ind["vol_regime_min"]
    poc_conf = out["vpvr_dist_atr"] <= ind["proximity_atr_k"]
    out["long_entry"] = long_break & regime_exp & poc_conf & have
    return out
