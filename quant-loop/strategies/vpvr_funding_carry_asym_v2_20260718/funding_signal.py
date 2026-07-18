"""Funding-rate EMA signal for vpvr_funding_carry_asym_v2 (SMA-34990).

Computes an EMA of the funding rate on the 8h-event cadence, then
forwards the EMA onto a finer bar index (15m here) via ffill. The
threshold logic is a pure function so it can be unit-tested with
synthetic event series.

Public API
----------
``compute_funding_ema_signal(events_df, bar_index, *, span_events, threshold)``
    Returns a Series aligned to ``bar_index`` with columns-like tuple:
    a DataFrame with ``funding_ema`` (float) and ``above_threshold`` /
    ``below_threshold`` (bool) columns.

No-look-ahead
-------------
- The EMA is computed on the event cadence using only events
  strictly before each event's timestamp.
- ``shift(1)`` is applied after the ffill-onto-bar step so the
  per-bar EMA reflects the most recent past event (cycle-46).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


DEFAULT_SPAN_EVENTS: int = 21  # ~7d @ 8h events
DEFAULT_THRESHOLD: float = 0.0001  # 1 bp per 8h event


def compute_funding_ema_signal(
    events_df: pd.DataFrame,
    bar_index: pd.DatetimeIndex,
    *,
    span_events: int = DEFAULT_SPAN_EVENTS,
    threshold: float = DEFAULT_THRESHOLD,
    shift_bars: int = 1,
    funding_col: str = "fundingRate",
    event_index_col: Optional[str] = None,
) -> pd.DataFrame:
    """EMA of funding events reindexed onto bar_index.

    Args:
        events_df: one row per funding event. Must have a DatetimeIndex
            or a column named in ``event_index_col`` (or 'ts' /
            'fundingTime') of event timestamps, and a funding-rate
            column (``fundingRate`` by default).
        bar_index: target bar timestamps for the output Series.
        span_events: EMA span measured in events (21 ≈ 7d at 8h).
        threshold: absolute funding-EMA threshold (raw rate units).
        shift_bars: extra shift applied to the per-bar EMA for the
            standard cycle-46 no-look-ahead (shift 1 bar).
        funding_col: column name for the rate.
        event_index_col: optional explicit event index column.

    Returns:
        pd.DataFrame indexed by ``bar_index`` with columns:
          - ``funding``     (raw rate ffilled onto bar_index)
          - ``funding_ema`` (EMA of funding, shifted for no-look-ahead)
          - ``above_threshold`` (bool)
          - ``below_threshold`` (bool)
    """
    ev = events_df.copy()
    if not isinstance(ev.index, pd.DatetimeIndex):
        if event_index_col and event_index_col in ev.columns:
            ev[event_index_col] = pd.to_datetime(ev[event_index_col], utc=True)
            ev = ev.set_index(event_index_col)
        elif "ts" in ev.columns:
            ev["ts"] = pd.to_datetime(ev["ts"], utc=True)
            ev = ev.set_index("ts")
        elif "fundingTime" in ev.columns:
            ev["ts"] = pd.to_datetime(ev["fundingTime"], unit="ms", utc=True)
            ev = ev.set_index("ts")
        else:
            raise ValueError(
                "events_df must have a DatetimeIndex or a ts/fundingTime column"
            )
    ev = ev.sort_index()
    if ev.index.tz is not None:
        ev.index = ev.index.tz_convert(None)
    # Coerce bar_index to tz-naive UTC to match the event index after
    # the strip above (otherwise reindex fails on tz-aware vs tz-naive).
    if not isinstance(bar_index, pd.DatetimeIndex):
        bar_index = pd.DatetimeIndex(bar_index)
    if bar_index.tz is not None:
        bar_index = bar_index.tz_convert(None)

    if funding_col not in ev.columns:
        raise ValueError(f"events_df missing funding column {funding_col!r}")

    funding_events = ev[funding_col].astype(np.float64)

    # EMA on the EVENT cadence (cycle-46 — span measured in events).
    ema_event = funding_events.ewm(span=int(span_events), adjust=False).mean()

    # ffill the per-event EMA onto bar_index, then shift(1) for
    # no-look-ahead.
    ema_on_bar = ema_event.reindex(bar_index, method="ffill")
    ema_on_bar = ema_on_bar.shift(int(shift_bars))

    # Same ffill/shift for the raw funding rate (used for diagnostics).
    raw_on_bar = funding_events.reindex(bar_index, method="ffill")
    raw_on_bar = raw_on_bar.shift(int(shift_bars))

    return pd.DataFrame(
        {
            "funding": raw_on_bar,
            "funding_ema": ema_on_bar,
            "above_threshold": (ema_on_bar > float(threshold)),
            "below_threshold": (ema_on_bar < -float(threshold)),
        },
        index=bar_index,
    )


__all__ = [
    "DEFAULT_SPAN_EVENTS",
    "DEFAULT_THRESHOLD",
    "compute_funding_ema_signal",
]