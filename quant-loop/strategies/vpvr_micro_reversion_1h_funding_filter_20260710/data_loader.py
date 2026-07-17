"""Data loader for vpvr_micro_reversion_1h_funding_filter_20260710.

Reads the canonical 1h Binance USD-M parquet for BTCUSDT and returns a
tz-aware UTC OHLCV frame indexed by ``openTime``. A SHA256 manifest is
written to ``data/manifest.parquet.sha256`` so any later ETL swap of the
source parquet is detectable.

Source layout (this strategy owns its own canonical 1h file):
    <strategy_dir>/data/fapi_BTCUSDT__1h.parquet

Schema contract:
    index  : openTime, pd.DatetimeIndex, tz=UTC, dtype datetime64[ns]
    cols   : open, high, low, close, volume  (all float64)
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

STRATEGY_DIR = Path(__file__).parent
DATA_DIR = STRATEGY_DIR / "data"

# Hard guardrail: paper-trade only. There is no code path here that can place
# a real order. Set LIVE_TRADING=1 to fail loudly if someone wires this loader
# into a live pipeline by mistake.
if os.environ.get("LIVE_TRADING") == "1":
    raise SystemExit(
        "data_loader.py is paper-trade only; refusing to run with LIVE_TRADING=1"
    )


@dataclass
class SourceManifest:
    """SHA256 manifest for the upstream 1h parquet files."""

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


def build_source_manifest(
    data_dir: Path = DATA_DIR,
) -> SourceManifest:
    files: Dict[str, str] = {}
    for p in sorted(data_dir.glob("fapi_*USDT__1h.parquet")):
        files[p.name] = _sha256(p)
    if not files:
        raise FileNotFoundError(
            f"no fapi_*USDT__1h.parquet files under {data_dir}"
        )
    return SourceManifest(root=data_dir, files=files)


def _read_1h(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    expected_cols = {"open", "high", "low", "close", "volume"}
    missing = expected_cols - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    if df.index.name != "openTime":
        df = df.reset_index()
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], utc=True)
            df = df.set_index("date")
    df.index.name = "openTime"
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    out = df[["open", "high", "low", "close", "volume"]].sort_index()
    return out


def load_symbol_1h(
    symbol: str,
    data_dir: Path = DATA_DIR,
) -> pd.DataFrame:
    sym = symbol.upper()
    src = data_dir / f"fapi_{sym}__1h.parquet"
    if not src.exists():
        raise FileNotFoundError(f"missing 1h source for {sym}: {src}")
    return _read_1h(src)


def load_all(
    symbols: Optional[list[str]] = None,
    data_dir: Path = DATA_DIR,
) -> Dict[str, pd.DataFrame]:
    if symbols is None:
        return {"BTCUSDT": load_symbol_1h("BTCUSDT", data_dir=data_dir)}
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        out[sym] = load_symbol_1h(sym, data_dir=data_dir)
    return out


def main() -> int:
    """CLI: emit manifest so any later drift is detectable."""
    manifest = build_source_manifest()
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    manifest.write(manifest_path)
    drift = manifest.verify()
    print(f"Source manifest written: {manifest_path} ({len(manifest.files)} files)")
    for rel, sha in sorted(manifest.files.items()):
        print(f"  {sha}  {rel}")
    if drift:
        print(f"  !! drift detected on: {drift}", flush=True)
    df = load_symbol_1h("BTCUSDT")
    print(
        f"  loaded BTCUSDT rows={len(df)} "
        f"span={df.index[0].isoformat()}..{df.index[-1].isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())