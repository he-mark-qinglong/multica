"""Data loader for vpvr_xs_basis_zscore_15m_funding_filter_20260712 (iter #72).

15m BTCUSDT/ETHUSDT native parquets, each carrying an in-line funding_rate
column (forward-filled onto the 15m bar index from 8h perp funding events).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    """Keep OHLCV + funding_rate, sort, normalize tz."""
    df = df.copy()
    if isinstance(df.index, pd.DatetimeIndex):
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        df.index.name = "openTime"
    keep = [c for c in ("open", "high", "low", "close", "volume", "funding_rate") if c in df.columns]
    out = df[keep].copy()
    out.index.name = "openTime"
    return out.sort_index()


def load_all(symbols):
    """Load per-symbol 15m OHLCV+funding DataFrames."""
    out = {}
    for sym in symbols:
        p = DATA_DIR / (sym + "__15m.parquet")
        if not p.is_file():
            raise SystemExit("missing 15m data parquet: " + str(p))
        df = pd.read_parquet(p)
        df = _standardize(df)
        out[sym] = df
    return out


def load_funding_series(symbols):
    """Per-symbol funding_rate Series aligned to 15m bars (forward-fill)."""
    out = {}
    for sym in symbols:
        if sym not in ("BTCUSDT", "ETHUSDT"):
            raise SystemExit("unsupported symbol for funding: " + sym)
        # Funding lives inside the OHLCV parquet as a column; carry it as Series.
        p = DATA_DIR / (sym + "__15m.parquet")
        df = pd.read_parquet(p)
        if isinstance(df.index, pd.DatetimeIndex):
            if df.index.tz is not None:
                df.index = df.index.tz_convert(None)
        if "funding_rate" not in df.columns:
            raise SystemExit(sym + ": funding_rate column missing from 15m parquet")
        s = pd.Series(df["funding_rate"].astype(float).values, index=df.index, name="funding_rate")
        s = s.sort_index()
        out[sym] = s
    return out
