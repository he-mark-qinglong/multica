"""Data loader for vpvr_mtf_reversion_5m_consensus_20260710 (iter 71).

Resamples 1m canonical parquets to 5m bars. Also pre-computes a 1h
resampled copy for each symbol so the higher-timeframe alignment check
can run without re-resampling during the backtest.

Source layout:
    /home/smark/services/strategy_display_engine_data/canonical/workdir/
        strategies/vpvr_reversion_1m_entryk1.5_20260630/data/
            fapi_BTCUSDT__1m.parquet
            fapi_ETHUSDT__1m.parquet

Output layout (per-symbol cache, this strategy owns it):
    <strategy_dir>/data/
        fapi_<SYM>__5m.parquet
        fapi_<SYM>__1h.parquet
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
    higher_timeframe: str,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, pd.DataFrame]:
    sym = symbol.upper()
    cache_main = data_dir / f"fapi_{sym}__{timeframe}.parquet"
    cache_htf = data_dir / f"fapi_{sym}__{higher_timeframe}.parquet"
    src = source_root / f"fapi_{sym}__1m.parquet"
    if not src.exists():
        raise FileNotFoundError(f"missing 1m source for {sym}: {src}")
    if cache_main.exists() and cache_htf.exists() and not refresh:
        return {
            timeframe: pd.read_parquet(cache_main),
            higher_timeframe: pd.read_parquet(cache_htf),
        }
    df_1m = _read_1m(src)
    out_main = _resample(df_1m, timeframe)
    out_htf = _resample(df_1m, higher_timeframe)
    data_dir.mkdir(parents=True, exist_ok=True)
    out_main.to_parquet(cache_main)
    out_htf.to_parquet(cache_htf)
    return {timeframe: out_main, higher_timeframe: out_htf}


def load_all(
    symbols: Optional[Iterable[str]] = None,
    timeframe: Optional[str] = None,
    higher_timeframe: Optional[str] = None,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    cfg = json.loads(CONFIG_PATH.read_text())
    if symbols is None:
        symbols = cfg["instruments"]
    if timeframe is None:
        timeframe = cfg["timeframe"]
    if higher_timeframe is None:
        higher_timeframe = cfg["mtf"]["higher_timeframe"]
    out: Dict[str, Dict[str, pd.DataFrame]] = {}
    for sym in symbols:
        out[sym] = load_symbol(sym, timeframe, higher_timeframe, source_root, data_dir, refresh=refresh)
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

    htf = cfg["mtf"]["higher_timeframe"]
    for sym in cfg["instruments"]:
        out = load_symbol(sym, cfg["timeframe"], htf, refresh=True)
        main_df = out[cfg["timeframe"]]
        htf_df = out[htf]
        print(
            f"  cached  {sym:<8} tf={cfg['timeframe']:<3} rows={len(main_df)} "
            f"span={main_df.index[0].date()}..{main_df.index[-1].date()} | "
            f"htf={htf:<3} rows={len(htf_df)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())