#!/usr/bin/env python3
"""Binance depth10 WebSocket collector — raw 10-level book snapshots to JSONL.

Subscribes to Binance futures depth10 channel for BTC/ETH/SOL USDT perps.
Stores raw bid/ask levels (price + size per level) as JSONL — one snapshot
per line — so downstream order-book factor research can rebuild any derived
metric offline.

Same design as collect_okx_book_ws.py: worker that exits on failure and
lets a supervisor wrapper restart it.

Usage:
    python3 scripts/collect_binance_book_ws.py [--proxy http://127.0.0.1:7890]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _shared.data.ingest_ts import stamp_ingest_ts  # noqa: E402

DATA_DIR = ROOT / "data" / "binance_book"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
WS_BASE = "wss://fstream.binance.com/ws"


def _day_of(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def normalize_depth_update(data: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Normalize a Binance depthUpdate message to our JSONL schema."""
    ts_ms = data.get("T", data.get("E", 0))  # T=transaction time, E=event time
    bids = data.get("b", [])  # [[price, qty], ...]
    asks = data.get("a", [])

    row: dict[str, Any] = {
        "ts": ts_ms,
        "ts_ns": ts_ms * 1_000_000,
        "symbol": symbol,
        "bids": bids[:10],
        "asks": asks[:10],
        "checksum": data.get("u", -1),  # update ID
    }

    # Flatten to bid_p1..bid_p10 etc.
    for i, (price, qty) in enumerate(bids[:10], 1):
        row[f"bid_p{i}"] = float(price)
        row[f"bid_q{i}"] = float(qty)
    for i, (price, qty) in enumerate(asks[:10], 1):
        row[f"ask_p{i}"] = float(price)
        row[f"ask_q{i}"] = float(qty)

    return row


async def collect_symbol(symbol: str) -> None:
    """Collect depth10 updates for one symbol."""
    from websockets.asyncio.client import connect

    stream_name = f"{symbol.lower()}@depth10@100ms"
    url = f"{WS_BASE}/{stream_name}"

    async with connect(url, ping_interval=20, ping_timeout=10) as ws:
        print(f"[{datetime.now(UTC).isoformat()}] {symbol} connected", flush=True)

        current_file = None
        current_day = ""

        async for raw in ws:
            try:
                data = json.loads(raw)
                row = normalize_depth_update(data, symbol)

                # Daily rotation
                day = _day_of(row["ts"])
                if day != current_day:
                    if current_file:
                        current_file.close()
                    path = DATA_DIR / f"{symbol}_{day}.jsonl"
                    current_file = open(path, "a")
                    current_day = day
                    print(f"[{datetime.now(UTC).isoformat()}] {symbol} writing to {path}", flush=True)

                # Stamp dual timestamp
                stamped = stamp_ingest_ts(pd.DataFrame([row]))
                row["ingest_ts"] = int(stamped["ingest_ts"].iloc[0])

                current_file.write(json.dumps(row) + "\n")

            except json.JSONDecodeError:
                continue
            except Exception as e:
                print(f"[{symbol}] Error: {e}", flush=True)
                raise


async def main():
    print(f"[{datetime.now(UTC).isoformat()}] Binance depth10 collector start", flush=True)
    print(f"  Symbols: {SYMBOLS}", flush=True)

    tasks = [collect_symbol(sym) for sym in SYMBOLS]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped", flush=True)
