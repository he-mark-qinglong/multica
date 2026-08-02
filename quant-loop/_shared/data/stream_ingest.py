"""Realtime aggTrade stream ingestion (F16).

Consumes Binance ``<symbol>@aggTrade`` websocket events into an in-memory
ring buffer, aggregates them into fixed-width bars (default 1 minute), and
periodically appends the bars to a per-symbol parquet store under
``data/trades_stream/{SYMBOL}_1m.parquet``. Crash recovery: on restart the
ingester reads the last persisted bar timestamp and resumes from there —
anything newer in the buffer/file is merged with dedupe on ``timestamp``.

Design:
  - ``RingBuffer``            bounded deque; drops oldest on overflow
                              (bounded memory under bursty tick flow).
  - ``aggregate_bars``        pure: trade dicts → OHLCV bars.
  - ``StreamIngester``        state machine: on_trade → maybe flush →
                              parquet append (merge-dedupe); knows its last
                              persisted timestamp for resume.
  - ``run_ws``                async websocket loop with reconnect
                              (exponential backoff), protocol pings and
                              per-trade receipt timestamps; ``connector``
                              is injectable so tests exercise the loop
                              without network.

References:
  - Binance USDⓈ-M websocket — "Aggregate Trade Streams"
    (<symbol>@aggTrade; fields T=trade time ms, p=price, q=qty,
    m=is-buyer-maker).
  - Ring-buffer ingestion: LMAX Disruptor pattern (bounded queue between
    producer/consumer, drop/overwrite policy under overload).
  - Recovery by high-water mark: persist-then-resume from the last durable
    offset, cf. Kafka consumer checkpointing (Kleppmann, DDIA ch. 11).
"""
from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from _shared.data.fetch_common import merge_dedupe

PathLike = str | Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "trades_stream"

BAR_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "trades"]
STREAM_URL = "wss://fstream.binance.com/ws/{symbol}@aggTrade"


@dataclass(frozen=True)
class StreamConfig:
    """Tunables for one symbol's stream ingestion."""

    symbol: str
    out_dir: str = str(DEFAULT_OUT_DIR)
    bar_ms: int = 60_000
    flush_interval_ms: int = 60_000
    buffer_size: int = 100_000
    proxy: str | None = "http://127.0.0.1:7890"


class RingBuffer:
    """Bounded FIFO of raw trades; oldest entries dropped on overflow."""

    def __init__(self, maxlen: int = 100_000):
        self._buf: deque[Mapping[str, object]] = deque(maxlen=maxlen)
        self.dropped = 0

    def append(self, trade: Mapping[str, object]) -> None:
        if len(self._buf) == self._buf.maxlen:
            self.dropped += 1
        self._buf.append(trade)

    def drain(self) -> tuple[Mapping[str, object], ...]:
        """Pop and return everything currently buffered (FIFO order)."""
        items = tuple(self._buf)
        self._buf.clear()
        return items

    def __len__(self) -> int:
        return len(self._buf)


