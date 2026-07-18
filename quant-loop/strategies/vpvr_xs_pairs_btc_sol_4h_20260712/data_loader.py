"""Load the per-symbol 4h data for V3 (iter#75).

BTC and ETH have no native 4h parquet in this workspace; we resample the
canonical 1h parquet to 4h on the fly. SOLUSDT has a native 4h parquet.

All symbols share a common UTC index once resampled.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from strategy import resample_ohlcv


DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_1h(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / f"fapi_{symbol}__1h.parquet"
    if not p.is_file():
        raise SystemExit(f"missing 1h data parquet: {p}")
    df = pd.read_parquet(p)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SystemExit(f"{symbol}: index is not datetime")
    df.index.name = "openTime"
    return df.sort_index()


def _load_4h(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / f"fapi_{symbol}__4h.parquet"
    if not p.is_file():
        raise SystemExit(f"missing 4h data parquet: {p}")
    df = pd.read_parquet(p)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SystemExit(f"{symbol}: index is not datetime")
    df.index.name = "openTime"
    return df.sort_index()


def load_all(symbols: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for sym in symbols:
        if sym == "SOLUSDT":
            df_4h = _load_4h(sym)
            out[sym] = df_4h
        else:
            df_1h = _load_1h(sym)
            out[sym] = resample_ohlcv(df_1h, rule="4h")
    return out