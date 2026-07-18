"""Data loader for the U5 funding_carry ETH/SOL 1m harness (SMA-34930).

Sources (all real Binance USDT-M perp data; per the issue hard rule
"NO synthetic data"):

  ETH 1m OHLCV     -> data/perp_1m/ETHUSDT_1m.parquet       (shared pool)
  SOL 1m OHLCV     -> strategies/vpvr_volume_edge_3tf_v1_20260711/
                      data/SOLUSDT__1m.parquet              (real Binance snapshot;
                                                            verified provenance via
                                                            SHA256 match with the
                                                            vpvr_xs_pairs mirror copy
                                                            and 2,378,800 rows
                                                            2022-01-01..2026-07-10)
  ETH funding      -> data/funding/ETHUSDT.parquet          (Binance USDT-M 8h events)
  SOL funding      -> data/funding/SOLUSDT.parquet          (Binance USDT-M 8h events)

Funding merge: the 8h funding events are reindexed onto the 1m OHLCV
index with ``ffill`` (carry-of-record at bar ``t`` is the most recent
paid funding event before bar ``t``'s open). The ``build_signals``
wrapper applies a ``shift(1)`` to enforce strict no-look-ahead.

Per the workspace AGENTS.md §1 (canonical enumeration) we already
verified the SOL 1m data exists only as strategy-local snapshots,
not in the shared ``data/perp_1m/`` pool. This file documents that
choice explicitly so future audits can reproduce it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

QUANT_LOOP = Path("/home/smark/multica/quant-loop")

ETH_1M = QUANT_LOOP / "data" / "perp_1m" / "ETHUSDT_1m.parquet"
SOL_1M = (QUANT_LOOP / "strategies" / "vpvr_volume_edge_3tf_v1_20260711"
          / "data" / "SOLUSDT__1m.parquet")
ETH_FUNDING = QUANT_LOOP / "data" / "funding" / "ETHUSDT.parquet"
SOL_FUNDING = QUANT_LOOP / "data" / "funding" / "SOLUSDT.parquet"


def _load_ohlcv_1m(path: Path) -> pd.DataFrame:
    """Load a 1m OHLCV parquet and normalise to a tz-naive UTC
    DatetimeIndex aligned to ``open_time``.
    """
    if not path.exists():
        raise FileNotFoundError(f"missing 1m OHLCV: {path}")
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        idx = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.drop(columns=["open_time"])
        df.index = idx
    elif "openTime" in df.columns or df.index.name in ("openTime", "open_time"):
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[["open", "high", "low", "close", "volume"]].astype(np.float64)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def _load_funding(path: Path) -> pd.DataFrame:
    """Load an 8h funding-event parquet; return ``fundingRate`` series
    indexed by UTC-aware event timestamp.
    """
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
    """ffill the funding series onto the 1m bar index (tz-naive)."""
    ohlcv = ohlcv.copy()
    if ohlcv.index.tz is None:
        idx_utc = pd.to_datetime(ohlcv.index, utc=True)
    else:
        idx_utc = ohlcv.index.tz_convert("UTC")
    fa = funding.reindex(idx_utc, method="ffill")
    fa.index = ohlcv.index
    ohlcv["funding"] = fa["fundingRate"].fillna(0.0).astype(np.float64)
    return ohlcv


def load_symbol_1m(symbol: str, window_days: int) -> Tuple[pd.DataFrame, dict]:
    """Load one symbol (ETH or SOL) at 1m, with funding merged.

    Returns
    -------
    (df, funding_event_stats)
        ``df`` indexed by tz-naive UTC ``DatetimeIndex`` with columns
        ``open, high, low, close, volume, funding``. ``funding`` is the
        ffill of the 8h funding events onto the 1m bar index, before
        the no-look-ahead ``shift(1)`` that the signal layer applies.
        ``funding_event_stats`` describes the raw event distribution
        over the loaded window (used for threshold sanity checks).
    """
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
    # Funding event stats over the same window (event cadence, not bar index)
    start_utc = pd.to_datetime(start, utc=True)
    end_utc = pd.to_datetime(end, utc=True)
    events = funding.loc[start_utc:end_utc, "fundingRate"]
    stats = {
        "n_events": int(len(events)),
        "max": float(events.max()) if len(events) else 0.0,
        "p99": float(events.quantile(0.99)) if len(events) else 0.0,
        "p95": float(events.quantile(0.95)) if len(events) else 0.0,
        "p90": float(events.quantile(0.90)) if len(events) else 0.0,
        "p80": float(events.quantile(0.80)) if len(events) else 0.0,
        "p20": float(events.quantile(0.20)) if len(events) else 0.0,
        "p10": float(events.quantile(0.10)) if len(events) else 0.0,
        "p05": float(events.quantile(0.05)) if len(events) else 0.0,
        "p01": float(events.quantile(0.01)) if len(events) else 0.0,
        "min": float(events.min()) if len(events) else 0.0,
        "mean": float(events.mean()) if len(events) else 0.0,
        "neg_pct": float((events < 0).mean()) if len(events) else 0.0,
        "le_-1bp_pct": float((events <= -0.0001).mean()) if len(events) else 0.0,
        "le_-0.5bp_pct": float((events <= -0.00005).mean()) if len(events) else 0.0,
    }
    return window, stats


__all__ = ["load_symbol_1m"]