"""Data loader for trend_multi_tf_momentum_cascade_4h_1h_15m_20260714 (V2).

Loads 4h (trend), 1h (pullback), 15m (entry) klines for BTCUSDT from
the canonical ``live_data/`` directory. No resampling, no fetch.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"
STRATEGY_DIR = Path(__file__).parent
DATA_DIR = STRATEGY_DIR / "data"

DEFAULT_LIVE_DATA_ROOT = Path("/home/smark/multica/quant-loop/live_data")

if os.environ.get("LIVE_TRADING") == "1":
    raise SystemExit(
        "data_loader.py is paper-trade only; refusing to run with LIVE_TRADING=1"
    )


@dataclass
class SourceManifest:
    root: Path
    files: Dict[str, str]

    def write(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w") as fh:
            for rel, sha in sorted(self.files.items()):
                fh.write(f"{sha}  {rel}\n")

    def verify(self) -> list:
        return [r for r, e in self.files.items() if _sha256(self.root / r) != e]


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


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


def _read_source(symbol: str, timeframe: str, root: Path) -> pd.DataFrame:
    src = root / f"{symbol.upper()}_{timeframe}.parquet"
    if not src.exists():
        raise FileNotFoundError(f"missing {timeframe} source for {symbol}: {src}")
    raw = pd.read_parquet(src)
    return _normalize_ohlcv(raw, src)


def load_symbol_tf(
    symbol: str, timeframe: str,
    source_root: Path = DEFAULT_LIVE_DATA_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> pd.DataFrame:
    cache = data_dir / f"{symbol.upper()}__{timeframe}.parquet"
    if cache.exists() and not refresh:
        cached = pd.read_parquet(cache)
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
        cached.index.name = "openTime"
        return cached
    df = _read_source(symbol, timeframe, source_root)
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    return df


def load_symbol_multi(
    symbol: str,
    timeframes: Tuple[str, ...] = ("4h", "1h", "15m"),
    source_root: Path = DEFAULT_LIVE_DATA_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, pd.DataFrame]:
    return {tf: load_symbol_tf(symbol, tf, source_root, data_dir, refresh=refresh) for tf in timeframes}


def load_all(
    symbols: Optional[Iterable[str]] = None,
    source_root: Path = DEFAULT_LIVE_DATA_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    cfg = json.loads(CONFIG_PATH.read_text())
    if symbols is None:
        symbols = cfg["instruments"]
    tfs = (cfg["timeframe_trend"], cfg["timeframe_filter"], cfg["timeframe_entry"])
    out: Dict[str, Dict[str, pd.DataFrame]] = {}
    for sym in symbols:
        out[sym] = load_symbol_multi(sym, tfs, source_root, data_dir, refresh=refresh)
    return out


def build_source_manifest(source_root: Path = DEFAULT_LIVE_DATA_ROOT) -> SourceManifest:
    files: Dict[str, str] = {}
    for tf in ("4h", "1h", "15m"):
        for p in sorted(source_root.glob(f"*_{tf}.parquet")):
            files[p.name] = _sha256(p)
    if not files:
        raise FileNotFoundError(f"no <SYM>_(4h|1h|15m).parquet under {source_root}")
    return SourceManifest(root=source_root, files=files)


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    manifest = build_source_manifest(DEFAULT_LIVE_DATA_ROOT)
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    manifest.write(manifest_path)
    print(f"Source manifest written: {manifest_path} ({len(manifest.files)} files)")
    for sym in cfg["instruments"]:
        for tf in (cfg["timeframe_trend"], cfg["timeframe_filter"], cfg["timeframe_entry"]):
            df = load_symbol_tf(sym, tf, refresh=True)
            print(f"  cached  {sym:<8} {tf:<3} rows={len(df):>6} "
                  f"span={df.index[0].date()}..{df.index[-1].date()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())