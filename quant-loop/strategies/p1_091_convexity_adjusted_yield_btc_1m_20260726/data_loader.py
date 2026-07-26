"""Data loader for convexity_adjusted_yield (SMA-36109).

Loads BTCUSDT 1m OHLCV from the canonical shared pool per `quant-loop/AGENTS.md` §1:

  - 1m:      ``data/perp_1m/BTCUSDT_1m.parquet``
  - funding: ``data/funding/BTCUSDT.parquet``

The funding data is observed at the 8h event boundary (00:00 / 08:00 /
16:00 UTC). We **forward-fill** the funding rate onto each 1m bar so
``bars[f]`` always carries the funding rate that would apply to a
position opened at bar ``f``. This is the correct convention for back-
testing a funding-aware strategy: holding through an event captures the
last-observed funding amount.

Returns a single DataFrame with a tz-naive ``DatetimeIndex`` (the
convention used by the canonical examples in ``vpvr_multi_tf_funding``
and ``vpvr_funding_carry_asym_v2``) with the OHLCV columns plus a
``fundingRate`` column (forward-filled).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

try:
    from _shared.paths import quant_loop_root
except ImportError:  # bare-script mode
    _QL = str(Path(__file__).resolve().parents[2])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import quant_loop_root

QUANT_LOOP = quant_loop_root()
PERP_1M = QUANT_LOOP / "data" / "perp_1m"
FUNDING = QUANT_LOOP / "data" / "funding"
DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_ohlcv(path: Path) -> pd.DataFrame:
    """Load the canonical 1m perp parquet.

    The shared-pool parquets use ``open_time`` (ms epoch) for the
    timestamp. We coerce to a tz-naive ``DatetimeIndex`` and keep only
    the OHLCV columns.
    """
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["__ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
        df = df.set_index("__ts").drop(columns=["open_time"])
    elif "ts" in df.columns:
        df["__ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.set_index("__ts").drop(columns=["ts"])
    else:
        raise ValueError(f"perp_1m parquet {path} has no open_time/ts column")
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep].astype(np.float64)


def _load_funding(path: Path) -> pd.Series:
    """Load the canonical 8h funding file as a tz-naive-indexed Series.

    Returns a Series indexed by the funding-event timestamp, with values
    equal to ``fundingRate`` (the 8h per-unit rate, e.g. ``0.0001`` for
    1bp per 8h).
    """
    df = pd.read_parquet(path)
    if "ts" in df.columns:
        df["__ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.set_index("__ts").drop(columns=["ts"])
    else:
        raise ValueError(f"funding parquet {path} has no ts column")
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    if "fundingRate" not in df.columns:
        raise ValueError(f"funding parquet {path} has no fundingRate column")
    out = df["fundingRate"].astype(np.float64)
    return out


def _attach_funding(bars: pd.DataFrame, funding: pd.Series) -> pd.DataFrame:
    """Forward-fill funding onto each 1m bar.

    Bars that **pre-date** the first funding event receive `NaN`
    funding (the strategy will treat them as warm-up). Bars **after**
    the last funding event carry the last observed value (forward-fill
    is the correct convention for the "funding in effect right now"
    semantics).
    """
    bars = bars.copy()
    ff = funding.reindex(bars.index, method="ffill")
    bars["fundingRate"] = ff
    return bars


def load_tf(symbol: str, tf: str) -> pd.DataFrame:
    """Load a single (symbol, timeframe) frame with funding attached.

    Args:
        symbol: only ``BTCUSDT`` is supported.
        tf: only ``1m`` is supported.
    """
    if symbol != "BTCUSDT":
        raise ValueError(
            f"only BTCUSDT supported (got {symbol!r}); see SPEC §Data"
        )
    if tf != "1m":
        raise ValueError(f"only 1m is supported (got {tf!r})")
    bars_path = PERP_1M / f"{symbol}_1m.parquet"
    funding_path = FUNDING / f"{symbol}.parquet"
    if not bars_path.exists():
        raise FileNotFoundError(f"no 1m parquet for {symbol} at {bars_path}")
    if not funding_path.exists():
        raise FileNotFoundError(f"no funding parquet for {symbol} at {funding_path}")
    bars = _load_ohlcv(bars_path)
    funding = _load_funding(funding_path)
    return _attach_funding(bars, funding)


def load_all(symbol: str, tfs: list) -> Dict[str, pd.DataFrame]:
    """Load all requested TFs for ``symbol`` and write a per-strategy manifest."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out: Dict[str, pd.DataFrame] = {}
    for tf in tfs:
        out[tf] = load_tf(symbol, tf)
    manifest_lines = [
        f"{symbol}\t{tf}\t{len(df)}\t{df.index.min()}\t{df.index.max()}\t"
        f"funding_nan_pct={df['fundingRate'].isna().mean():.4f}"
        for tf, df in out.items()
    ]
    (DATA_DIR / "manifest.txt").write_text("\n".join(manifest_lines) + "\n")
    return out


__all__ = ["load_tf", "load_all", "QUANT_LOOP", "DATA_DIR"]
