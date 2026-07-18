"""Indicators for vol_breakout_2tf_vpvr_confluence_4h_20260712 (iter#84, single-TF 4h).

V8 single-TF design
-------------------

V8 runs ENTIRELY on the 4h frame (the parent V1 used 4h as a coarse filter
and 1h as the fine entry/exit clock). Single-TF means there is no merge_asof,
no 1h clock — every indicator below is computed on the same 4h bars that
the backtest loop iterates over.

Bar-count constant (single source of truth):

    BARS_PER_YEAR_4H = 2190  (24/7 crypto, 365.25 * 6 = 2191.5, rounded down)

Indicator set (4h, all on the same frame)
----------------------------------------

- ``realized_vol_4h(N=20)``           rolling std of 4h log returns, 20-bar window
- ``vol_median_4h(M=120)``            rolling median of realized_vol over 120 4h bars
- ``vol_regime_4h``                   realized_vol / vol_median
- ``ATR_4h(14)``                      Wilder ATR over 14 4h bars
- ``vpvr_poc_4h(80, 24 bins)``        rolling 80-bar (~13d) window with 24 price bins
- ``vpvr_dist_atr_4h``                |close_4h - vpvr_poc_4h| / ATR_4h(14)
- ``range_high_4h(20)``               Donchian upper, shift(1) (no look-ahead)
- ``range_low_4h(20)``                Donchian lower, shift(1)

All indicator functions are pure: they take an OHLCV frame and return a
``pd.Series`` aligned to the same index. No I/O, no global state.

Look-ahead discipline: every function that "uses the prior N bars" either
relies on ``pd.rolling``'s trailing window or applies a ``shift(1)`` so the
value at bar ``t`` is computed from bars ``[t-W, t-1]``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"

# ---------------------------------------------------------------------------
# Constants — single source of truth.
# ---------------------------------------------------------------------------

# 24/7 crypto. 365.25 days * 6 four-hour bars/day = 2191.5. Rounded down to
# 2190 per spec. Used by the vol-target sizing formula on the 4h frame:
#
#     size_units = 0.10 * NAV / (close_4h * realized_vol_4h(20)
#                                * sqrt(BARS_PER_YEAR_4H))
#
# sqrt(2190) ≈ 46.818.
BARS_PER_YEAR_4H: int = 2190


# ---------------------------------------------------------------------------
# Helpers — config-driven wrappers.
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Read the strategy config. We re-read on every call to keep this
    module test-friendly (tests can monkey-patch ``CONFIG_PATH``)."""
    return json.loads(CONFIG_PATH.read_text())


# ---------------------------------------------------------------------------
# Realized vol / vol regime.
# ---------------------------------------------------------------------------

def realized_vol(df: pd.DataFrame, n: int) -> pd.Series:
    """Rolling std of log returns over the prior ``n`` bars, then ``shift(1)``.

    Output is shifted by 1 so the value at bar ``t`` reflects returns up to
    and including bar ``t-1`` — never bar ``t`` itself.
    """
    log_close = np.log(df["close"])
    log_ret = log_close.diff()
    return log_ret.rolling(window=n, min_periods=n).std().shift(1)


def vol_median(df: pd.DataFrame, n: int, rv_n: int) -> pd.Series:
    """Rolling median of ``realized_vol(rv_n)`` over the prior ``n`` bars."""
    rv = realized_vol(df, rv_n)
    return rv.rolling(window=n, min_periods=n).median()


def vol_regime(df: pd.DataFrame, rv_n: int, med_n: int) -> pd.Series:
    """``realized_vol / vol_median``.

    Values > 1.0 indicate realized vol is expanding (above the recent
    median); values < 1.0 indicate vol contraction.
    """
    rv = realized_vol(df, rv_n)
    med = vol_median(df, med_n, rv_n)
    return rv / med


# ---------------------------------------------------------------------------
# ATR — Wilder smoothing.
# ---------------------------------------------------------------------------

