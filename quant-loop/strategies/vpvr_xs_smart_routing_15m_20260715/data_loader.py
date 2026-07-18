"""Data loader for V3_xs_smart_routing (iter#105).

TF = 15m. BTCUSDT only. We do NOT have bybit/okx public klines in live_data;
the multi-venue dimension is implemented inside strategy.py as a microprice
proxy derived from Binance spot taker_buy_share imbalance. When real cross-
venue data is wired (fetch_*_bybit.py / fetch_*_okx.py added to live_data),
swap the proxy for true microprice.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

LIVE_DATA = Path("/home/smark/multica/quant-loop/live_data")
DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_15m(symbol: str) -> pd.DataFrame:
    p = LIVE_DATA / f"{symbol}_15m.parquet"
    if not p.exists():
        raise FileNotFoundError(f"no 15m parquet for {symbol} at {p}")
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
    df = df.set_index("ts").sort_index()
    keep = ["open", "high", "low", "close", "volume",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]
    return df[keep].astype(np.float64)


def load_symbol(symbol: str) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _load_15m(symbol)


def load_all(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = {sym: load_symbol(sym) for sym in symbols}
    manifest_lines = []
    for sym, df in out.items():
        sha = hashlib.sha256(
            pd.util.hash_pandas_object(df.reset_index(), index=False).values
        ).hexdigest()[:16]
        manifest_lines.append(
            f"{sym}\t{len(df)}\t{sha}\tMULTI-VENUE-DATA-MISSING microprice=binance_buy_share_proxy"
        )
    (DATA_DIR / "manifest.txt").write_text("\n".join(manifest_lines) + "\n")
    return out