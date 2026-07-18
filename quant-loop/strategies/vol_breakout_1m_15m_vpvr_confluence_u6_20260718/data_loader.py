"""Data loader for vol_breakout_1m_15m_vpvr_confluence_u6_20260718 (U6).

Loads 1m (perp) and 15m (spot) Binance klines for BTC/ETH/SOL using a
per-(symbol, timeframe) parquet, takes the last 30d window per
SMA-34802 convention, and normalizes to a UTC DatetimeIndex with
columns ``open, high, low, close, volume``.

Layout:

  BTC 15m : /home/smark/multica/quant-loop/live_data/BTCUSDT_15m.parquet
  ETH 15m : /home/smark/multica/quant-loop/live_data/ETHUSDT_15m.parquet
  SOL 15m : /home/smark/multica/quant-loop/live_data/SOLUSDT_15m.parquet
  BTC 1m  : /home/smark/multica/quant-loop/data/perp_1m/BTCUSDT_1m.parquet
  ETH 1m  : /home/smark/multica/quant-loop/data/perp_1m/ETHUSDT_1m.parquet
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"
STRATEGY_DIR = Path(__file__).parent
DATA_DIR = STRATEGY_DIR / "data"


def _normalize_ohlcv(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    """Bring any source to: UTC DatetimeIndex, columns=OHLCV."""
    if "open_time" in df.columns:
        idx = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.drop(columns=["open_time"])
        df.index = idx
    elif df.index.name != "openTime" and not isinstance(df.index, pd.DatetimeIndex):
        # try openTime column
        if "openTime" in df.columns:
            idx = pd.to_datetime(df["openTime"], unit="ms", utc=True)
            df = df.drop(columns=["openTime"])
            df.index = idx
    df.index.name = "openTime"
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    out = df[["open", "high", "low", "close", "volume"]].copy()
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = out[c].astype("float64")
    return out.sort_index()


def _cache_path(symbol: str, timeframe: str) -> Path:
    return DATA_DIR / f"{symbol}__{timeframe}.parquet"


def load_symbol(symbol: str, timeframe: str, window_days: int, refresh: bool = False) -> pd.DataFrame:
    """Return the last ``window_days`` of OHLCV for (symbol, timeframe).

    Cache policy: a per-(symbol, timeframe) normalized cache holds the
    full normalized source frame; ``window_days`` slices in-memory after
    cache hit. This guarantees that requesting window_days=365 yields
    365 days even if the cache was originally written for window=30.
    """
    sym = symbol.upper()
    cache = _cache_path(sym, timeframe)
    cfg = _read_cfg()
    src = Path(cfg["data_paths"][f"{sym}_{timeframe}"])
    if not src.is_absolute():
        src = Path("/home/smark/multica/quant-loop") / src
    if not src.exists():
        raise FileNotFoundError(f"missing source for {sym} {timeframe}: {src}")

    if cache.exists() and not refresh:
        cached = pd.read_parquet(cache)
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
        cached.index.name = "openTime"
        end = cached.index.max()
        start = end - pd.Timedelta(days=window_days)
        if cached.index[0] <= start:
            return cached.loc[start:end].copy()
        # Cache is shorter than requested window — fall through and
        # rebuild from source.
        refresh = True

    raw = pd.read_parquet(src)
    norm = _normalize_ohlcv(raw, src)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    norm.to_parquet(cache)
    end = norm.index.max()
    start = end - pd.Timedelta(days=window_days)
    sliced = norm.loc[start:end].copy()
    return sliced


def _read_cfg() -> dict:
    import json
    return json.loads(CONFIG_PATH.read_text())


def load_symbols(symbols: List[str], timeframe: str, window_days: int) -> Dict[str, pd.DataFrame]:
    """Multi-symbol convenience wrapper."""
    return {s: load_symbol(s, timeframe, window_days) for s in symbols}


def main() -> int:
    """CLI: warm caches and print span."""
    cfg = _read_cfg()
    for sym in cfg["instruments"]:
        for tf in ("15m", "1m"):
            try:
                df = load_symbol(sym, tf, cfg["window_days"], refresh=False)
                print(
                    f"  cached  {sym:<8} tf={tf:<3} rows={len(df):>6} "
                    f"span={df.index[0].date()}..{df.index[-1].date()}"
                )
            except FileNotFoundError as exc:
                print(f"  -- {sym:<8} tf={tf:<3} unavailable: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
