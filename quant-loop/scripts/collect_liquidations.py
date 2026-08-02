#!/usr/bin/env python3
"""Collect Binance USDT-M futures force-order (liquidation) events.

Subscribes to btcusdt/ethusdt/solusdt @forceOrder on the combined stream and
appends one JSON object per line to data/liquidations/{SYMBOL}.jsonl:
    {"ts": <event ms>, "symbol": "BTCUSDT", "side": "SELL", "price": "...", "qty": "..."}

Requires a local HTTP proxy (default http://127.0.0.1:7890) because
fstream.binance.com is region-blocked on our networks (direct TCP connect is
refused / DNS-poisoned). Override with --proxy or LIQ_PROXY env; "none" to
connect directly.

Usage:
    python3 collect_liquidations.py [--proxy http://127.0.0.1:7890] [--duration 0]
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import websockets

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "liquidations"
STREAMS = "/".join(f"{s.lower()}@forceOrder" for s in SYMBOLS)
URL = f"wss://fstream.binance.com/market/stream?streams={STREAMS}"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def append_event(symbol: str, event: dict) -> None:
    path = DATA_DIR / f"{symbol}.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


async def collect(proxy: str | None, duration: float) -> None:
    t0 = time.time()
    n = 0
    async with websockets.connect(
        URL, proxy=proxy, ping_interval=None, close_timeout=5
    ) as ws:
        log(f"CONNECTED {URL} proxy={proxy or 'none'}")
        while duration <= 0 or time.time() - t0 < duration:
            raw = await ws.recv()
            msg = json.loads(raw)
            data = msg.get("data", {})
            o = data.get("o", {})
            if not o:
                continue
            event = {
                "ts": o.get("T", data.get("E")),
                "symbol": o.get("s"),
                "side": o.get("S"),
                "price": o.get("p"),
                "qty": o.get("q"),
            }
            append_event(event["symbol"], event)
            n += 1
            log(f"LIQ #{n} raw={raw}")
        log(f"DONE after {time.time() - t0:.0f}s, liquidation events written: {n}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default=os.environ.get("LIQ_PROXY", "http://127.0.0.1:7890"))
    ap.add_argument("--duration", type=float, default=0, help="seconds; 0 = run forever")
    args = ap.parse_args()
    proxy = None if args.proxy.lower() == "none" else args.proxy
    while True:
        try:
            asyncio.run(collect(proxy, args.duration))
            break  # clean finish (duration reached)
        except KeyboardInterrupt:
            log("stopped by user")
            break
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {e} — reconnecting in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
