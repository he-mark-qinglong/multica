"""Data loader for vol_breakout_2tf_vpvr_confluence_4h_20260712 (iter#84, single-TF 4h).

V8 reads only 4h Binance spot klines. No 1h data, no resampling.

Source layout (canonical):
    /home/smark/multica/quant-loop/live_data/
        BTCUSDT_4h.parquet
        ETHUSDT_4h.parquet
        SOLUSDT_4h.parquet

Output layout (per-symbol 4h cache, owned by this strategy):
    <strategy_dir>/data/
        <SYM>__4h.parquet            # cached copy with normalized index
        manifest.parquet.sha256      # one line per source 4h file

Schema contract (after normalization):
    index  : openTime, pd.DatetimeIndex, tz=UTC, dtype datetime64[ns]
    cols   : open, high, low, close, volume  (all float64)

The parquet from ``fetch_binance_4h.py`` uses int-ms ``open_time`` as a
regular column (no index). We convert it to a UTC ``DatetimeIndex`` here.

A SHA256 manifest is emitted at ``data/manifest.parquet.sha256`` so any
later re-fetch of the source parquet is detectable.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"
STRATEGY_DIR = Path(__file__).parent
DATA_DIR = STRATEGY_DIR / "data"

# Source location for the canonical Binance spot 4h klines.
DEFAULT_SOURCE_ROOT = Path("/home/smark/multica/quant-loop/live_data")

# Paper-trade guardrail.
if os.environ.get("LIVE_TRADING") == "1":
    raise SystemExit(
        "data_loader.py is paper-trade only; refusing to run with LIVE_TRADING=1"
    )


@dataclass
class SourceManifest:
    """SHA256 manifest for the upstream 4h parquet files."""

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
    """Shared OHLCV normalization: index = openTime (UTC), cols = OHLCV only."""
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


def _read_source(symbol: str, timeframe: str,
                 source_root: Path) -> pd.DataFrame:
    src = source_root / _source_filename(symbol, timeframe)
    if not src.exists():
        raise FileNotFoundError(f"missing {timeframe} source for {symbol}: {src}")
    raw = pd.read_parquet(src)
    return _normalize_ohlcv(raw, src)


def build_source_manifest(
    source_root: Path = DEFAULT_SOURCE_ROOT,
) -> SourceManifest:
    """Walk ``source_root`` and hash every ``<SYM>_4h.parquet``.

    V8 only needs 4h files; 1h parquet under ``source_root`` is ignored.
    """
    files: Dict[str, str] = {}
    for p in sorted(source_root.glob("*_4h.parquet")):
        stem = p.stem
        if "_" not in stem:
            continue
        sym = stem.split("_")[0]
        if not sym.isupper() or not sym.endswith("USDT"):
            continue
        files[p.name] = _sha256(p)
    if not files:
        raise FileNotFoundError(
            f"no <SYM>_4h.parquet files under {source_root}"
        )
    return SourceManifest(root=source_root, files=files)


def load_symbol(
    symbol: str,
    timeframe: str,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return an OHLCV frame for ``symbol`` at ``timeframe``. Caches a
    normalized parquet copy under ``data_dir/<SYM>__<tf>.parquet``."""
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


def load_all(
    symbols: Optional[Iterable[str]] = None,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, pd.DataFrame]:
    """Load 4h per-symbol. Returns ``{symbol: df_4h}``.

    V8 is single-TF: no 1h loading. The strategy only needs 4h.
    """
    cfg = json.loads(CONFIG_PATH.read_text())
    if symbols is None:
        symbols = cfg["instruments"]
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        out[sym] = load_symbol(sym, "4h", source_root, data_dir, refresh=refresh)
    return out


def load_symbol_4h(
    symbol: str,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> pd.DataFrame:
    """Convenience: load just the 4h frame for a symbol (used by tests)."""
    return load_symbol(symbol, "4h", source_root, data_dir, refresh=refresh)


def main() -> int:
    """CLI: emit manifest + refresh per-symbol 4h caches."""
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
        df = load_symbol(sym, "4h", refresh=True)
        print(
            f"  cached  {sym:<8} tf=4h  rows={len(df)} "
            f"span={df.index[0].date()}..{df.index[-1].date()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())