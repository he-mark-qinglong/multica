"""Parquet I/O for open-interest history.

This module wraps the on-disk layout so the network layer
(``backfiller.py``) can stay focused on ccxt. The layout mirrors the
K-line ``DataManager`` convention: one parquet file per
``(exchange, symbol, period)`` triple.

Layout::

    {base_path}/{exchange_id}_{safe_symbol}/{period}.parquet

Migrated verbatim from ``trading/src/data/open_interest_history.py``
``OpenInterestDataManager`` at
``da0020de89575c0694b5763c0628a486612d6256``.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class OpenInterestDataManager:
    """Round-trip open-interest parquet files under ``base_path``.

    Layout::

        {base_path}/{exchange_id}_{safe_symbol}/{period}.parquet

    Mirrors the layout used by the K-line ``DataManager`` so downstream
    consumers can locate files uniformly.
    """

    def __init__(self, base_path: str = "./data/open_interest"):
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)

    # ----- path helpers -----
    def _safe_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "_").replace("-", "_").replace(":", "_")

    def _dir(self, exchange_id: str, symbol: str) -> str:
        return f"{self.base_path}/{exchange_id}_{self._safe_symbol(symbol)}"

    def path_for(self, exchange_id: str, symbol: str, period: str) -> str:
        return f"{self._dir(exchange_id, symbol)}/{period}.parquet"

    # ----- read / write -----
    def save(self, exchange_id: str, symbol: str, period: str,
             df: pd.DataFrame) -> str:
        if df is None or df.empty:
            logger.info("save() called with empty df; skipping %s %s %s",
                        exchange_id, symbol, period)
            return self.path_for(exchange_id, symbol, period)
        os.makedirs(self._dir(exchange_id, symbol), exist_ok=True)
        path = self.path_for(exchange_id, symbol, period)
        df.to_parquet(path)
        logger.info("saved %d rows -> %s", len(df), path)
        return path

    def load(self, exchange_id: str, symbol: str, period: str) -> Optional[pd.DataFrame]:
        path = self.path_for(exchange_id, symbol, period)
        if not os.path.exists(path):
            return None
        df = pd.read_parquet(path)
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df = df.set_index("timestamp")
            else:
                df.index = pd.to_datetime(df.index)
        return df.sort_index()

    def exists(self, exchange_id: str, symbol: str, period: str) -> bool:
        return os.path.exists(self.path_for(exchange_id, symbol, period))