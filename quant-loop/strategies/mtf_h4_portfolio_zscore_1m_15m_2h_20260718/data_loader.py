"""Data loader for mtf_xs_pairs_1m_15m_2h_h4_20260718 (H4 — portfolio)."""

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
    return {sym: _load_1m(sym) for sym in symbols}


def load_funding(symbols):  # pragma: no cover
    return {}