def true_range(df: pd.DataFrame) -> pd.Series:
    """True range series. The first bar's true range degenerates to
    ``high - low`` because there is no prior close."""
    prev_close = df["close"].shift(1)
    hi_lo = df["high"] - df["low"]
    hi_pc = (df["high"] - prev_close).abs()
    lo_pc = (df["low"] - prev_close).abs()
    return pd.concat([hi_lo, hi_pc, lo_pc], axis=1).max(axis=1)


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder ATR. The first ``period`` bars are NaN because the smoothing
    window needs ``period`` bars to seed."""
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------------
# VPVR POC — Volume Profile Visible Range, Point of Control.
# ---------------------------------------------------------------------------

def _price_bin_edges(lo: float, hi: float, n_bins: int) -> np.ndarray:
    """Return ``n_bins + 1`` edges from ``lo`` to ``hi``. Bins are equal-width."""
    return np.linspace(lo, hi, n_bins + 1)


def vpvr_poc(
    df: pd.DataFrame,
    window: int,
    n_bins: int,
) -> pd.Series:
    """Rolling Volume Profile Visible Range, Point of Control.

    For each bar ``t``, compute the POC over bars ``[t-window, t-1]``:

        - bin edges = ``linspace(window_low, window_high, n_bins + 1)``
        - bucket each bar's volume into the bin containing its ``close``
        - POC = price level (midpoint) of the bin with the highest
          cumulative volume

    Output is the price level (a ``float``) at the POC for each bar, with
    ``shift(1)`` so the value at ``t`` reflects bars ``[t-window, t-1]`` —
    **no look-ahead**.
    """
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values
    n = len(df)
    out = np.full(n, np.nan, dtype=float)

    for t in range(window, n):
        win_lo = float(np.nanmin(low[t - window:t]))
        win_hi = float(np.nanmax(high[t - window:t]))
        if not np.isfinite(win_lo) or not np.isfinite(win_hi) or win_hi <= win_lo:
            continue
        edges = _price_bin_edges(win_lo, win_hi, n_bins)
        bin_idx = np.clip(
            np.searchsorted(edges, close[t - window:t], side="right") - 1,
            0,
            n_bins - 1,
        )
        bin_vol = np.bincount(bin_idx, weights=volume[t - window:t], minlength=n_bins)
        poc_bin = int(np.argmax(bin_vol))
        out[t] = 0.5 * (edges[poc_bin] + edges[poc_bin + 1])
    return pd.Series(out, index=df.index, name="vpvr_poc").shift(1)


def vpvr_dist_atr(
    df: pd.DataFrame,
    poc: pd.Series,
    atr: pd.Series,
) -> pd.Series:
    """Normalized distance from ``close`` to POC, in ATR units.

    ``|close - poc| / ATR``. Used by V8 as the confluence filter: enter only
    when ``vpvr_dist_atr_4h <= proximity_atr_k`` (default 0.6).
    """
    return (df["close"] - poc).abs() / atr


# ---------------------------------------------------------------------------
# Donchian range — for breakout entry and trend-fail exit.
# ---------------------------------------------------------------------------

def range_high(df: pd.DataFrame, n: int) -> pd.Series:
    """Max close over the prior ``n`` bars, shifted by 1.

    At bar ``t``, ``range_high[t] = max(close[t-n : t-1])``. Used as the
    breakout trigger: long when ``close[t] > range_high[t]``.
    """
    return df["close"].rolling(window=n, min_periods=n).max().shift(1)


def range_low(df: pd.DataFrame, n: int) -> pd.Series:
    """Min close over the prior ``n`` bars, shifted by 1.

    Used as a trend-fail exit: long closes when ``close[t] < range_low[t]``.
    """
    return df["close"].rolling(window=n, min_periods=n).min().shift(1)


# ---------------------------------------------------------------------------
# Annotated 4h frame — single-TF; no merge_asof.
# ---------------------------------------------------------------------------

def annotate_4h(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Annotate the 4h frame with every indicator V8 needs and compute
    ``long_entry``.

    Output columns (4h OHLCV + 4h indicators):

        open, high, low, close, volume,
        realized_vol_4h, vol_median_4h, vol_regime_4h,
        atr_4h, vpvr_poc_4h, vpvr_dist_atr_4h,
        range_high_4h, range_low_4h,
        long_entry

    Entry rule (V8):
        long_break       = close_4h > range_high_4h
        regime_expanding = vol_regime_4h > vol_regime_min  (1.2)
        poc_confluence   = vpvr_dist_atr_4h <= proximity_atr_k  (0.6)
        long_entry       = long_break & regime_expanding & poc_confluence
    """
    ind = cfg["indicators_4h"]
    out = df.copy()
    out["realized_vol_4h"] = realized_vol(out, ind["realized_vol_n"])
    out["vol_median_4h"] = vol_median(out, ind["vol_median_m"], ind["realized_vol_n"])
    out["vol_regime_4h"] = vol_regime(out, ind["realized_vol_n"], ind["vol_median_m"])
    out["atr_4h"] = wilder_atr(out, ind["atr_period"])
    out["vpvr_poc_4h"] = vpvr_poc(out, ind["vpvr_window_bars"], ind["vpvr_bins"])
    out["vpvr_dist_atr_4h"] = vpvr_dist_atr(out, out["vpvr_poc_4h"], out["atr_4h"])
    out["range_high_4h"] = range_high(out, ind["range_n"])
    out["range_low_4h"] = range_low(out, ind["range_n"])

    entry_cfg = cfg["entry"]
    have_4h = (
        out["range_high_4h"].notna()
        & out["range_low_4h"].notna()
        & out["vol_regime_4h"].notna()
        & out["vpvr_dist_atr_4h"].notna()
    )
    long_break = out["close"] > out["range_high_4h"]
    regime_expanding = out["vol_regime_4h"] > ind["vol_regime_min"]
    poc_confluence = out["vpvr_dist_atr_4h"] <= ind["proximity_atr_k"]

    out["long_entry"] = (
        long_break
        & regime_expanding
        & poc_confluence
        & have_4h
    )
    return out


def indicator_columns(cfg: dict) -> Dict[str, str]:
    """Return the canonical column-name mapping for V8."""
    return {
        "range_high": "range_high_4h",
        "range_low": "range_low_4h",
        "atr": "atr_4h",
        "realized_vol": "realized_vol_4h",
        "realized_vol_4h": "realized_vol_4h",
        "vol_median_4h": "vol_median_4h",
        "vol_regime_4h": "vol_regime_4h",
        "atr_4h": "atr_4h",
        "vpvr_poc_4h": "vpvr_poc_4h",
        "vpvr_dist_atr_4h": "vpvr_dist_atr_4h",
        "long_entry": "long_entry",
    }