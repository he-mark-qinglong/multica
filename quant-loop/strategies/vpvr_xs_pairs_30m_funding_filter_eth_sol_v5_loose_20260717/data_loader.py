"""Data loader for vpvr_xs_pairs_30m_funding_filter_eth_sol (iter #84).

ETH/SOL V7-variant: both legs use native 30m Binance USDT-M perp data.
No 15m resample needed (unlike the BTC/SOL variant where SOLUSDT has
no native 30m and needs 15m->30m resample).

Funding rates come from Binance fapi (8h cycle).
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "open_time" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df["openTime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("openTime")
    elif isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index.name = "openTime"
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    out = df[keep].copy()
    out.index.name = "openTime"
    return out.sort_index()


def _load_30m(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / (symbol + "__30m.parquet")
    if not p.is_file():
        raise SystemExit("missing 30m data parquet: " + str(p))
    df = pd.read_parquet(p)
    if not isinstance(df.index, pd.DatetimeIndex):
        df = _standardize_columns(df)
    df.index.name = "openTime"
    return df.sort_index()


def _load_funding(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / (symbol + "__funding.parquet")
    if not p.is_file():
        raise SystemExit("missing funding parquet: " + str(p))
    df = pd.read_parquet(p)
    if "fundingTime" in df.columns:
        df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    elif "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    else:
        raise SystemExit("unexpected funding schema: " + str(list(df.columns)))
    df = df.sort_values("ts").set_index("ts")
    return df[["fundingRate"]]


def load_all(symbols):
    return {s: _load_30m(s) for s in symbols}


def load_funding(symbols):
    return {s: _load_funding(s) for s in symbols}
