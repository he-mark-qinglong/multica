"""4h EMA(50) slope trend filter for vpvr_funding_carry_asym_v2 (SMA-34990).

Computes the EMA(50) of close on a higher TF and the per-bar slope.
The slope direction is the trend filter: ENTER long only when
slope > 0, ENTER short only when slope < 0.

Public API
----------
``build_trend_filter(df_4h, *, ema_period, slope_period)``
    Returns a DataFrame indexed like ``df_4h`` with columns:
        ``ema``, ``slope``.

No-look-ahead
-------------
- EMA is computed with the standard pandas ``ewm(adjust=False)`` which
  uses only past closes.
- ``slope = ema.diff(slope_period)`` (positive slope ⇒ EMA rising).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_trend_filter(
    df: pd.DataFrame,
    *,
    ema_period: int = 50,
    slope_period: int = 1,
) -> pd.DataFrame:
    """Compute the trend filter (EMA + slope) on the given frame.

    Args:
        df: OHLCV frame with DatetimeIndex. Must include ``close``.
        ema_period: EMA span in bars.
        slope_period: bars over which to compute the slope
            (``ema.diff(slope_period)``).

    Returns:
        pd.DataFrame indexed like ``df`` with columns ``ema`` and
        ``slope`` (NaN until the EMA has warmed up).
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df must have a DatetimeIndex")
    df = df.sort_index()
    close = df["close"].astype(np.float64)
    ema = close.ewm(span=int(ema_period), adjust=False).mean()
    slope = ema.diff(int(slope_period))
    return pd.DataFrame({"ema": ema, "slope": slope}, index=df.index)


__all__ = ["build_trend_filter"]