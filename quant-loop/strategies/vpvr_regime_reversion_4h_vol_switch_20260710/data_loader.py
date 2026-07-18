"""Data loader for vpvr_regime_reversion_4h_vol_switch_20260710 (iter 72).

Resamples 1m canonical parquets to 4h bars. Writes a SHA256 manifest of the
upstream 1m source so any ETL drift in the canonical dir is detected.

Source layout:
    /home/smark/services/strategy_display_engine_data/canonical/workdir/
        strategies/vpvr_reversion_1m_entryk1.5_20260630/data/
            fapi_SOLUSDT__1m.parquet

Output layout (per-symbol 4h cache, this strategy owns it):
    <strategy_dir>/data/
        fapi_<SYM>__4h.parquet
        manifest.parquet.sha256
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

DEFAULT_SOURCE_ROOT = Path(
    "/home/smark/services/strategy_display_engine_data/canonical/workdir/"
    "strategies/vpvr_reversion_1m_entryk1.5_20260630/data"
)

if os.environ.get("LIVE_TRADING") == "1":
    raise SystemExit("data_loader.py is paper-trade only; refusing LIVE_TRADING=1")


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


def build_source_manifest(source_root: Path = DEFAULT_SOURCE_ROOT) -> SourceManifest:
    files: Dict[str, str] = {}
    for p in sorted(source_root.glob("fapi_*USDT__1m.parquet")):
        files[p.name] = _sha256(p)
    if not files:
        raise FileNotFoundError(f"no fapi_*USDT__1m.parquet files under {source_root}")
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


def _resample(df_1m: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df_1m.empty:
        return df_1m.copy()
    pandas_rule = _to_pandas_freq(rule)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df_1m.resample(pandas_rule, label="left", closed="left").agg(agg)
    out = out.dropna(subset=["close"])
    out.index.name = "openTime"
    return out


def _to_pandas_freq(tf: str) -> str:
    tf = tf.strip().lower()
    mapping = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1D",
    }
    if tf in mapping:
        return mapping[tf]
    return tf


def load_symbol(
    symbol: str,
    timeframe: str,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> pd.DataFrame:
    sym = symbol.upper()
    cache = data_dir / f"fapi_{sym}__{timeframe}.parquet"
    src = source_root / f"fapi_{sym}__1m.parquet"
    if not src.exists():
        raise FileNotFoundError(f"missing 1m source for {sym}: {src}")
    if cache.exists() and not refresh:
        return pd.read_parquet(cache)
    df_1m = _read_1m(src)
    df_out = _resample(df_1m, timeframe)
    data_dir.mkdir(parents=True, exist_ok=True)
    df_out.to_parquet(cache)
    return df_out


def load_all(
    symbols: Optional[Iterable[str]] = None,
    timeframe: Optional[str] = None,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, pd.DataFrame]:
    cfg = json.loads(CONFIG_PATH.read_text())
    if symbols is None:
        symbols = cfg["instruments"]
    if timeframe is None:
        timeframe = cfg["timeframe"]
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        out[sym] = load_symbol(sym, timeframe, source_root, data_dir, refresh=refresh)
    return out


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    manifest = build_source_manifest()
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    manifest.write(manifest_path)

    drift = manifest.verify()
    print(f"Source manifest written: {manifest_path} ({len(manifest.files)} files)")
    for rel, sha in sorted(manifest.files.items()):
        print(f"  {sha}  {rel}")
    if drift:
        print(f"  !! drift detected on: {drift}", flush=True)

    for sym in cfg["instruments"]:
        df = load_symbol(sym, cfg["timeframe"], refresh=True)
        print(
            f"  cached  {sym:<8} tf={cfg['timeframe']:<3} rows={len(df)} "
            f"span={df.index[0].date()}..{df.index[-1].date()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())