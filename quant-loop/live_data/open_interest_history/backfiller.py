"""Open-interest history backfiller (network layer).

Paginates ``fetchOpenInterestHistory`` over a ``[start_ms, end_ms)`` window
and merges results into the parquet store via :class:`OpenInterestDataManager`.
The paging is purely arithmetic (see ``_helpers.windowed_iter``); this
module only adds the ccxt call, retry/backoff, and idempotent merge.

Migrated verbatim from ``trading/src/data/open_interest_history.py``
``OIBackfiller`` at
``da0020de89575c0694b5763c0628a486612d6256``.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import List, Optional, Sequence

import ccxt
import pandas as pd

from ._helpers import (
    MAX_ROWS_PER_CALL,
    SUPPORTED_PERIODS,
    Window,
    _period_to_seconds,
    windowed_iter,
)
from .manager import OpenInterestDataManager

logger = logging.getLogger(__name__)


class OIBackfiller:
    """Backfill historical open interest for one exchange at a time.

    Parameters
    ----------
    exchange_id:
        Either ``"binance"`` (USDT-M) or ``"okx"``. Used both to look up
        the right ccxt class and to namespace the parquet output.
    rate_limit_sleep_factor:
        Multiplier on top of ccxt's auto rate-limit. Keep at 1.0 unless
        you are running many parallel backfillers.
    max_retries:
        How many times to retry a single request before giving up.
    proxies:
        Optional ``{"http": ..., "https": ...}`` proxy dict forwarded to
        ccxt. Leave ``None`` to use the system default.
    """

    EXCHANGES: tuple = ("binance", "okx")

    def __init__(
        self,
        exchange_id: str = "binance",
        *,
        rate_limit_sleep_factor: float = 1.0,
        max_retries: int = 5,
        proxies: Optional[dict] = None,
    ) -> None:
        if exchange_id not in self.EXCHANGES:
            raise ValueError(
                f"Unsupported exchange_id {exchange_id!r}; "
                f"expected one of {self.EXCHANGES}."
            )
        self.exchange_id = exchange_id
        self.rate_limit_sleep_factor = rate_limit_sleep_factor
        self.max_retries = max_retries

        ccxt_id = "binanceusdm" if exchange_id == "binance" else "okx"
        config: dict = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "adjustForTimeDifference": True,
            },
        }
        if proxies:
            config["proxies"] = proxies
        if exchange_id == "okx":
            config["hostname"] = "www.okx.com"

        self.exchange = getattr(ccxt, ccxt_id)(config)

    # ----- symbol formatting -----
    def format_symbol(self, symbol: str) -> Optional[str]:
        """Translate a friendly symbol into ccxt's unified format.

        Returns ``None`` on input we cannot parse; callers must handle that
        explicitly rather than receive a silently-wrong symbol.
        """
        if not symbol:
            logger.error("format_symbol: empty input")
            return None
        try:
            if self.exchange_id == "binance":
                # ETH-USDT-SWAP / ETH-USDT / ETH/USDT -> ETH/USDT:USDT
                base = symbol.split("-")[0].split("/")[0]
                if not base:
                    return None
                return f"{base}/USDT:USDT"
            if self.exchange_id == "okx":
                # OKX linear swap: ETH/USDT:USDT (same as binance for our symbols)
                base = symbol.split("-")[0].split("/")[0]
                if not base:
                    return None
                return f"{base}/USDT:USDT"
            return None
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("format_symbol(%r) failed: %s", symbol, exc)
            return None

    # ----- single-window fetch with retry -----
    def _fetch_window(
        self,
        ccxt_symbol: str,
        period: str,
        since_ms: int,
        until_ms: int,
    ) -> List[dict]:
        """Fetch one [since_ms, until_ms) window with retries + backoff."""
        attempt = 0
        backoff_base = 0.5
        while attempt <= self.max_retries:
            try:
                # ccxt paginates internally only if `paginate=True`.
                # We hand it explicit `since` + `until` so we get a single
                # deterministic page per call; the chunking is done by us.
                rows = self.exchange.fetch_open_interest_history(
                    ccxt_symbol,
                    timeframe=period,
                    since=since_ms,
                    limit=MAX_ROWS_PER_CALL,
                    params={"until": until_ms},
                )
                if rows is None:
                    return []
                # ccxt returns ascending order; filter defensively in case
                # the exchange ever flips it.
                rows = [
                    r for r in rows
                    if since_ms <= int(r["timestamp"]) < until_ms
                ]
                rows.sort(key=lambda r: r["timestamp"])
                return rows
            except Exception as exc:
                attempt += 1
                wait = min(10.0, backoff_base * (2 ** (attempt - 1)))
                wait += random.uniform(0, 0.1 * wait)
                logger.warning(
                    "fetch_open_interest_history error %s (attempt %d/%d) "
                    "for %s %s [%d, %d): %s. Backoff %.2fs",
                    type(exc).__name__, attempt, self.max_retries,
                    ccxt_symbol, period, since_ms, until_ms, exc, wait,
                )
                time.sleep(wait * self.rate_limit_sleep_factor)
        logger.error(
            "fetch_open_interest_history gave up after %d attempts for "
            "%s %s [%d, %d)",
            self.max_retries, ccxt_symbol, period, since_ms, until_ms,
        )
        return []

    # ----- merge new rows into existing parquet -----
    @staticmethod
    def _to_dataframe(rows: Sequence[dict]) -> pd.DataFrame:
        """Normalise raw ccxt rows into a DatetimeIndex OI DataFrame."""
        if not rows:
            return pd.DataFrame(columns=["sumOpenInterest", "sumOpenInterestValue",
                                         "countOpenInterest"])
        df = pd.DataFrame(rows)
        # ccxt gives us: timestamp, symbol, baseVolume, quoteVolume,
        # sumOpenInterest, sumOpenInterestValue, countOpenInterest...
        # Keep only the fields the user actually wants to query.
        keep = [
            c for c in (
                "sumOpenInterest",
                "sumOpenInterestValue",
                "countOpenInterest",
            )
            if c in df.columns
        ]
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
        df = df[keep]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df

    @staticmethod
    def _merge(existing: Optional[pd.DataFrame],
               new: pd.DataFrame) -> pd.DataFrame:
        """Concatenate + dedupe by index, keeping the latest value."""
        if existing is None or existing.empty:
            combined = new.copy()
        else:
            combined = pd.concat([existing, new])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        return combined

    # ----- public entry point -----
    def backfill(
        self,
        symbol: str,
        *,
        period: str = "5m",
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
        manager: OpenInterestDataManager,
        save: bool = True,
    ) -> pd.DataFrame:
        """Backfill ``[start_ms, end_ms)`` of OI for ``symbol`` at ``period``.

        Parameters
        ----------
        symbol:
            Friendly ticker (``"BTC"`` / ``"ETH-USDT-SWAP"``). Will be
            routed through :meth:`format_symbol`.
        period:
            One of :data:`live_data.open_interest_history.SUPPORTED_PERIODS`.
            Default ``"5m"``.
        start_ms / end_ms:
            Unix-millisecond bounds. If omitted, defaults to:

            * ``end_ms`` -> now (UTC)
            * ``start_ms`` -> whatever the local parquet file's earliest
              timestamp is, or 30 days back if no file exists.

            Either both must be provided or neither.
        manager:
            Persistence layer. Must be supplied (even with ``save=False``)
            because we read the existing parquet to know where to resume.
        save:
            If ``True`` (default), persist merged result back to parquet.

        Returns
        -------
        pd.DataFrame
            The merged OI frame, sorted ascending, with a DatetimeIndex.
        """
        _period_to_seconds(period)  # validate early

        ccxt_symbol = self.format_symbol(symbol)
        if ccxt_symbol is None:
            raise ValueError(f"Cannot format symbol: {symbol!r}")

        now_ms = int(time.time() * 1000)
        if end_ms is None:
            end_ms = now_ms
        if start_ms is None:
            existing = manager.load(self.exchange_id, symbol, period)
            if existing is not None and not existing.empty:
                start_ms = int(existing.index[0].timestamp() * 1000)
            else:
                # default: 30 days back, fits in one 5m request
                start_ms = now_ms - 30 * 24 * 60 * 60 * 1000

        existing = manager.load(self.exchange_id, symbol, period)

        all_rows: List[dict] = []
        for window in windowed_iter(start_ms, end_ms, period):
            rows = self._fetch_window(
                ccxt_symbol, period, window.since_ms, window.until_ms,
            )
            logger.info(
                "%s %s window [%s, %s) -> %d rows",
                symbol, period,
                datetime.fromtimestamp(window.since_ms / 1000, tz=timezone.utc),
                datetime.fromtimestamp(window.until_ms / 1000, tz=timezone.utc),
                len(rows),
            )
            all_rows.extend(rows)

        new_df = self._to_dataframe(all_rows)
        merged = self._merge(existing, new_df)
        if save:
            manager.save(self.exchange_id, symbol, period, merged)
        return merged


__all__ = [
    "OIBackfiller",
    "Window",
    "SUPPORTED_PERIODS",
]