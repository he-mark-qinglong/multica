"""Data loader for vpvr_xs_pairs_30m_funding_filter_20260712 (iter #81).

V5 spec:
  - timeframe 30m (BTC + SOL pair).
  - BTCUSDT 30m parquet is canonical. SOLUSDT comes from a 15m parquet
    that we resample to 30m on the fly (no native 30m parquet exists
    for SOL in this workspace).
  - Funding-rate parquets for BTC and SOL are loaded alongside for the
    funding-blowoff filter.

All symbols share a common UTC index once resampled.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from strategy import resample_ohlcv


DATA_DIR = Path(__file__).resolve().parent / "data"


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical OHLCV column set: open/high/low/close/volume + dt index."""
    # If the index is not a DatetimeIndex but open_time is present, build the index.
    if "open_time" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df["openTime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("openTime")
    elif isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index.name = "openTime"
    # Normalize tz to naive UTC for cross-symbol alignment.
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    out = df[keep].copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        raise SystemExit("index is not datetime: " + str(list(out.index.names)))
    out.index.name = "openTime"
    return out.sort_index()


def _load_30m(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / (symbol + "__30m.parquet")
    if not p.is_file():
        raise SystemExit("missing 30m data parquet: " + str(p))
    df = pd.read_parquet(p)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SystemExit(symbol + ": index is not datetime")
    df.index.name = "openTime"
    return df.sort_index()


def _load_15m(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / (symbol + "__15m.parquet")
    if not p.is_file():
        raise SystemExit("missing 15m data parquet: " + str(p))
    df = pd.read_parquet(p)
    df = _standardize_columns(df)
    return df


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
    """Load per-symbol 30m OHLCV dataframes."""
    out = {}
    for sym in symbols:
        if sym == "BTCUSDT":
            out[sym] = _load_30m(sym)
        elif sym == "SOLUSDT":
            df_15m = _load_15m(sym)
            out[sym] = resample_ohlcv(df_15m, rule="30min")
        else:
            raise SystemExit("unsupported symbol in V5: " + sym)
    return out


def load_funding(symbols):
    """Load per-symbol 8h funding events as DatetimeIndex Series."""
    out = {}
    for sym in symbols:
        f = _load_funding(sym)
        idx = f.index.tz_convert(None) if f.index.tz is not None else f.index
        out[sym] = pd.Series(f["fundingRate"].to_numpy(), index=idx, name="funding_rate")
    return out