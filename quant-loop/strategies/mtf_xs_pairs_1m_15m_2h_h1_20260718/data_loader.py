"""Data loader for mtf_xs_pairs_1m_15m_2h_h1_20260718 (H1 — xs-pair z-score).

Loads native 1m parquet per symbol. 15m and 2h aggregation happens in
strategy.py via aggregate_ohlcv (built from the same 1m bars, no
look-ahead).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_1m(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / (symbol + "__1m.parquet")
    if not p.is_file():
        raise SystemExit("missing 1m data parquet: " + str(p))
    df = pd.read_parquet(p)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SystemExit(symbol + ": index is not datetime")
    df.index.name = "openTime"
    return df.sort_index()


def load_all(symbols):
    """Return dict symbol -> 1m OHLCV DataFrame."""
    return {sym: _load_1m(sym) for sym in symbols}


def load_funding(symbols):  # pragma: no cover (H1 doesn't use funding)
    """Funding loader is unused by H1; returns empty dict to satisfy API."""
    return {}
