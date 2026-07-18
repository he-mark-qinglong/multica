"""Data loader for momentum_intraday_fast_15m_btc_20260712 (iter#89, V13).

Loads **15m** (entry TF) and **1h** (trend filter TF) parquet for
BTCUSDT only from the canonical ``live_data/`` directory.

The two TFs are aligned by **forward-filling** the 1h frame to the
15m grid. This is the standard pattern: trend filters must not look
ahead, so the 1h EMA value used at 15m bar ``t`` is the most recent
1h close ``<= t`` (with the per-bar 1h EMA additionally shifted by 1
to enforce strict trailing).

Source layout (canonical):
    /home/smark/multica/quant-loop/live_data/
        BTCUSDT_15m.parquet
        BTCUSDT_1h.parquet

Output layout (per-strategy cache, owned by this directory):
    <strategy_dir>/data/
        BTCUSDT__15m.parquet
        BTCUSDT__1h.parquet
        manifest.parquet.sha256      # SHA256 of every source parquet

Schema contract (after normalization):
    index  : openTime, pd.DatetimeIndex, tz=UTC, dtype datetime64[ns]
    cols   : open, high, low, close, volume  (all float64)
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"
STRATEGY_DIR = Path(__file__).parent
DATA_DIR = STRATEGY_DIR / "data"

# Canonical live data store.
DEFAULT_SOURCE_ROOT = Path("/home/smark/multica/quant-loop/live_data")

# Paper-trade guardrail. Live trading is not supported by this strategy.
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

    def verify(self) -> List[str]:
        drift: List[str] = []
        for rel, expected in self.files.items():
            current = _sha256(self.root / rel)
            if current != expected:
                drift.append(rel)
        return drift


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _source_filename(symbol: str, timeframe: str) -> str:
    return f"{symbol.upper()}_{timeframe}.parquet"


def _normalize_ohlcv(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    expected_cols = {"open", "high", "low", "close", "volume"}
    missing = expected_cols - set(df.columns)
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


def _read_source(symbol: str, timeframe: str, source_root: Path) -> pd.DataFrame:
    src = source_root / _source_filename(symbol, timeframe)
    if not src.exists():
        raise FileNotFoundError(f"missing {timeframe} source for {symbol}: {src}")
    raw = pd.read_parquet(src)
    return _normalize_ohlcv(raw, src)


def build_source_manifest(source_root: Path = DEFAULT_SOURCE_ROOT) -> SourceManifest:
    """Hash every <SYM>_<TF>.parquet under source_root where TF is in
    {15m, 1h} (this strategy only depends on these two)."""
    files: Dict[str, str] = {}
    for tf in ("15m", "1h"):
        for p in sorted(source_root.glob(f"*_{tf}.parquet")):
            stem = p.stem
            if "_" not in stem:
                continue
            sym = stem.split("_")[0]
            if not sym.isupper() or not sym.endswith("USDT"):
                continue
            files[p.name] = _sha256(p)
    if not files:
        raise FileNotFoundError(f"no <SYM>_(15m|1h).parquet under {source_root}")
    return SourceManifest(root=source_root, files=files)


def load_symbol(
    symbol: str,
    timeframe: str,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> pd.DataFrame:
    sym = symbol.upper()
    cache = data_dir / f"{sym}__{timeframe}.parquet"
    src = source_root / _source_filename(sym, timeframe)
    if not src.exists():
        raise FileNotFoundError(f"missing {timeframe} source for {sym}: {src}")
    if cache.exists() and not refresh:
        cached = pd.read_parquet(cache)
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
        cached.index.name = "openTime"
        return cached
    df = _read_source(sym, timeframe, source_root)
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    return df


def load_symbol_multi_tf(
    symbol: str,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(df_15m, df_1h)`` for one symbol. Both frames are
    normalized OHLCV with UTC ``openTime`` index."""
    df_15m = load_symbol(symbol, "15m", source_root, data_dir, refresh=refresh)
    df_1h = load_symbol(symbol, "1h", source_root, data_dir, refresh=refresh)
    return df_15m, df_1h


def load_all(
    symbols: Optional[Iterable[str]] = None,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Load 15m + 1h for every symbol. Returns ``{symbol: {"15m": df_15m, "1h": df_1h}}``."""
    cfg = json.loads(CONFIG_PATH.read_text())
    if symbols is None:
        symbols = cfg["instruments"]
    out: Dict[str, Dict[str, pd.DataFrame]] = {}
    for sym in symbols:
        df_15m, df_1h = load_symbol_multi_tf(sym, source_root, data_dir, refresh=refresh)
        out[sym] = {"15m": df_15m, "1h": df_1h}
    return out


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    manifest = build_source_manifest(DEFAULT_SOURCE_ROOT)
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    manifest.write(manifest_path)

    drift = manifest.verify()
    print(f"Source manifest written: {manifest_path} ({len(manifest.files)} files)")
    for rel, sha in sorted(manifest.files.items()):
        print(f"  {sha}  {rel}")
    if drift:
        print(f"  !! drift detected on: {drift}", flush=True)

    for sym in cfg["instruments"]:
        for tf in ("15m", "1h"):
            df = load_symbol(sym, tf, refresh=True)
            print(
                f"  cached  {sym:<8} tf={tf:<3} rows={len(df)} "
                f"span={df.index[0].date()}..{df.index[-1].date()}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())