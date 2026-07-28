"""Data loader for mtf_xs_pairs_1m_15m_2h_h3_20260718 (H3 — 2h funding regime).

Loads native 1m parquet per symbol + funding parquet (8h events). 15m
and 2h aggregation happens in the shared base via aggregate_ohlcv
(built from the same 1m bars, no look-ahead).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_1m(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / (symbol + "__1m.parquet")
    if not p.is_file():
        raise SystemExit("missing 1m data parquet: " + str(p))
    df = pd.read_parquet(p)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SystemExit(symbol + ": index is not datetime")
    df.index.name = "openTime"
    return df.sort_index()


def _load_funding_one(symbol: str) -> pd.Series:
    p = DATA_DIR / (symbol + "__funding.parquet")
    if not p.is_file():
        raise SystemExit("missing funding parquet: " + str(p))
    df = pd.read_parquet(p)
    if "ts" not in df.columns or "fundingRate" not in df.columns:
        raise SystemExit(symbol + ": funding parquet missing ts/fundingRate")
    s = pd.Series(df["fundingRate"].astype(float).to_numpy(),
                  index=pd.DatetimeIndex(df["ts"]), name="fundingRate")
    s = s.sort_index()
    s.index.name = "fundingTime"
    return s


def load_all(symbols):
    """Return dict symbol -> 1m OHLCV DataFrame."""
    return {sym: _load_1m(sym) for sym in symbols}


def load_funding(symbols):
    """Return dict symbol -> fundingRate Series indexed by event timestamp."""
    return {sym: _load_funding_one(sym) for sym in symbols}