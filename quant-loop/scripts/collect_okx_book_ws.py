#!/usr/bin/env python3
"""OKX books5 WebSocket collector — raw 5-level book snapshots to JSONL.

Replaces the REST-polling design that only persisted derived imbalance
metrics and threw away the raw 50-level book. This worker:

  - subscribes to the OKX public WS ``books5`` channel (5-level depth,
    snapshot + incremental updates) for BTC/ETH/SOL USDT swaps;
  - persists **raw** bid/ask levels (price + size per level) as JSONL —
    one snapshot per line — so downstream research (order-book factor
    family, ``_shared/factor_analysis/orderbook_factors.py``) can rebuild
    any derived metric offline instead of being stuck with imb_5/10/50;
  - stamps every flushed batch with the dual-timestamp scheme of
    ``_shared/data/ingest_ts.py`` (exchange ``ts`` + local ``ingest_ts``),
    making feed stalls / clock skew measurable end-to-end;
  - rotates output files daily per symbol under ``data/okx_book/``.

It is a *worker*: on any connection failure it exits non-zero and lets the
supervisor wrapper (``scripts/run_okx_book_collector.py``) restart it with
backoff. Run standalone for a one-shot session:

    python3 scripts/collect_okx_book_ws.py [--proxy http://127.0.0.1:7890]

JSONL schema (one line per book update):
    ts         int   exchange event time, ms (OKX ``data[].ts``)
    ts_ns      int   exchange event time, ns (ts * 1e6)
    ingest_ts  int   local persist time, ms (added by stamp_ingest_ts)
    symbol     str   e.g. "BTC-USDT-SWAP"
    bids       [[price, qty], ...]  up to 5 levels, best first
    asks       [[price, qty], ...]  up to 5 levels, best first
    bid_p1..bid_p5, bid_q1..bid_q5, ask_p1..ask_p5, ask_q1..ask_q5  float
    checksum   int   OKX checksum (-1 when absent)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _shared.data.ingest_ts import stamp_ingest_ts  # noqa: E402

OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
SYMBOLS: tuple[str, ...] = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
N_LEVELS = 5
PING_INTERVAL_SEC = 25.0  # OKX drops idle connections after ~30s
RECV_TIMEOUT_SEC = 60.0  # no message for this long -> wedged feed, bail out

DEFAULT_DATA_DIR = _ROOT / "data" / "okx_book"

#: Flat per-level columns, matching the orderbook_factors input schema.
LEVEL_COLUMNS: tuple[str, ...] = tuple(
    f"{side}_{kind}{i}"
    for i in range(1, N_LEVELS + 1)
    for side in ("bid", "ask")
    for kind in ("p", "q")
)


def normalize_snapshot(data: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Convert one OKX ``books5`` data entry to a raw JSONL row (unstamped).

    ``data`` has ``bids``/``asks`` as ``[price, size, liq, n_orders]``
    string arrays, plus ``ts`` (ms) and ``checksum``. Missing levels are
    zero-padded so every row carries the full 5-level schema.
    """
    bids_raw = data.get("bids")
    asks_raw = data.get("asks")
    ts_raw = data.get("ts")
    if not bids_raw or not asks_raw or ts_raw is None:
        raise ValueError(f"malformed books5 entry for {symbol}: missing bids/asks/ts")

    ts_ms = int(ts_raw)
    bids = [[float(lv[0]), float(lv[1])] for lv in bids_raw[:N_LEVELS]]
    asks = [[float(lv[0]), float(lv[1])] for lv in asks_raw[:N_LEVELS]]

    row: dict[str, Any] = {
        "ts": ts_ms,
        "ts_ns": ts_ms * 1_000_000,
        "symbol": symbol,
        "bids": bids,
        "asks": asks,
        "checksum": int(data.get("checksum", -1)),
    }
    for i in range(N_LEVELS):
        bid = bids[i] if i < len(bids) else (0.0, 0.0)
        ask = asks[i] if i < len(asks) else (0.0, 0.0)
        row[f"bid_p{i + 1}"] = bid[0]
        row[f"bid_q{i + 1}"] = bid[1]
        row[f"ask_p{i + 1}"] = ask[0]
        row[f"ask_q{i + 1}"] = ask[1]
    return row


