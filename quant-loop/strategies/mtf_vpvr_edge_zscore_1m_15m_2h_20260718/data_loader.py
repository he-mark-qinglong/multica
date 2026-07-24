"""Data loader for mtf_vpvr_edge_zscore_1m_15m_2h_20260718 (SMA-34991).

Single-asset multi-TF VPVR-edge + zscore reversion. Loads the canonical 1m
perp parquet from the shared pool at ``~/multica/quant-loop/data/perp_1m/`` and
exposes a tz-naive UTC DatetimeIndex so it can be aggregated to 15m / 2h
inside the strategy (no look-ahead; the higher-TF bars are constructed from
the same 1m tape).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

# Canonical shared pool — single source of truth for 1m perp bars.
SHARED_POOL = Path("/home/smark/multica/quant-loop/data/perp_1m")


def _load_1m(symbol: str) -> pd.DataFrame:
    """Load the canonical 1m perp parquet for ``symbol`` and tz-strip to naive."""
    p = SHARED_POOL / f"{symbol}_1m.parquet"
    if not p.is_file():
        raise SystemExit(f"missing 1m data parquet: {p}")
    df = pd.read_parquet(p)
    if "open_time" not in df.columns:
        raise SystemExit(f"{symbol}: open_time column missing")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time").sort_index()
    # Normalise to tz-naive UTC for cross-TF aggregation downstream.
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df.index.name = "open_time"
    keep = ["open", "high", "low", "close", "volume"]
    return df[keep]


def load_all(symbols: Iterable[str]) -> dict[str, pd.DataFrame]:
    return {sym: _load_1m(sym) for sym in symbols}


def load_funding(symbols: Iterable[str]):  # pragma: no cover  (single-asset, no funding)
    return {}
