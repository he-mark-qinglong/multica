"""Signal builder for vpvr_funding_carry_asym_v2 (SMA-34990).

Combines the three per-TF inputs into a single per-1m-bar decision:

  - 15m funding EMA(7d) signal (above/below threshold).
  - 15m VPVR VAH/VAL band half (lower/upper).
  - 4h EMA(50) slope (positive/negative).

The decision is:
  - long  iff funding_above AND price_in_lower_half AND 4h_slope > 0
  - short iff funding_below AND price_in_upper_half AND 4h_slope < 0
  - 0    otherwise

Public API
----------
``build_signals(df_1m, df_15m, df_4h, funding_events, params)`` →
    pd.DataFrame indexed like ``df_1m`` with columns:
        ``decision``     (int, {-1, 0, +1})
        ``funding_ema``  (float)
        ``funding_above``, ``funding_below`` (bool)
        ``vah``, ``val``, ``midpoint``, ``half`` (from 15m VPVR)
        ``ema_4h``, ``slope_4h`` (from 4h trend filter)
        ``atr_1m``       (ATR(14) on the 1m bars)
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from funding_signal import compute_funding_ema_signal
from trend_filter import build_trend_filter
from vpvr_levels_band import build_vpvr_band


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Cycle-46 ATR with close.shift(1) so today's range cannot leak."""
    h = high.astype(np.float64)
    l = low.astype(np.float64)
    c = close.astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean().rename("atr")


def _align_to_1m(df_1m: pd.DataFrame, df_other: pd.DataFrame) -> pd.DataFrame:
    """Reindex another-TF frame onto the 1m index with ffill.

    Both frames must carry a ``DatetimeIndex`` in the same tz state.
    The data_loader strips tz on load so by convention both indexes
    are tz-naive UTC; if a caller hands us a tz-aware frame we coerce
    it to match the 1m index.
    """
    if df_1m.index.tz is None and df_other.index.tz is not None:
        df_other = df_other.copy()
        df_other.index = df_other.index.tz_convert(None)
    elif df_1m.index.tz is not None and df_other.index.tz is None:
        df_other = df_other.copy()
        df_other.index = df_other.index.tz_localize("UTC")
    aligned = df_other.reindex(df_1m.index, method="ffill")
    return aligned


def build_signals(
    df_1m: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_4h: pd.DataFrame,
    funding_events: pd.DataFrame,
    params: dict,
) -> pd.DataFrame:
    """Combine 1m / 15m / 4h + funding into a per-1m-bar decision.

    Args:
        df_1m: 1m OHLCV frame with DatetimeIndex. Must include ``close``.
        df_15m: 15m OHLCV frame with DatetimeIndex. Must include ``high``,
            ``low``, ``close``, ``volume``.
        df_4h: 4h OHLCV frame with DatetimeIndex. Must include ``close``.
        funding_events: funding events (per-event DataFrame with
            ``fundingRate`` and a DatetimeIndex).
        params: dict (see config.json).

    Returns:
        pd.DataFrame indexed like ``df_1m`` with the columns described
        in the module docstring.
    """
    if not isinstance(df_1m.index, pd.DatetimeIndex):
        raise ValueError("df_1m must have a DatetimeIndex")
    df_1m = df_1m.sort_index()

    # 1m ATR (cycle-46 convention).
    atr_1m = _atr(
        df_1m["high"], df_1m["low"], df_1m["close"],
        int(params.get("atr_period_1m", 14)),
    )

    # 15m VPVR band.
    band_15m = build_vpvr_band(
        df_15m,
        window_bars=int(params.get("vpvr_window_bars_15m", 180)),
        snapshot_every_bars=int(params.get("vpvr_snapshot_every_bars_15m", 16)),
        num_bins=int(params.get("vpvr_bins_15m", 24)),
        value_area_fraction=float(params.get("vpvr_value_area_fraction", 0.70)),
    )
    band_1m = _align_to_1m(df_1m, band_15m)

    # Funding EMA(7d) on 15m cadence.
    fund_15m = compute_funding_ema_signal(
        funding_events,
        df_15m.index,
        span_events=int(params.get("funding_ema_span_events", 21)),
        threshold=float(params.get("funding_threshold", 0.0001)),
        shift_bars=int(params.get("funding_shift_bars", 1)),
    )
    fund_1m = _align_to_1m(df_1m, fund_15m)

    # 4h trend filter.
    trend_4h = build_trend_filter(
        df_4h,
        ema_period=int(params.get("ema50_period_4h", 50)),
        slope_period=int(params.get("trend_filter_slope_period_4h", 1)),
    )
    trend_1m = _align_to_1m(df_1m, trend_4h)

    funding_above = fund_1m["above_threshold"].fillna(False).astype(bool)
    funding_below = fund_1m["below_threshold"].fillna(False).astype(bool)
    half = band_1m["half"].astype(object)
    slope_4h = trend_1m["slope"].astype(np.float64)

    decision = pd.Series(0, index=df_1m.index, dtype=np.int64)
    long_mask = funding_above & half.eq("lower") & (slope_4h > 0)
    short_mask = funding_below & half.eq("upper") & (slope_4h < 0)
    decision = decision.mask(long_mask, 1)
    decision = decision.mask(short_mask, -1)

    return pd.DataFrame(
        {
            "decision": decision,
            "funding_ema": fund_1m["funding_ema"].astype(np.float64),
            "funding_above": funding_above,
            "funding_below": funding_below,
            "vah": band_1m["vah"].astype(np.float64),
            "val": band_1m["val"].astype(np.float64),
            "midpoint": band_1m["midpoint"].astype(np.float64),
            "half": half,
            "ema_4h": trend_1m["ema"].astype(np.float64),
            "slope_4h": slope_4h,
            "atr_1m": atr_1m.astype(np.float64),
        },
        index=df_1m.index,
    )


__all__ = ["build_signals"]