"""Data loader for vpvr_volume_edge_3tf_v1_20260711.

Loads 4h (trend), 15m (filter), 1m (entry) klines from the strategy's
local ``data/`` directory. No fetch, no resampling.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"
STRATEGY_DIR = Path(__file__).parent
DATA_DIR = STRATEGY_DIR / "data"

if os.environ.get("LIVE_TRADING") == "1":
    raise SystemExit(
        "data_loader.py is paper-trade only; refusing to run with LIVE_TRADING=1"
    )


def _normalize_ohlcv(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    expected = {"open", "high", "low", "close", "volume"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    if "open_time" in df.columns:
        idx = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.drop(columns=["open_time"])
        df.index = idx
    df.index.name = "openTime"
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df[["open", "high", "low", "close", "volume"]].sort_index()


def _read_local(symbol: str, timeframe: str) -> pd.DataFrame:
    src = DATA_DIR / f"{symbol.upper()}__{timeframe}.parquet"
    if not src.exists():
        raise FileNotFoundError(f"missing {timeframe} source for {symbol}: {src}")
    raw = pd.read_parquet(src)
    return _normalize_ohlcv(raw, src)


def load_symbol_tf(
    symbol: str, timeframe: str,
    data_dir: Path = DATA_DIR,
) -> pd.DataFrame:
    """Load cached parquet from the strategy's data/ dir."""
    src = data_dir / f"{symbol.upper()}__{timeframe}.parquet"
    if not src.exists():
        raise FileNotFoundError(f"missing {timeframe} source for {symbol}: {src}")
    df = pd.read_parquet(src)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index.name = "openTime"
    return df


def load_symbol_multi(
    symbol: str,
    timeframes: Tuple[str, ...] = ("4h", "15m", "1m"),
    data_dir: Path = DATA_DIR,
) -> Dict[str, pd.DataFrame]:
    return {tf: load_symbol_tf(symbol, tf, data_dir) for tf in timeframes}


def load_all(
    symbols: Optional[Iterable[str]] = None,
    data_dir: Path = DATA_DIR,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    cfg = json.loads(CONFIG_PATH.read_text())
    if symbols is None:
        symbols = cfg["instruments"]
    tfs = (cfg["timeframe_trend"], cfg["timeframe_filter"], cfg["timeframe_entry"])
    out: Dict[str, Dict[str, pd.DataFrame]] = {}
    for sym in symbols:
        out[sym] = load_symbol_multi(sym, tfs, data_dir)
    return out


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    for sym in cfg["instruments"]:
        for tf in (cfg["timeframe_trend"], cfg["timeframe_filter"], cfg["timeframe_entry"]):
            df = load_symbol_tf(sym, tf)
            print(f"  {sym:<8} {tf:<3} rows={len(df):>7} "
                  f"span={df.index[0].date()}..{df.index[-1].date()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
