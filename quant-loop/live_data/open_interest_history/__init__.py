"""Open-interest history backfill package.

Backfills historical open interest (OI) for perpetual swap symbols from
Binance USDT-margined futures (and OKX USDT-SWAP swaps) into local
parquet files for offline analysis, factor engineering, and live-trading
prep.

Why parquet?
  - Compressed (zstd) columnar storage keeps the on-disk footprint small
    even for years of 5m OI (~10M+ rows for BTC).
  - Native pandas / polars / arrow read path (no server round-trips).

Why intervals matter:
  - Binance USDT-M ``openInterestHist`` accepts period in
    {5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d} and max 500 rows per call.
  - For period=5m the per-request lookback is bounded at ~30 days, so we
    chunk long backfills into ~25-day windows to leave slack for clock
    skew between us and the exchange.

Design choices:
  - Idempotent: existing parquet files are appended/merged (dedupe on
    timestamp), so re-runs are safe.
  - Stateless: no global state; one ``OIBackfiller`` instance per task.
  - Local-mode helpers (``parse_timestamp``, ``chunk_seconds_for_period``,
    ``windowed_iter``, etc.) are pure / side-effect free so the logic can
    be unit-tested without ever touching the network.

Public API
----------
  OIBackfiller              - main class; paginates and writes parquet.
  OpenInterestDataManager   - thin wrapper to round-trip the parquet files.

CLI entry point
---------------
  ``python -m live_data.open_interest_history --help``.

Migration provenance
--------------------
Migrated verbatim from the (archived) ``trading`` repo at
``da0020de89575c0694b5763c0628a486612d6256``. The trading repo is
archived (``a80a927`` + ``4c052b2``); new work lives here.
"""

from ._helpers import (
    CHUNK_SAFETY_RATIO,
    LOOKBACK_UPPER_BOUND_SECONDS,
    MAX_ROWS_PER_CALL,
    PERIOD_SECONDS,
    SUPPORTED_PERIODS,
    Window,
    chunk_seconds_for_period,
    parse_timestamp,
    windowed_iter,
)
from .backfiller import OIBackfiller
from .manager import OpenInterestDataManager

__all__ = [
    # Network layer
    "OIBackfiller",
    # Parquet I/O
    "OpenInterestDataManager",
    # Pure helpers
    "parse_timestamp",
    "chunk_seconds_for_period",
    "windowed_iter",
    "Window",
    # Constants
    "SUPPORTED_PERIODS",
    "PERIOD_SECONDS",
    "MAX_ROWS_PER_CALL",
    "LOOKBACK_UPPER_BOUND_SECONDS",
    "CHUNK_SAFETY_RATIO",
]