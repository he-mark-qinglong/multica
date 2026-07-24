"""Data loader for vpvr_funding_carry_asym_v2 (SMA-34990).

Loads BTCUSDT 1m / 15m / 4h OHLCV (canonical shared pool) and merges
the funding events onto each TF's bar index via ffill. Returns a dict
of per-TF frames plus the per-event funding frame.

All shared-pool locations follow ``AGENTS.md`` §1:

  1m: ``data/perp_1m/{symbol}_1m.parquet``
  15m: ``live_data/{symbol}_15m.parquet``
  4h: ``live_data/{symbol}_4h.parquet``
  funding: ``data/funding/{symbol}.parquet``
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

QUANT_LOOP = Path("/home/smark/multica/quant-loop")
LIVE_DATA = QUANT_LOOP / "live_data"
PERP_1M = QUANT_LOOP / "data" / "perp_1m"
FUNDING_DIR = QUANT_LOOP / "data" / "funding"
DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_ohlcv(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["__ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
        df = df.set_index("__ts").drop(columns=["open_time"])
    elif "ts" in df.columns:
        df["__ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.set_index("__ts").drop(columns=["ts"])
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep].astype(np.float64)


def _load_funding_events() -> pd.DataFrame:
    candidates = [
        FUNDING_DIR / "BTCUSDT.parquet",
        FUNDING_DIR / "BTCUSDT_bybit_funding.parquet",
    ]
    for p in candidates:
        if p.exists():
            fdf = pd.read_parquet(p)
            if "ts" in fdf.columns:
                fdf["__ts"] = pd.to_datetime(fdf["ts"], utc=True)
            elif "fundingTime" in fdf.columns:
                fdf["__ts"] = pd.to_datetime(fdf["fundingTime"], unit="ms", utc=True)
            else:
                raise ValueError(f"funding parquet {p} has no ts/fundingTime column")
            fdf = fdf.set_index("__ts").sort_index()
            if fdf.index.tz is not None:
                fdf.index = fdf.index.tz_convert(None)
            return fdf[["fundingRate"]].astype(np.float64)
    raise FileNotFoundError("no BTCUSDT funding parquet found")


def load_tf(symbol: str, tf: str) -> pd.DataFrame:
    if tf == "1m":
        path = PERP_1M / f"{symbol}_1m.parquet"
    elif tf == "15m":
        path = LIVE_DATA / f"{symbol}_15m.parquet"
    elif tf == "4h":
        path = LIVE_DATA / f"{symbol}_4h.parquet"
    else:
        raise ValueError(f"unsupported tf {tf!r}")
    if not path.exists():
        raise FileNotFoundError(f"no {tf} parquet for {symbol} at {path}")
    return _load_ohlcv(path)


def load_funding(symbol: str) -> pd.DataFrame:
    """Symbol-scoped funding events."""
    if symbol != "BTCUSDT":
        raise NotImplementedError("V2 first cut is BTCUSDT-only")
    return _load_funding_events()


def load_all(symbol: str, tfs: List[str]) -> Dict[str, pd.DataFrame]:
    """Load all requested TFs for ``symbol`` plus its funding events."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out: Dict[str, pd.DataFrame] = {}
    for tf in tfs:
        out[tf] = load_tf(symbol, tf)
    out["funding"] = load_funding(symbol)
    manifest_lines = [
        f"{symbol}\t{tf}\t{len(df)}\t{df.index.min()}\t{df.index.max()}"
        for tf, df in out.items() if tf != "funding"
    ]
    manifest_lines.append(
        f"{symbol}\tfunding\t{len(out['funding'])}\t"
        f"{out['funding'].index.min()}\t{out['funding'].index.max()}"
    )
    sha = hashlib.sha256(
        pd.util.hash_pandas_object(pd.concat(
            [df.reset_index() for tf, df in out.items()], axis=0
        ), index=False).values
    ).hexdigest()[:16]
    (DATA_DIR / "manifest.txt").write_text("\n".join(manifest_lines) + f"\nsha={sha}\n")
    return out


__all__ = ["load_tf", "load_funding", "load_all", "QUANT_LOOP"]