"""Data loader for V1_funding_term_curve (iter#97, vpvr_funding_term_curve_1h_20260714).

Loads 1h klines for BTCUSDT and ETHUSDT, forward-fills 8h funding to 1h bar frequency.
Funding comes every 8h on Binance USD-margined perps — we use the ``fundingRate``
column at its native cadence and forward-fill to align with 1h kline index.
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


def _load_1h(symbol: str) -> pd.DataFrame:
    p = LIVE_DATA / f"{symbol}_1h.parquet"
    if not p.is_file():
        raise FileNotFoundError(f"missing 1h parquet for {symbol}: {p}")
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
    df = df.set_index("ts").sort_index()
    keep = ["open", "high", "low", "close", "volume",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]
    return df[keep].astype(np.float64)


def _load_funding(symbol: str) -> pd.DataFrame:
    p = FUNDING_DIR / f"{symbol}_funding.parquet"
    if not p.is_file():
        raise FileNotFoundError(f"missing funding parquet for {symbol}: {p}")
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=False)
    df = df.set_index("ts").sort_index()
    return df[["fundingRate"]].astype(np.float64)


def load_symbol(symbol: str) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_1h = _load_1h(symbol)
    funding = _load_funding(symbol)
    funding_1h = funding.reindex(df_1h.index, method="ffill").fillna(0.0)
    df = df_1h.copy()
    df["fundingRate"] = funding_1h["fundingRate"].astype(np.float64)
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