"""Data loader for V3_funding_reset_window (iter#108).

Loads 1h klines for BTCUSDT and aligns the 8h funding rate to 1h bar
frequency via forward-fill. Funding parquet comes from funding_analysis/
(sibling project of live_data/).
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
# The canonical 1h and funding snapshots are present in sibling strategy
# bundles; keep the loader usable when the shared live_data symlink is stale.
OHLCV_FALLBACK_DIR = Path(
    "/home/smark/multica/quant-loop/strategies/"
    "momentum_trend_multi_tf_atr_scaled_1h_20260712/data"
)
FUNDING_FALLBACK_DIR = Path(
    "/home/smark/multica/quant-loop/strategies/"
    "vpvr_funding_aware_v1_20260711/data"
)


def _first_existing(paths):
    for path in paths:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "none of the candidate data files exist: "
        + ", ".join(str(path) for path in paths)
    )


def _to_naive_utc(values, unit=None):
    parsed = pd.to_datetime(values, unit=unit, utc=True)
    return pd.DatetimeIndex(parsed).tz_convert(None)


def _load_1h(symbol):
    p = _first_existing(
        [
            DATA_DIR / f"{symbol}__1h.parquet",
            OHLCV_FALLBACK_DIR / f"{symbol}__1h.parquet",
            LIVE_DATA / f"{symbol}_1h.parquet",
        ]
    )
    raw = pd.read_parquet(p)
    if "open_time" in raw.columns:
        ts = _to_naive_utc(raw["open_time"], unit="ms")
    else:
        ts = _to_naive_utc(raw.index)
    raw = raw.copy()
    raw.index = pd.DatetimeIndex(ts, name="ts")
    keep = ["open", "high", "low", "close", "volume"]
    optional = ["quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]
    for column in optional:
        if column not in raw:
            raw[column] = 0.0
    return raw[keep + optional].astype(np.float64).sort_index()


def _load_funding(symbol):
    p = _first_existing(
        [
            DATA_DIR / f"{symbol}__funding.parquet",
            FUNDING_DIR / f"{symbol}_funding.parquet",
            FUNDING_FALLBACK_DIR / f"{symbol}__funding.parquet",
        ]
    )
    raw = pd.read_parquet(p)
    raw = raw.copy()
    if "fundingTime" in raw.columns:
        ts = _to_naive_utc(raw["fundingTime"], unit="ms")
    elif "ts" in raw.columns:
        ts = _to_naive_utc(raw["ts"])
    else:
        raise ValueError(f"funding data has no timestamp column: {p}")
    raw.index = pd.DatetimeIndex(ts, name="ts")
    return raw[["fundingRate"]].astype(np.float64).sort_index()


def load_symbol(symbol):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_1h = _load_1h(symbol)
    funding = _load_funding(symbol)
    funding_1h = funding.reindex(df_1h.index, method="ffill").fillna(0.0)
    df = df_1h.copy()
    df["fundingRate"] = funding_1h["fundingRate"].astype(np.float64)
    return df


def load_all(symbols):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = {sym: load_symbol(sym) for sym in symbols}
    manifest_lines = []
    for sym, df in out.items():
        sha = hashlib.sha256(
            pd.util.hash_pandas_object(df.reset_index(), index=False).values
        ).hexdigest()[:16]
        manifest_lines.append(
            f"{sym}\t{len(df)}\t{sha}\tFUNDING-DATA-PRESENT funding_cadence=8h"
        )
    (DATA_DIR / "manifest.txt").write_text("\n".join(manifest_lines) + "\n")
    return out