def parse_message(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse one WS text frame into ``(symbol, row)`` pairs.

    Non-data frames (subscribe confirmations, ``pong``) yield nothing.
    Raises ValueError on a data frame that fails schema validation — a
    protocol change is exactly what we want to be loud about.
    """
    if raw == "pong":
        return []
    msg = json.loads(raw)
    if not isinstance(msg, dict) or "data" not in msg:
        return []  # subscribe ack / event frame
    symbol = str(msg.get("arg", {}).get("instId", ""))
    return [(symbol, normalize_snapshot(entry, symbol)) for entry in msg["data"]]


def _day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).strftime("%Y-%m-%d")


class BookJsonlWriter:
    """Buffered JSONL writer: per-symbol daily files + dual timestamps.

    Rows are buffered per symbol and flushed in batches; each batch is
    stamped by :func:`stamp_ingest_ts` so ``ingest_ts`` records the local
    wall-clock moment the batch was durably written (flush time), per the
    ``_shared/data/ingest_ts.py`` contract. File rotation is driven by the
    *exchange* date of each row, so late/arrears data still lands in the
    correct daily file.
    """

    def __init__(self, data_dir: Path | str, symbols: tuple[str, ...] = SYMBOLS):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.symbols = symbols
        self._buffers: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
        self.n_written = 0

    @staticmethod
    def file_path(data_dir: Path, symbol: str, ts_ms: int) -> Path:
        short = symbol.split("-")[0]
        return data_dir / f"{short}_{_day_of(ts_ms)}.jsonl"

    def add(self, symbol: str, row: dict[str, Any]) -> None:
        self._buffers.setdefault(symbol, []).append(row)

    def flush(self, now_ms: int | None = None) -> int:
        """Stamp and append all buffered rows; returns rows written."""
        written = 0
        for symbol, buf in self._buffers.items():
            if not buf:
                continue
            df = pd.DataFrame(buf)
            # Dual timestamp: exchange ts (event) + ingest_ts (persist).
            df = stamp_ingest_ts(df, ts_col="ts", now_ms=now_ms)
            for day, day_df in df.groupby(df["ts"].map(_day_of)):
                path = self.file_path(self.data_dir, symbol, day_df["ts"].iloc[0])
                with path.open("a") as fh:
                    for record in day_df.to_dict(orient="records"):
                        fh.write(json.dumps(record) + "\n")
                written += len(day_df)
            buf.clear()
        self.n_written += written
        return written

    def pending(self) -> int:
        return sum(len(b) for b in self._buffers.values())


async def collect_loop(data_dir: Path, proxy: str | None,
                       flush_interval: float = 5.0) -> int:
    """One WS session: subscribe books5 and write until the connection dies.

    Raises (non-zero exit for the supervisor) on any transport failure or
    when no message arrives for ``RECV_TIMEOUT_SEC`` — a silent stall must
    be a restart, not a zombie.
    """
    from websockets.asyncio.client import connect

    writer = BookJsonlWriter(data_dir)
    sub = {"op": "subscribe",
           "args": [{"channel": "books5", "instId": s} for s in SYMBOLS]}

    async with connect(OKX_WS_URL, proxy=proxy) as ws:
        await ws.send(json.dumps(sub))
        print(f"[{_utcnow()}] subscribed books5: {SYMBOLS}", flush=True)
        last_flush = time.monotonic()
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT_SEC)
            except TimeoutError as exc:
                raise ConnectionError(
                    f"no WS message for {RECV_TIMEOUT_SEC}s — wedged feed") from exc
            for symbol, row in parse_message(raw):
                writer.add(symbol, row)
            now = time.monotonic()
            if now - last_flush >= flush_interval:
                writer.flush()
                last_flush = now
                if writer.n_written % 2000 < 50:
                    print(f"[{_utcnow()}] {writer.n_written} snapshots persisted",
                          flush=True)
            # OKX keepalive: text "ping" -> "pong" (protocol ping also works,
            # but the text ping is the documented v5 API behaviour).
            if now % PING_INTERVAL_SEC < flush_interval:
                await ws.send("ping")


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--proxy",
                    default=os.environ.get("OKX_PROXY", "http://127.0.0.1:7890"),
                    help="'none' for a direct connection")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--flush-interval", type=float, default=5.0)
    args = ap.parse_args()

    proxy = None if args.proxy.lower() == "none" else args.proxy
    try:
        asyncio.run(collect_loop(Path(args.data_dir), proxy, args.flush_interval))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # Non-zero exit: the supervisor wrapper restarts us with backoff.
        print(f"[{_utcnow()}] collector died: {exc!r}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
