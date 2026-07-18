"""Data loader for SMA-34946 (U5 funding_carry ETH/SOL 1m fresh test).

Canonical real-Binance USDT-M perp sources (no synthetic):

  ETH OHLCV 1m : /home/smark/multica/quant-loop/data/perp_1m/ETHUSDT_1m.parquet
  SOL OHLCV 1m : /home/smark/multica/quant-loop/data/perp_1m/SOLUSDT_1m.parquet
                   (latest 2026-07-18 refetch per
                    data/perp_1m/fetch_report_usdm_1m.json — preferred
                    over the strategy-local snapshot used by SMA-34930)
  ETH funding  : /home/smark/multica/quant-loop/data/funding/ETHUSDT.parquet
  SOL funding  : /home/smark/multica/quant-loop/data/funding/SOLUSDT.parquet
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

QUANT_LOOP = Path("/home/smark/multica/quant-loop")

ETH_1M = QUANT_LOOP / "data" / "perp_1m" / "ETHUSDT_1m.parquet"
SOL_1M = QUANT_LOOP / "data" / "perp_1m" / "SOLUSDT_1m.parquet"
ETH_FUNDING = QUANT_LOOP / "data" / "funding" / "ETHUSDT.parquet"
SOL_FUNDING = QUANT_LOOP / "data" / "funding" / "SOLUSDT.parquet"


def _load_ohlcv_1m(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing 1m OHLCV: {path}")
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        idx = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.drop(columns=["open_time"])
        df.index = idx
    else:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep].astype(np.float64)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def _load_funding(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing funding: {path}")
    df = pd.read_parquet(path)
    if "ts" in df.columns:
        idx = pd.to_datetime(df["ts"], utc=True)
    elif "fundingTime" in df.columns:
        idx = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    else:
        raise ValueError(f"funding parquet {path} has neither 'ts' nor 'fundingTime'")
    df.index = idx
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df[["fundingRate"]].astype(np.float64)


def _merge_funding(ohlcv: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    ohlcv = ohlcv.copy()
    idx_utc = ohlcv.index
    if idx_utc.tz is None:
        idx_utc = pd.to_datetime(idx_utc, utc=True)
    else:
        idx_utc = idx_utc.tz_convert("UTC")
    fa = funding.reindex(idx_utc, method="ffill")
    fa.index = ohlcv.index
    ohlcv["funding"] = fa["fundingRate"].fillna(0.0).astype(np.float64)
    return ohlcv


def load_symbol_1m(symbol: str, window_days: int) -> Tuple[pd.DataFrame, dict]:
    sym = symbol.upper()
    if sym == "ETHUSDT":
        df = _load_ohlcv_1m(ETH_1M)
        funding = _load_funding(ETH_FUNDING)
    elif sym == "SOLUSDT":
        df = _load_ohlcv_1m(SOL_1M)
        funding = _load_funding(SOL_FUNDING)
    else:
        raise ValueError(f"unsupported symbol {symbol!r} (want ETHUSDT or SOLUSDT)")
    df = _merge_funding(df, funding)
    end = df.index.max()
    start = end - pd.Timedelta(days=window_days)
    window = df.loc[start:end].copy()
    start_utc = pd.to_datetime(start, utc=True)
    end_utc = pd.to_datetime(end, utc=True)
    events = funding.loc[start_utc:end_utc, "fundingRate"]
    stats = {
        "n_events": int(len(events)),
        "min": float(events.min()) if len(events) else 0.0,
        "max": float(events.max()) if len(events) else 0.0,
        "mean": float(events.mean()) if len(events) else 0.0,
        "neg_pct": float((events < 0).mean()) if len(events) else 0.0,
        "le_-1bp_pct": float((events <= -0.0001).mean()) if len(events) else 0.0,
        "le_-3bp_pct": float((events <= -0.0003).mean()) if len(events) else 0.0,
        "le_-5bp_pct": float((events <= -0.0005).mean()) if len(events) else 0.0,
        "le_-10bp_pct": float((events <= -0.001).mean()) if len(events) else 0.0,
    }
    return window, stats


__all__ = ["load_symbol_1m", "ETH_1M", "SOL_1M", "ETH_FUNDING", "SOL_FUNDING"]
