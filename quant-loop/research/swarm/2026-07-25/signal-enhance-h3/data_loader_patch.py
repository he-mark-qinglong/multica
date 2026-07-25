"""Swarm-local data loader for H3 signal-enhancement research.

Reads the canonical 1m perp klines from ``quant-loop/data/perp_1m/`` and the
funding rate series from ``quant-loop/funding_analysis/`` / graveyard cache,
then normalises indices to tz-naive DatetimeIndex so the shared
``mtf_xs_pairs_base_20260718`` pipeline can consume them unchanged.

This is a research patch; it does not modify the strategy or shared code.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path("/Users/mark/multica/quant-loop")

PERP_1M_DIR = ROOT / "data" / "perp_1m"
BTC_FUNDING = ROOT / "funding_analysis" / "BTCUSDT_funding.parquet"
SOL_FUNDING = ROOT / "strategies" / "_graveyard" / "xs_pairs_30m" / "vpvr_xs_pairs_30m_funding_filter_20260712" / "data" / "SOLUSDT__funding.parquet"


def _read_1m(symbol: str) -> pd.DataFrame:
    p = PERP_1M_DIR / f"{symbol}_1m.parquet"
    df = pd.read_parquet(p, columns=["open_time", "open", "high", "low", "close", "volume"])
    df = df.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)).tz_convert(None)
    df.index = idx
    df.index.name = "openTime"
    return df.sort_index()


def _read_funding_btc() -> pd.Series:
    df = pd.read_parquet(BTC_FUNDING)
    idx = pd.DatetimeIndex(pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)).tz_convert(None)
    s = pd.Series(df["fundingRate"].astype(float).to_numpy(), index=idx, name="fundingRate")
    return s.sort_index()


def _read_funding_sol() -> pd.Series:
    df = pd.read_parquet(SOL_FUNDING)
    idx = pd.DatetimeIndex(pd.to_datetime(df["ts"], utc=True)).tz_convert(None)
    s = pd.Series(df["fundingRate"].astype(float).to_numpy(), index=idx, name="fundingRate")
    return s.sort_index()


def load_all(symbols=("BTCUSDT", "SOLUSDT")) -> dict[str, pd.DataFrame]:
    return {sym: _read_1m(sym) for sym in symbols}


def load_funding(symbols=("BTCUSDT", "SOLUSDT")) -> dict[str, pd.Series]:
    out = {}
    if "BTCUSDT" in symbols:
        out["BTCUSDT"] = _read_funding_btc()
    if "SOLUSDT" in symbols:
        out["SOLUSDT"] = _read_funding_sol()
    return out


def slice_by_date(d1m: dict[str, pd.DataFrame], funding: dict[str, pd.Series],
                  start: str | None = "2022-01-01", end: str | None = None):
    """Return copies restricted to [start, end]."""
    d1m_s = {}
    for sym, df in d1m.items():
        mask = True
        if start:
            mask = mask & (df.index >= pd.Timestamp(start))
        if end:
            mask = mask & (df.index <= pd.Timestamp(end))
        d1m_s[sym] = df.loc[mask].copy()
    fund_s = {}
    for sym, s in funding.items():
        mask = True
        if start:
            mask = mask & (s.index >= pd.Timestamp(start))
        if end:
            mask = mask & (s.index <= pd.Timestamp(end))
        fund_s[sym] = s.loc[mask].copy()
    return d1m_s, fund_s