def parse_aggtrade(msg: str | bytes | Mapping[str, object]) -> Mapping[str, object] | None:
    """Pure: one aggTrade WS payload → normalised trade dict, None if bad.

    Accepts either the raw JSON text or an already-parsed message. Handles
    both bare payloads and combined-stream envelopes (``{"data": {...}}``).
    """
    try:
        data = json.loads(msg) if isinstance(msg, (str, bytes)) else dict(msg)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if "data" in data and isinstance(data["data"], Mapping):
        data = data["data"]
    try:
        return {
            "ts": int(data["T"]),
            "price": float(data["p"]),
            "qty": float(data["q"]),
            "is_buyer_maker": bool(data["m"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def aggregate_bars(trades: Iterable[Mapping[str, object]], bar_ms: int = 60_000) -> pd.DataFrame:
    """Pure: trade dicts (``ts`` ms, ``price``, ``qty``) → OHLCV bars.

    Bars are bucketed on ``ts // bar_ms * bar_ms``; output sorted by
    timestamp. Only *closed* semantics are applied by the caller — this
    function aggregates whatever it is given.
    """
    df = pd.DataFrame(list(trades))
    if len(df) == 0:
        return pd.DataFrame(columns=BAR_COLUMNS)
    df["timestamp"] = (df["ts"].astype("int64") // bar_ms) * bar_ms
    bars = (
        df.groupby("timestamp")
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("qty", "sum"),
            trades=("price", "size"),
        )
        .reset_index()
    )
    # groupby preserves original row order within each bucket, so
    # first/last are the true open/close of the bar
    return bars.sort_values("timestamp").reset_index(drop=True)[BAR_COLUMNS]


class StreamIngester:
    """Buffers raw trades, aggregates closed bars, appends them to parquet."""

    def __init__(self, config: StreamConfig):
        self.config = config
        self.buffer = RingBuffer(config.buffer_size)
        self.out_path = Path(config.out_dir) / f"{config.symbol}_1m.parquet"
        self._last_flush_ms: int | None = None
        # wall-clock ms of the most recently received trade (receipt time,
        # as opposed to the exchange-side ``ts``); None until the first trade.
        self.last_receipt_ms: int | None = None

    # -- recovery ------------------------------------------------------------
    def last_persisted_ts(self) -> int | None:
        """High-water mark: last bar timestamp already durable on disk."""
        if not self.out_path.exists():
            return None
        df = pd.read_parquet(self.out_path, columns=["timestamp"])
        return int(df["timestamp"].max()) if len(df) else None

    # -- ingest --------------------------------------------------------------
    def on_trade(
        self, trade: Mapping[str, object], now_ms: int | None = None
    ) -> pd.DataFrame | None:
        """Buffer one trade; flush if the flush interval has elapsed.

        Returns the flushed bars frame when a flush happened, else None.
        ``now_ms`` defaults to the trade's own timestamp.
        """
        self.buffer.append(trade)
        now_ms = int(trade["ts"]) if now_ms is None else now_ms
        if self._last_flush_ms is None:
            self._last_flush_ms = now_ms
            return None
        if now_ms - self._last_flush_ms >= self.config.flush_interval_ms:
            return self.flush(now_ms=now_ms)
        return None

    # -- flush ---------------------------------------------------------------
    def flush(self, now_ms: int | None = None) -> pd.DataFrame:
        """Aggregate buffered trades into *closed* bars and append to parquet.

        The bar containing ``now_ms`` is still open and is kept out of the
        flush (its trades are re-buffered), so bars are only written once
        they can no longer change. Returns the bars that were written
        (empty frame when nothing was ready).
        """
        trades = self.buffer.drain()
        self._last_flush_ms = now_ms if now_ms is not None else self._last_flush_ms
        if not trades:
            return pd.DataFrame(columns=BAR_COLUMNS)
        bars = aggregate_bars(trades, bar_ms=self.config.bar_ms)
        if now_ms is not None and len(bars):
            open_bar_start = (int(now_ms) // self.config.bar_ms) * self.config.bar_ms
            closed = bars[bars["timestamp"] < open_bar_start]
            # re-buffer trades belonging to the still-open bar
            leftover = [
                t
                for t in trades
                if (int(t["ts"]) // self.config.bar_ms) * self.config.bar_ms >= open_bar_start
            ]
            for t in leftover:
                self.buffer.append(t)
            bars = closed
        if len(bars) == 0:
            return bars
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            pd.read_parquet(self.out_path) if self.out_path.exists()
            else pd.DataFrame(columns=BAR_COLUMNS)
        )
        merged = merge_dedupe(existing, bars, subset=["timestamp"], sort_col="timestamp")
        merged.to_parquet(self.out_path, index=False)
        return bars


def backoff_delays(
    base_sec: float = 1.0, factor: float = 2.0, max_sec: float = 60.0
) -> Iterable[float]:
    """Pure: endless exponential-backoff delays, capped at ``max_sec``.

    1s, 2s, 4s, ... 60s, 60s, ... — the standard reconnect cadence for
    market-data sockets (Binance docs recommend backing off rather than
    hammering the endpoint after a drop).
    """
    delay = float(base_sec)
    while True:
        yield min(delay, max_sec)
        delay *= factor


async def run_ws(
    config: StreamConfig,
    duration_sec: float = 0.0,
    *,
    ping_interval_sec: float | None = 20.0,
    ping_timeout_sec: float | None = 20.0,
    backoff_base_sec: float = 1.0,
    max_reconnects: int | None = None,
    connector=None,
) -> int:
    """Production loop: consume the aggTrade stream into a StreamIngester.

    Reconnects with exponential backoff (:func:`backoff_delays`) whenever
    the connection drops, and keeps the socket alive with protocol-level
    pings (``ping_interval_sec`` / ``ping_timeout_sec``) so half-open
    connections are detected instead of silently starving the feed. Each
    received trade is stamped with ``recv_ts`` (local wall-clock ms) and
    the ingester's ``last_receipt_ms`` is updated — receipt vs exchange
    ``ts`` drift is the first latency signal in a degraded feed.

    ``duration_sec <= 0`` runs forever. ``max_reconnects`` bounds the
    reconnect loop (None = unlimited, the daemon default). ``connector``
    injects a websocket factory for tests (same call shape as
    ``websockets.connect``); production leaves it None. Returns the number
    of flushed bars.
    """
    import asyncio
    import time

    ingester = StreamIngester(config)
    url = STREAM_URL.format(symbol=config.symbol.lower())
    if connector is None:
        import websockets

        def connector(u):  # noqa: E731 — closure over ping tunables
            return websockets.connect(
                u,
                proxy=config.proxy,
                ping_interval=ping_interval_sec,
                ping_timeout=ping_timeout_sec,
                close_timeout=5,
            )

    flushed = 0
    reconnects = 0
    t0 = time.monotonic()
    backoff = backoff_delays(base_sec=backoff_base_sec)

    def time_up() -> bool:
        return duration_sec > 0 and time.monotonic() - t0 >= duration_sec

    while not time_up():
        try:
            async with connector(url) as ws:
                backoff = backoff_delays(base_sec=backoff_base_sec)  # reset on connect
                while not time_up():
                    raw = await ws.recv()
                    trade = parse_aggtrade(raw)
                    if trade is None:
                        continue
                    recv_ts = int(time.time() * 1000)
                    ingester.last_receipt_ms = recv_ts
                    out = ingester.on_trade(dict(trade, recv_ts=recv_ts))
                    if out is not None:
                        flushed += len(out)
        except Exception:
            # any failure of the connection/recv path -> backoff + reconnect;
            # a persistent failure surfaces via max_reconnects if bounded.
            if time_up():
                break
            reconnects += 1
            if max_reconnects is not None and reconnects > max_reconnects:
                raise
            await asyncio.sleep(next(backoff))
            continue
        break  # clean exit: duration elapsed
    # final drain on clean exit
    flushed += len(ingester.flush())
    return flushed
