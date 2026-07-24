"""Data loader for funding_carry_asym (SMA-34793 prototype).

Loads BTCUSDT 4h OHLCV and merges the SMA-34789 funding series
(Binance USDT-M perpetual, 8h cadence) onto the bar index via
``ffill``. The funding column at bar ``t`` is the rate paid at the
most recent funding event strictly before bar ``t``'s open —
enforced later by ``build_signals`` via ``shift(1)``.

Notes
-----
The cycle-46 4h strategies in this catalog use
``funding_analysis/{SYM}_bybit_funding.parquet`` (Bybit) and a
different ``data_source`` string. This prototype keeps the SMA-34789
Binance fetcher as its source of truth; the merge logic is identical
modulo which parquet path is read.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

LIVE_DATA = Path("/home/smark/multica/quant-loop/live_data")
SMA_34789_DIR = Path("/home/smark/multica/quant-loop/data/funding")

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_ohlcv(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load OHLCV bars from the canonical live_data store.

    The BTCUSDT 4h parquet lives at ``live_data/BTCUSDT_4h.parquet``.
    For the 1m and 15m TF the harness stages them under this
    strategy's ``data/`` directory at ``run_backtest.py`` time
    (see run_backtest for the path).
    """
    candidates = [
        LIVE_DATA / f"{symbol}_{timeframe}.parquet",
        DATA_DIR / f"{symbol}__{timeframe}.parquet",
    ]
    for p in candidates:
        if p.exists():
            df = pd.read_parquet(p)
            for col in ("open_time", "ts"):
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], unit="ms", utc=False)
                    df = df.set_index(col)
                    break
            else:
                # Already indexed by a DatetimeIndex.
                pass
            df = df.sort_index()
            keep = ["open", "high", "low", "close", "volume"]
            present = [c for c in keep if c in df.columns]
            return df[present].astype(np.float64)
    raise FileNotFoundError(
        f"no {timeframe} OHLCV parquet for {symbol}; looked in "
        f"{[str(p) for p in candidates]}"
    )


def _load_funding(symbol: str) -> pd.DataFrame:
    """Load the SMA-34789 funding series for a symbol."""
    candidates = [
        SMA_34789_DIR / f"{symbol}.parquet",
        SMA_34789_DIR / f"{symbol}_bybit_funding.parquet",
    ]
    for p in candidates:
        if p.exists():
            df = pd.read_parquet(p)
            if "ts" in df.columns:
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df = df.set_index("ts")
            elif "fundingTime" in df.columns:
                df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
                df = df.set_index("fundingTime")
            df = df.sort_index()
            return df[["fundingRate"]].astype(np.float64)
    raise FileNotFoundError(
        f"no funding parquet for {symbol} in {SMA_34789_DIR}"
    )


def load_symbol(symbol: str, timeframe: str = "4h") -> pd.DataFrame:
    """Single-symbol loader.

    Returns a DataFrame with [open, high, low, close, volume, funding]
    on the bar index, with funding ffilled from the most recent paid
    event. The ``funding`` column at bar ``t`` is the rate paid at the
    last funding event at-or-before bar ``t``'s open time — the
    backtest layer is responsible for the one-step ``shift(1)`` to
    enforce no-look-ahead (see ``build_signals.py``).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_ohlcv = _load_ohlcv(symbol, timeframe)
    funding = _load_funding(symbol)
    # Funding merge: align funding (UTC, datetime64) to the OHLCV
    # index. The OHLCV index is tz-naive (ms UTC); coerce to UTC
    # datetime64 for the reindex.
    if df_ohlcv.index.tz is None:
        idx_utc = pd.to_datetime(df_ohlcv.index, utc=True)
    else:
        idx_utc = df_ohlcv.index.tz_convert("UTC")
    funding_aligned = funding.reindex(idx_utc, method="ffill")
    # Drop tz so it aligns with df_ohlcv.index
    funding_aligned.index = funding_aligned.index.tz_localize(None)
    funding_aligned.index = df_ohlcv.index
    df = df_ohlcv.copy()
    df["funding"] = funding_aligned["fundingRate"].fillna(0.0).astype(np.float64)
    return df


def load_all(symbols: List[str], timeframe: str = "4h") -> Dict[str, pd.DataFrame]:
    """Multi-symbol loader; writes a manifest for traceability."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        out[sym] = load_symbol(sym, timeframe=timeframe)
    lines = []
    for sym, df in out.items():
        sha = hashlib.sha256(
            pd.util.hash_pandas_object(df.reset_index(), index=False).values
        ).hexdigest()[:16]
        lines.append(f"{sym}\t{timeframe}\t{len(df)}\t{sha}")
    (DATA_DIR / "manifest.txt").write_text("\n".join(lines) + "\n")
    return out


__all__ = ["load_symbol", "load_all", "LIVE_DATA", "SMA_34789_DIR", "DATA_DIR"]
