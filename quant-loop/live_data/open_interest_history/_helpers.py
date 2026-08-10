"""Pure helpers for the open-interest backfill pipeline.

This module is **side-effect free** and contains only arithmetic / parsing
logic so the rest of the pipeline (network layer, parquet I/O) can be
unit-tested without ever touching the network or disk. The functions
exported here are imported by ``backfiller.py`` and ``manager.py``.

Migrated verbatim from ``trading/src/data/open_interest_history.py``
(``OIBackfiller`` / ``OpenInterestDataManager`` / helpers) at
``da0020de89575c0694b5763c0628a486612d6256``. The trading repo is
archived (``a80a927`` + ``4c052b2``); new work lives here.

Public API
----------
* :data:`SUPPORTED_PERIODS` / :data:`PERIOD_SECONDS` / :data:`MAX_ROWS_PER_CALL`
* :data:`LOOKBACK_UPPER_BOUND_SECONDS` / :data:`CHUNK_SAFETY_RATIO`
* :func:`parse_timestamp`
* :func:`chunk_seconds_for_period`
* :func:`windowed_iter` (yields :class:`Window`)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional, Union


# =============================================================================
# Public constants
# =============================================================================

#: Allowed Binance USDT-M OI history periods.
SUPPORTED_PERIODS: tuple = (
    "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d",
)

#: Seconds per period. Used for window math + clock-skew safety.
PERIOD_SECONDS: dict = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
    "6h": 6 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
}

#: Binance USDT-M maximum rows per ``fetchOpenInterestHistory`` call.
MAX_ROWS_PER_CALL: int = 500

#: How wide a single request's lookback can be, per period. The exchange
#: caps total returned rows at 500, so e.g. 5m = ~30 days.
#: These are conservative upper bounds; the actual chunk size we use in
#: :func:`windowed_iter` is ``min(MAX_ROWS_PER_CALL * period_seconds, value below)``.
LOOKBACK_UPPER_BOUND_SECONDS: dict = {
    "5m": 30 * 24 * 60 * 60,
    "15m": 30 * 24 * 60 * 60,
    "30m": 60 * 24 * 60 * 60,
    "1h": 90 * 24 * 60 * 60,
    "2h": 180 * 24 * 60 * 60,
    "4h": 365 * 24 * 60 * 60,
    "6h": 365 * 24 * 60 * 60,
    "12h": 365 * 24 * 60 * 60,
    "1d": 365 * 24 * 60 * 60,
}

#: Safety margin we shave off the *theoretical* max lookback. 0.83 ~= 5/6,
#: which leaves room for ~500-row batching plus clock skew.
CHUNK_SAFETY_RATIO: float = 0.83


# =============================================================================
# Helpers (pure / side-effect free — easy to unit test)
# =============================================================================

def parse_timestamp(value: Union[int, float, str, datetime, None]) -> Optional[int]:
    """Coerce ``value`` into a Unix-millisecond integer, or return ``None``.

    Accepts:
      - ``None``  -> ``None``
      - ``int`` / ``float`` already in milliseconds (>1e12) -> returned as-is
      - ``int`` / ``float`` already in seconds    -> scaled to milliseconds
      - ISO-8601 string  -> parsed as UTC
      - ``datetime``  -> treated as UTC

    Heuristic for numeric input:
      * values ``>= 1e12`` are treated as ms (>= year 2001 in seconds)
      * otherwise treated as seconds.

    The heuristic avoids accidentally treating a Unix-seconds timestamp
    as milliseconds and ending up 1000 years in the future.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)
    if isinstance(value, (int, float)):
        value = float(value)
        if value >= 1e12:
            return int(value)
        return int(value * 1000)
    if isinstance(value, str):
        # Try a straight int parse first (very common: "1700000000000")
        try:
            return parse_timestamp(int(value))
        except (TypeError, ValueError):
            pass
        # Then ISO-8601
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parse_timestamp(dt)
        except ValueError as exc:
            raise ValueError(f"Cannot parse timestamp: {value!r}") from exc
    raise TypeError(f"Unsupported timestamp type: {type(value).__name__}")


def _period_to_seconds(period: str) -> int:
    """Map a Binance period string to integer seconds, with a helpful error."""
    if period not in PERIOD_SECONDS:
        raise ValueError(
            f"Unsupported period {period!r}. "
            f"Expected one of {SUPPORTED_PERIODS}."
        )
    return PERIOD_SECONDS[period]


def chunk_seconds_for_period(period: str,
                             safety_ratio: float = CHUNK_SAFETY_RATIO) -> int:
    """Maximum seconds of history a *single* API call can return.

    Binance caps a request at 500 rows. For a 5m period that is ~30 days of
    data; for 1h it's ~500 hours. We shave ``safety_ratio`` off the
    theoretical max so callers don't accidentally request exactly at the
    boundary and miss a row.
    """
    if not 0 < safety_ratio <= 1:
        raise ValueError(f"safety_ratio must be in (0, 1], got {safety_ratio!r}")
    if period not in LOOKBACK_UPPER_BOUND_SECONDS:
        raise ValueError(
            f"Unsupported period {period!r}. "
            f"Expected one of {SUPPORTED_PERIODS}."
        )
    upper = LOOKBACK_UPPER_BOUND_SECONDS[period]
    rows_seconds = MAX_ROWS_PER_CALL * _period_to_seconds(period)
    return int(min(upper, rows_seconds) * safety_ratio)


# =============================================================================
# Window iterator (pure)
# =============================================================================

@dataclass(frozen=True)
class Window:
    """A half-open ``[since_ms, until_ms)`` backfill window."""
    since_ms: int
    until_ms: int

    @property
    def duration_seconds(self) -> int:
        return max(0, (self.until_ms - self.since_ms) // 1000)


def windowed_iter(start_ms: int,
                  end_ms: int,
                  period: str,
                  *,
                  safety_ratio: float = CHUNK_SAFETY_RATIO) -> Iterator[Window]:
    """Yield ``Window`` chunks covering ``[start_ms, end_ms)``.

    Each window's duration is at most :func:`chunk_seconds_for_period`.
    Windows are *backwards-growing*: we always pin ``until_ms`` to either
    ``end_ms`` or the next chunk boundary, never overshoot.

    The iterator is purely arithmetic; no I/O happens, so it is trivial
    to unit-test against.
    """
    if start_ms is None or end_ms is None:
        raise ValueError("start_ms and end_ms must be provided")
    if end_ms <= start_ms:
        raise ValueError(
            f"end_ms ({end_ms}) must be strictly greater than start_ms ({start_ms})"
        )

    chunk_ms = chunk_seconds_for_period(period, safety_ratio) * 1000
    cursor = end_ms
    while cursor > start_ms:
        since_ms = max(start_ms, cursor - chunk_ms)
        yield Window(since_ms=since_ms, until_ms=cursor)
        if since_ms == start_ms:
            return
        cursor = since_ms