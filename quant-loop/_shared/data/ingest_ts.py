"""Dual-timestamp stamping for persisted market data (F-Data).

Every persisted row carries two timestamps:

  - ``timestamp`` / ``open_time`` — the *exchange* time of the event
    (when the bar opened / the trade printed on the matching engine).
  - ``ingest_ts`` — the *local* wall-clock time at which the row was
    durably written to storage (when our pipeline first persisted it).

The gap ``ingest_ts − timestamp`` is the end-to-end pipeline latency for
that observation — the primary signal for degraded feeds, stuck buffers
and exchange↔ingester clock skew. Without it a silently-stalled websocket
looks identical to a live one (the bars just stop arriving); with it the
growing latency is immediately visible.

Design:
  - :func:`stamp_ingest_ts`  pure, non-destructive: add the column to a
                             frame (idempotent — skips if already present).
  - :func:`has_ingest_ts`    schema probe.
  - :func:`latency_ms`       pure: the latency series for diagnostics.
  - :func:`stamp_parquet`    stamp a parquet file in place (backfill old
                             stores that predate the dual-timestamp era).

References:
  - "Event time vs processing time" — Kleppmann, DDIA ch. 11; the
    ingestion lag is the processing-time/event-time skew that silently
    breaks windowed joins if left unmeasured.
  - Binance websocket ``E`` (event time) vs ``T`` (trade time): the
    exchange already emits both; ``ingest_ts`` extends the pair to the
    storage layer so latency is measurable end-to-end.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Union

import pandas as pd

PathLike = Union[str, Path]

#: Canonical column name for the local ingestion timestamp (int64 ms).
INGEST_COL = "ingest_ts"


def stamp_ingest_ts(
    df: pd.DataFrame,
    ts_col: str = "timestamp",
    ingest_col: str = INGEST_COL,
    now_ms: Optional[int] = None,
) -> pd.DataFrame:
    """Return a copy of *df* with an ``ingest_col`` (int64 ms) column.

    Pure & non-destructive: never mutates the input. If the column
    already exists it is left untouched (idempotent — safe to call on a
    frame that was partially stamped by an earlier write path, and safe
    to re-run after a crash-recovery re-flush).

    ``now_ms`` defaults to the current wall-clock time. For an empty
    frame the column is created (int64, empty) so downstream schema
    checks see a consistent shape.
    """
    if ingest_col in df.columns:
        return df.copy()
    out = df.copy()
    if len(out) == 0:
        out[ingest_col] = pd.Series(dtype="int64")
        return out
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    out[ingest_col] = int(now_ms)
    return out


def has_ingest_ts(df: pd.DataFrame, ingest_col: str = INGEST_COL) -> bool:
    """True when *df* carries a non-empty ingestion-timestamp column."""
    return ingest_col in df.columns and len(df) > 0


def latency_ms(
    df: pd.DataFrame,
    ts_col: str = "timestamp",
    ingest_col: str = INGEST_COL,
) -> pd.Series:
    """Pure: end-to-end pipeline latency ``ingest_ts − timestamp`` (ms).

    Returns an empty int64 Series when either column is absent or the
    frame is empty. Negative values (clock skew: ingest stamped before
    the exchange timestamp) are preserved so they are visible rather
    than silently clipped — a negative latency is itself a red flag.
    """
    if len(df) == 0 or ts_col not in df.columns or ingest_col not in df.columns:
        return pd.Series(dtype="int64")
    ts = df[ts_col]
    if pd.api.types.is_datetime64_any_dtype(ts):
        # resolution-agnostic ms (handles [ms]/[us]/[ns] + tz-aware),
        # same idiom as quality._ts_to_ms
        ts = (
            (pd.to_datetime(ts, utc=True) - pd.Timestamp("1970-01-01", tz="UTC"))
            // pd.Timedelta(milliseconds=1)
        ).astype("int64")
    else:
        ts = ts.astype("int64")
    return (df[ingest_col].astype("int64") - ts).astype("int64")


def stamp_parquet(
    path: PathLike,
    ts_col: str = "timestamp",
    ingest_col: str = INGEST_COL,
    now_ms: Optional[int] = None,
) -> int:
    """Backfill ``ingest_col`` onto an existing parquet store in place.

    Old stores that predate dual-timestamping get a single ``ingest_ts``
    set to ``now_ms`` (default: current wall-clock) — an honest "we don't
    know the original ingest moment, but we know when we audited it"
    sentinel, rather than a fabricated historical value. Returns the
    number of rows stamped (0 if the file was absent or already stamped).
    """
    p = Path(path)
    if not p.exists():
        return 0
    df = pd.read_parquet(p)
    if ingest_col in df.columns and df[ingest_col].notna().all():
        return 0  # already fully stamped
    stamped = stamp_ingest_ts(df, ts_col=ts_col, ingest_col=ingest_col, now_ms=now_ms)
    stamped.to_parquet(p, index=False)
    return len(stamped)
