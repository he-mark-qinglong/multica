"""Data loader for mtf_xs_pairs_1m_15m_2h_h3_20260718 (H3 — funding regime)."""

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


def _load_funding(symbol: str) -> pd.Series:
    p = DATA_DIR / (symbol + "__funding.parquet")
    if not p.is_file():
        return pd.Series(dtype=float, name="fundingRate")
    df = pd.read_parquet(p)
    if "fundingTime" in df.columns:
        df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    elif "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    else:
        raise SystemExit("unexpected funding schema for " + symbol)
    df = df.sort_values("ts").set_index("ts")
    return df["fundingRate"].rename("fundingRate")


def load_all(symbols):
    return {sym: _load_1m(sym) for sym in symbols}


def load_funding(symbols):
    return {sym: _load_funding(sym) for sym in symbols}