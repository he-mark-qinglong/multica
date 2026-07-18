"""Data loader for vpvr_multi_tf_funding (SMA-34989).

Loads BTCUSDT 1m / 15m / 4h OHLCV bars and merges the Bybit 8h
funding events onto each TF's bar index via ``ffill``. Funding is
exposed as a ``funding`` column; no-look-ahead is enforced later in
``build_signals.py`` via ``shift(1)`` per the cycle-46 convention.

No-look-ahead note
------------------
For the 4h TF the funding snapshot is the same 8h cadence (3 funding
events per day) and the rolling regime classifier (``z_funding``)
uses a 30-day rolling window on the 4h-bar funding series, with the
rolling mean / std shifted by 1 bar.

For the 15m and 1m TF the funding column is ffill-onto-bar from the
8h event series, then ``shift(1)``-ed inside the build_signals
wrapper so bar ``t`` sees the most recent event at-or-before bar
``t``'s open.

OHLCV paths
-----------
The strategy consumes the canonical shared pool per the cycle-46
``AGENTS.md`` rule:

  - 1m:  ``data/perp_1m/BTCUSDT_1m.parquet``
  - 15m: ``live_data/BTCUSDT_15m.parquet``
  - 4h:  ``live_data/BTCUSDT_4h.parquet``
  - funding: ``data/funding/BTCUSDT.parquet``
          (alt: ``funding_analysis/BTCUSDT_bybit_funding.parquet``)
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
ALT_FUNDING = QUANT_LOOP / "funding_analysis" / "BTCUSDT_bybit_funding.parquet"

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_ohlcv(path: Path) -> pd.DataFrame:
    """Load a per-TF OHLCV parquet and normalize the index/columns.

    The shared-pool parquets use ``open_time`` (ms epoch) for the
    timestamp; some legacy copies use ``ts``. We coerce to a
    tz-naive ``DatetimeIndex`` and keep only OHLCV columns.
    """
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


def _load_funding() -> pd.DataFrame:
    """Load Bybit BTCUSDT funding events as a per-event DataFrame."""
    candidates = [
        FUNDING_DIR / "BTCUSDT.parquet",
        FUNDING_DIR / "BTCUSDT_bybit_funding.parquet",
        ALT_FUNDING,
    ]
    for p in candidates:
        if p.exists():
            fdf = pd.read_parquet(p)
            if "ts" in fdf.columns:
                fdf["__ts"] = pd.to_datetime(fdf["ts"], utc=True)
            elif "fundingTime" in fdf.columns:
                fdf["__ts"] = pd.to_datetime(fdf["fundingTime"], unit="ms", utc=True)
            else:
                raise ValueError(
                    f"funding parquet {p} has no ts/fundingTime column"
                )
            fdf = fdf.set_index("__ts").sort_index()
            if fdf.index.tz is not None:
                fdf.index = fdf.index.tz_convert(None)
            return fdf[["fundingRate"]].astype(np.float64)
    raise FileNotFoundError(
        f"no BTCUSDT funding parquet found in {[str(p) for p in candidates]}"
    )


def _attach_funding(df: pd.DataFrame, funding_events: pd.DataFrame) -> pd.DataFrame:
    """FFill the funding event series onto the bar index.

    The bar at ``t`` receives the most recent funding event whose
    timestamp is <= ``t`` (cycle-46 convention). The ``shift(1)``
    that enforces no-look-ahead is applied inside ``build_signals``.
    """
    funding = funding_events["fundingRate"].reindex(df.index, method="ffill")
    out = df.copy()
    out["funding"] = funding.fillna(0.0).astype(np.float64)
    return out


def load_tf(symbol: str, tf: str) -> pd.DataFrame:
    """Load a single (symbol, timeframe) frame with funding merged.

    Args:
        symbol: only ``BTCUSDT`` is supported in v1.
        tf: one of ``"1m"``, ``"15m"``, ``"4h"``.
    """
    if symbol != "BTCUSDT":
        raise ValueError(
            f"only BTCUSDT supported in v1 (got {symbol!r}); see SPEC §Universe"
        )
    if tf == "1m":
        path = PERP_1M / f"{symbol}_1m.parquet"
    elif tf == "15m":
        path = LIVE_DATA / f"{symbol}_15m.parquet"
    elif tf == "4h":
        path = LIVE_DATA / f"{symbol}_4h.parquet"
    else:
        raise ValueError(f"unsupported tf {tf!r} (expected 1m/15m/4h)")

    if not path.exists():
        raise FileNotFoundError(f"no OHLCV parquet for {symbol} {tf} at {path}")
    df = _load_ohlcv(path)
    funding_events = _load_funding()
    return _attach_funding(df, funding_events)


def load_all(tfs: List[str]) -> Dict[str, pd.DataFrame]:
    """Load all TFs for BTCUSDT and write a per-strategy data manifest."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out: Dict[str, pd.DataFrame] = {}
    for tf in tfs:
        out[tf] = load_tf("BTCUSDT", tf)
    lines = []
    for tf, df in out.items():
        sha = hashlib.sha256(
            pd.util.hash_pandas_object(df.reset_index(), index=False).values
        ).hexdigest()[:16]
        lines.append(
            f"BTCUSDT\t{tf}\t{len(df)}\t{df.index.min()}\t{df.index.max()}\t{sha}"
        )
    (DATA_DIR / "manifest.txt").write_text("\n".join(lines) + "\n")
    return out


__all__ = ["load_tf", "load_all", "QUANT_LOOP", "DATA_DIR"]