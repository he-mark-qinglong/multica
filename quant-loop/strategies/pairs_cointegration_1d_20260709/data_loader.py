"""Data loader for the pairs-cointegration 1d strategy.

Reads the same canonical 1m Binance USD-M parquet files used by the rest of
quant-loop and resamples them to 1d bars. A SHA256 manifest is emitted at
``data/manifest.parquet.sha256`` so any later ETL replacement of the source
parquet is detectable.

Source layout (canonical):
    /home/smark/services/strategy_display_engine_data/canonical/
        workdir/strategies/vpvr_reversion_1m_20260624/data/
            fapi_BTCUSDT__1m.parquet
            fapi_ETHUSDT__1m.parquet
            fapi_SOLUSDT__1m.parquet

Output layout (per-symbol 1d cache, this strategy owns it):
    <strategy_dir>/data/
        fapi_<SYM>__1d.parquet
        manifest.parquet.sha256   # one line per file: "<sha256>  <relpath>"

Schema contract (1m input and 1d output both use it):
    index  : openTime, pd.DatetimeIndex, tz=UTC, dtype datetime64[ns]
    cols   : open, high, low, close, volume  (all float64)

UNIVERSE NOTE
-------------
The original spec called for 6 symbols (BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT,
ADAUSDT, AVAXUSDT). The canonical 1m source currently only has BTC, ETH, SOL.
We proceed with the 3-symbol universe and document this honestly in the SPEC +
results README rather than fabricate data we don't have. BNB/ADA/AVAX can be
folded in once the canonical ETL is extended; the B1 framework is symbol-agnostic.
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
    "/home/smark/services/strategy_display_engine_data/canonical/"
    "workdir/strategies/vpvr_reversion_1m_20260624/data"
)

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


def build_source_manifest(source_root: Path = DEFAULT_SOURCE_ROOT) -> SourceManifest:
    files: Dict[str, str] = {}
    for p in sorted(source_root.glob("fapi_*USDT__1m.parquet")):
        files[p.name] = _sha256(p)
    if not files:
        raise FileNotFoundError(
            f"no fapi_*USDT__1m.parquet files under {source_root}"
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
    if df_1m.empty:
        return df_1m.copy()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
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
    symbols: Optional[Iterable[str]] = None,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    data_dir: Path = DATA_DIR,
    refresh: bool = False,
) -> Dict[str, pd.DataFrame]:
    if symbols is None:
        cfg = json.loads(CONFIG_PATH.read_text())
        symbols = cfg["instruments"]
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        out[sym] = load_symbol_1d(sym, source_root, data_dir, refresh=refresh)
    return out


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    manifest = build_source_manifest()
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    manifest.write(manifest_path)
    print(f"Source manifest written: {manifest_path} ({len(manifest.files)} files)")
    for rel, sha in sorted(manifest.files.items()):
        print(f"  {sha[:16]}...  {rel}")
    drift = manifest.verify()
    if drift:
        print(f"  !! drift detected on: {drift}", flush=True)
    # NOTE: pass `data_dir=DATA_DIR` explicitly here rather than relying on
    # the function default, because monkeypatching module-level DATA_DIR at
    # test time does not affect function defaults (which are captured at
    # definition time). Explicit threading keeps the test redictable.
    for sym in cfg["instruments"]:
        df = load_symbol_1d(sym, data_dir=DATA_DIR, refresh=True)
        print(
            f"  cached  {sym:<8} rows={len(df)} "
            f"span={df.index[0].date()}..{df.index[-1].date()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())