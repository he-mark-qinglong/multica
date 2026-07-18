"""Session-of-day calendar for vpvr_tod_session_filter_15m_20260715.

Defines the asia / london / us sessions used to gate entry signals. Each
session is 8 hours, anchored to UTC (the canonical Binance USD-M kline
timezone). The supported session set matches the strategy manifest
tag `SESSION-OF-DAY-EMBEDDED session=asia_london_us`.

Public API:
    SESSION_WINDOWS          dict {name: (start_hour_utc, end_hour_utc)}
    session_for_timestamp(ts) -> str
    is_session_active(ts, names=("asia","london","us")) -> bool
    session_index_for_day(ts) -> int   (0=asia,1=london,2=us)
    all_session_change_points(idx) -> pd.Series[int] (1 at first bar of each session per day)
"""

from __future__ import annotations

from typing import Tuple

import pandas as pd


SESSION_WINDOWS: dict = {
    # 24h UTC: asia 00-08, london 08-16, us 16-24.
    "asia": (0, 8),
    "london": (8, 16),
    "us": (16, 24),
}


def session_for_timestamp(ts: pd.Timestamp) -> str:
    """Return session name for a UTC timestamp."""
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts)
    h = ts.hour
    if 0 <= h < 8:
        return "asia"
    if 8 <= h < 16:
        return "london"
    return "us"


def is_session_active(ts: pd.Timestamp, names: Tuple[str, ...] = ("asia", "london", "us")) -> bool:
    return session_for_timestamp(ts) in names


def session_index_for_day(ts: pd.Timestamp) -> int:
    s = session_for_timestamp(ts)
    return {"asia": 0, "london": 1, "us": 2}[s]


def session_label(idx: pd.DatetimeIndex) -> pd.Series:
    """Vectorised session label for an index."""
    h = pd.Series(idx.hour, index=idx)
    labels = pd.Series("asia", index=idx, dtype="object")
    labels[h >= 8] = "london"
    labels[h >= 16] = "us"
    return labels


def session_change_points(idx: pd.DatetimeIndex) -> pd.Series:
    """1 at the first bar of each new session-per-day (00:00, 08:00, 16:00 UTC)."""
    h = pd.Series(idx.hour, index=idx)
    flag = pd.Series(0, index=idx, dtype=int)
    flag[(h == 0) | (h == 8) | (h == 16)] = 1
    return flag
