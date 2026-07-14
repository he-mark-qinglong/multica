"""Data loader for the cross-sectional momentum rank 1d strategy.

Resamples 1m Binance USD-M klines from the canonical source into 1d OHLCV
frames and caches them under ``data/fapi_<SYM>__1d.parquet``. A SHA256
manifest of the upstream source files is emitted so any future ETL swap of
the 1m parquet is detectable on subsequent runs.

Schema (1m input == 1d output):
    index  : openTime, pd.DatetimeIndex, tz=UTC, dtype datetime64[ns]
    cols   : open, high, low, close, volume  (all float64)

Resample rules (1m -> 1d):
    open   = first 1m open of the day
    high   = max  1m high
    low    = min  1m low
    close  = last 1m close
    volume = sum 1m volume

Only symbols listed in either ``target_universe`` OR ``active_universe`` in
config.json get materialized locally; the rest are kept in config so the
strategy is forward-compatible when more symbols get downloaded.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"
STRATEGY_DIR = Path(__file__).parent
DATA_DIR = STRATEGY_DIR / "data"

# Canonical 1m source root. The donchian_breakout_atr_1d_20260709 and
# vpvr_reversion_1m_20260624 strategies read from the same place; we reuse
# the path so the SHA256 manifest comparison is meaningful.
DEFAULT_SOURCE_ROOT = Path(
    "/home/smark/services/strategy_display_engine_data/canonical/"
    "workdir/strategies/vpvr_reversion_1m_20260624/data"
)

# Hard guardrail: paper-trade only. No order-placement code path lives here.
if os.environ.get("LIVE_TRADING") == "1":
    raise SystemExit(
        "data_loader.py is paper-trade only; refusing to run with LIVE_TRADING=1"
    )


@dataclass
class SourceManifest:
    """SHA256 manifest for the upstream 1m parquet files (only the symbols we
    actually consume -- skipping a fully-symbol-wide list keeps the manifest
    diff focused and forward-compatible)."""

    root: Path
    files: Dict[str, str]  # relative_path -> sha256 hex digest

    def write(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w") as fh:
            for rel, sha in sorted(self.files.items()):
                fh.write(f"{sha}  {rel}\n")

    def verify(self) -> List[str]:
        """Return the list of relative paths whose current sha256 differs from
        what was recorded. Empty list = clean."""
        drift: List[str] = []
        for rel, expected in self.files.items():
            cur_path = self.root / rel
            if not cur_path.exists():
                drift.append(rel)
                continue
            current = _sha256(cur_path)
            if current != expected:
                drift.append(rel)
        return drift


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def build_source_manifest(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    symbols: Optional[List[str]] = None,
) -> SourceManifest:
    """Hash the source parquets for the symbols we care about. If ``symbols``
    is omitted, every ``fapi_*USDT__1m.parquet`` under the source root is
    included.
    """
    files: Dict[str, str] = {}
    if symbols is None:
        candidates = sorted(source_root.glob("fapi_*USDT__1m.parquet"))
    else:
        candidates = [source_root / f"fapi_{s.upper()}__1m.parquet" for s in symbols]
    for p in candidates:
        if not p.exists():
            continue
        files[p.name] = _sha256(p)
    if not files:
        raise FileNotFoundError(
            f"no fapi_*USDT__1m.parquet files under {source_root} for symbols {symbols}"
        )
    return SourceManifest(root=source_root, files=files)


def _read_1m(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    expected_cols = {"open", "high", "low", "close", "volume"}
    missing = expected_cols - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    if df.index.name != "openTime":
        df.index.name = "openTime"
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df[["open", "high", "low", "close", "volume"]].sort_index()


def _resample_1d(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Resample 1m bars to daily UTC bars.

    The 1d bar at calendar date D covers 1m bars with openTime in
    [00:00:00 UTC of D, 00:00:00 UTC of D+1). The reported ``close`` is the
    last 1m close of that window and the reported ``open`` is the first 1m
    open.
    """
    if df_1m.empty:
        return df_1m.copy()
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    out = df_1m.resample("1D", label="left", closed="left").agg(agg)
    out = out.dropna(subset=["close"])
    out.index.name = "openTime"
    return out


def load_symbol_1d(
    symbol: str,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return a 1d OHLCV frame for ``symbol``. Caches a parquet copy under
    ``data_dir/fapi_<symbol>__1d.parquet`` so the backtest is reproducible
    even if the source is later modified.
    """
    sym = symbol.upper()
    cache = data_dir / f"fapi_{sym}__1d.parquet"
    src = source_root / f"fapi_{sym}__1m.parquet"
    if not src.exists():
        raise FileNotFoundError(f"missing 1m source for {sym}: {src}")

    if cache.exists() and not refresh:
        return pd.read_parquet(cache)

    df_1m = _read_1m(src)
    df_1d = _resample_1d(df_1m)

    data_dir.mkdir(parents=True, exist_ok=True)
    df_1d.to_parquet(cache)
    return df_1d


def load_all(
    symbols: Optional[List[str]] = None,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, pd.DataFrame]:
    """Load each symbol in the provided list (defaults to ``active_universe``
    in config). Symbols whose 1m source is missing are skipped with a
    warning to stderr -- this lets us add new symbols to config without
    breaking the loader.
    """
    if symbols is None:
        cfg = json.loads(CONFIG_PATH.read_text())
        symbols = list(cfg.get("active_universe") or cfg.get("target_universe") or [])
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            out[sym] = load_symbol_1d(sym, source_root, data_dir, refresh=refresh)
        except FileNotFoundError as exc:
            print(f"  skip {sym}: {exc}", flush=True)
    return out


def main() -> int:
    """CLI: refresh source manifest + 1d caches for ``active_universe``."""
    cfg = json.loads(CONFIG_PATH.read_text())
    symbols = cfg.get("active_universe") or cfg.get("target_universe") or []
    manifest = build_source_manifest(symbols=symbols)
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    manifest.write(manifest_path)

    drift = manifest.verify()
    print(f"Source manifest written: {manifest_path} ({len(manifest.files)} files)")
    for rel, sha in sorted(manifest.files.items()):
        print(f"  {sha}  {rel}")
    if drift:
        print(f"  !! drift detected on: {drift}", flush=True)
    for sym in symbols:
        df = load_symbol_1d(sym, refresh=True)
        print(
            f"  cached  {sym:<8} rows={len(df)} "
            f"span={df.index[0].date()}..{df.index[-1].date()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
