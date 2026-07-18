"""Data loader for trend_regime_gate_1d_adx_4h_1h_20260714 (iter#101, V1).

Loads canonical 4h klines and resamples them to 1d for the regime filter
(ADX). 1h klines are the entry frame. We rely on the canonical
``live_data/`` directory, which has BTCUSDT, ETHUSDT and SOLUSDT at 1h
and 4h. 1d is derived (deterministic resample from 4h: 6 bars = 1 day)
to avoid an external data dependency.

The loader refuses to run with ``LIVE_TRADING=1``.
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


# ---------------------------------------------------------------------------
# Manifest helpers (kept compatible with the multica canonical pattern).
# ---------------------------------------------------------------------------

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
        drift: list = []
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


def load_symbol_1h(
    symbol: str,
    source_root: Path = DEFAULT_LIVE_DATA_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> pd.DataFrame:
    cache = data_dir / f"{symbol.upper()}__1h.parquet"
    if cache.exists() and not refresh:
        cached = pd.read_parquet(cache)
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
        cached.index.name = "openTime"
        return cached
    df = _read_source(symbol, "1h", source_root)
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    return df


def load_symbol_4h(
    symbol: str,
    source_root: Path = DEFAULT_LIVE_DATA_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> pd.DataFrame:
    cache = data_dir / f"{symbol.upper()}__4h.parquet"
    if cache.exists() and not refresh:
        cached = pd.read_parquet(cache)
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
        cached.index.name = "openTime"
        return cached
    df = _read_source(symbol, "4h", source_root)
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    return df


def resample_to_1d(df_4h: pd.DataFrame) -> pd.DataFrame:
    """Deterministic resample of 4h bars into 1d bars: 6 x 4h = 1 day."""
    out = df_4h.resample("1D", label="right", closed="right").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["close"])
    out.index.name = "openTime"
    return out


def load_symbol_multi(
    symbol: str,
    source_root: Path = DEFAULT_LIVE_DATA_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return ``(df_1h, df_4h, df_1d)`` for one symbol. The 1d frame is
    derived from the 4h frame by deterministic resample (no extra fetch)."""
    df_1h = load_symbol_1h(symbol, source_root, data_dir, refresh=refresh)
    df_4h = load_symbol_4h(symbol, source_root, data_dir, refresh=refresh)
    df_1d = resample_to_1d(df_4h)
    return df_1h, df_4h, df_1d


def load_all(
    symbols: Optional[Iterable[str]] = None,
    source_root: Path = DEFAULT_LIVE_DATA_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    cfg = json.loads(CONFIG_PATH.read_text())
    if symbols is None:
        symbols = cfg["instruments"]
    out: Dict[str, Dict[str, pd.DataFrame]] = {}
    for sym in symbols:
        df_1h, df_4h, df_1d = load_symbol_multi(sym, source_root, data_dir, refresh=refresh)
        out[sym] = {"1h": df_1h, "4h": df_4h, "1d": df_1d}
    return out


def build_source_manifest(source_root: Path = DEFAULT_LIVE_DATA_ROOT) -> SourceManifest:
    files: Dict[str, str] = {}
    for tf in ("1h", "4h"):
        for p in sorted(source_root.glob(f"*_{tf}.parquet")):
            files[p.name] = _sha256(p)
    if not files:
        raise FileNotFoundError(f"no <SYM>_(1h|4h).parquet under {source_root}")
    return SourceManifest(root=source_root, files=files)


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    manifest = build_source_manifest(DEFAULT_LIVE_DATA_ROOT)
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    manifest.write(manifest_path)
    print(f"Source manifest written: {manifest_path} ({len(manifest.files)} files)")
    drift = manifest.verify()
    if drift:
        print(f"  !! drift detected on: {drift}", flush=True)
    for sym in cfg["instruments"]:
        df_1h, df_4h, df_1d = load_symbol_multi(sym, refresh=True)
        print(
            f"  cached  {sym:<8} 1h={len(df_1h):>6} 4h={len(df_4h):>6} 1d={len(df_1d):>5} "
            f"span={df_1h.index[0].date()}..{df_1h.index[-1].date()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())