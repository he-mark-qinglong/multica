"""Load BTCUSDT 4h OHLCV from the canonical 30m parquet source.

This data is the `MACRO-CALENDAR-EMBEDDED` set referenced by the strategy
manifest: 9912 4h bars from 2022-01-01 through 2026-07-10, resampled from
`/home/smark/multica/quant-loop/data/perp_30m/BTCUSDT_30m.parquet`.

Public API:
    load_btcusdt_4h() -> pd.DataFrame
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


_SRC_30M = Path("/home/smark/multica/quant-loop/data/perp_30m/BTCUSDT_30m.parquet")


def load_btcusdt_4h() -> pd.DataFrame:
    """Return BTCUSDT 4h OHLCV indexed by timestamp (UTC)."""
    if not _SRC_30M.exists():
        raise FileNotFoundError(f"Missing 30m source: {_SRC_30M}")
    df = pd.read_parquet(_SRC_30M)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("ts")
    ohlcv = df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return ohlcv[["open", "high", "low", "close", "volume"]]