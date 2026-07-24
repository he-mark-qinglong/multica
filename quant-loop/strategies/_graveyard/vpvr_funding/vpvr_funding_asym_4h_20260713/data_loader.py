"""Data loader for vpvr_funding_asym_4h_20260713 (iter#92 V3).

Loads 4h OHLCV + 8h funding for BTCUSDT and ETHUSDT. Funding is
forward-filled to 4h bar frequency. Annualized funding rate is computed
in basis points: funding * 3 (events per day) * 365 * 10000.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

LIVE_DATA = Path("/home/smark/multica/quant-loop/live_data")
FUNDING_DIR = Path("/home/smark/multica/quant-loop/funding_analysis")
DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_4h(symbol: str) -> pd.DataFrame:
    p = LIVE_DATA / f"{symbol}_4h.parquet"
    if not p.exists():
        raise FileNotFoundError(f"no 4h parquet for {symbol} at {p}")
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
    df = df.set_index("ts").sort_index()
    keep = ["open", "high", "low", "close", "volume",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]
    return df[keep].astype(np.float64)


def _load_funding(symbol: str) -> pd.DataFrame:
    candidates = [
        FUNDING_DIR / f"{symbol}_bybit_funding.parquet",
        FUNDING_DIR / f"{symbol}_funding.parquet",
    ]
    for p in candidates:
        if p.exists():
            df = pd.read_parquet(p)
            df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=False)
            df = df.set_index("ts").sort_index()
            return df[["fundingRate"]].astype(np.float64)
    raise FileNotFoundError(f"no funding parquet for {symbol} in {FUNDING_DIR}")


def load_symbol(symbol: str) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_4h = _load_4h(symbol)
    funding = _load_funding(symbol)
    funding_4h = funding.reindex(df_4h.index, method="ffill").fillna(0.0)
    df = df_4h.copy()
    df["fundingRate"] = funding_4h["fundingRate"].astype(np.float64)
    # Annualized funding (8h events → 3/day → 1095/yr).
    df["fundingAnnBps"] = (df["fundingRate"] * 3.0 * 365.0 * 10000.0).astype(np.float64)
    return df


def load_all(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = {sym: load_symbol(sym) for sym in symbols}
    manifest_lines = []
    for sym, df in out.items():
        sha = hashlib.sha256(
            pd.util.hash_pandas_object(df.reset_index(), index=False).values
        ).hexdigest()[:16]
        manifest_lines.append(f"{sym}\t{len(df)}\t{sha}")
    (DATA_DIR / "manifest.txt").write_text("\n".join(manifest_lines) + "\n")
    return out