#!/usr/bin/env python3
"""Backfill Binance USDⓈ-M aggTrades for the symbols/timeframes we need.

Usage:
    python backfill_aggtrades.py --symbol BTCUSDT --start 2024-01-01 --end 2024-12-31 --out ~/multica/quant-loop/data/trades/
    python backfill_aggtrades.py --symbol ETHUSDT --months-back 12
    python backfill_aggtrades.py --symbol SOLUSDT --months-back 6

Binance API: GET https://fapi.binance.com/fapi/v1/aggTrades
- params: symbol, startTime, endTime (max 1h span per call), limit 1000
- No auth required for public aggTrades
- Rate limit: 1200 weight/min, this endpoint is weight 20

Output: parquet per symbol per day, partitioned:
    data/trades/symbol=BTCUSDT/date=2024-01-01/aggtrades.parquet
"""
import argparse
import time
import datetime as dt
from pathlib import Path
import urllib.request
import urllib.error
import json
import sys

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
AGGTRADES_ENDPOINT = "/fapi/v1/aggTrades"
RATE_LIMIT_WEIGHT_PER_MIN = 1200
WEIGHT_PER_CALL = 20
MAX_CALLS_PER_MIN = RATE_LIMIT_WEIGHT_PER_MIN // WEIGHT_PER_CALL  # 60
WINDOW_MS = 60 * 60 * 1000  # 1h per call max


def fetch_aggtrades(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    url = f"{BINANCE_FUTURES_BASE}{AGGTRADES_ENDPOINT}"
    params = {
        "symbol": symbol,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000,
    }
    full = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    try:
        with urllib.request.urlopen(full, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429 or e.code == 418:
            print(f"[rate-limit] {e.code}, sleeping 60s", file=sys.stderr)
            time.sleep(60)
            return fetch_aggtrades(symbol, start_ms, end_ms)
        raise
    except urllib.error.URLError as e:
        print(f"[network] {e}, retrying in 5s", file=sys.stderr)
        time.sleep(5)
        return fetch_aggtrades(symbol, start_ms, end_ms)


def backfill(symbol: str, start: dt.datetime, end: dt.datetime, out_root: Path):
    """Pull aggTrades for [start, end) in 1h windows. Skip windows already on disk."""
    out_root.mkdir(parents=True, exist_ok=True)
    current = start
    total_calls = 0
    while current < end:
        # Skip if partition exists
        date_str = current.strftime("%Y-%m-%d")
        part = out_root / f"symbol={symbol}" / f"date={date_str}" / "aggtrades.parquet"
        if part.exists():
            current += dt.timedelta(days=1)
            continue

        # Pull one full day (24 1h windows)
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + dt.timedelta(days=1)
        day_rows = []
        window_start = day_start
        while window_start < day_end and window_start < end:
            window_end = min(window_start + dt.timedelta(hours=1), day_end, end)
            rows = fetch_aggtrades(
                symbol,
                int(window_start.timestamp() * 1000),
                int(window_end.timestamp() * 1000),
            )
            day_rows.extend(rows)
            total_calls += 1
            if total_calls % MAX_CALLS_PER_MIN == 0:
                time.sleep(60)  # respect rate limit
            else:
                time.sleep(1)  # gentle
            window_start = window_end

        if not day_rows:
            current += dt.timedelta(days=1)
            continue

        # Write parquet
        try:
            import pandas as pd
        except ImportError:
            print("pandas required for parquet output", file=sys.stderr)
            sys.exit(1)

        df = pd.DataFrame(day_rows)
        df["T"] = pd.to_datetime(df["T"], unit="ms")
        df = df.rename(columns={
            "a": "agg_id", "T": "timestamp", "p": "price",
            "q": "qty", "f": "first_id", "l": "last_id",
            "m": "is_buyer_maker",
        })
        df["price"] = df["price"].astype(float)
        df["qty"] = df["qty"].astype(float)

        part.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(part, index=False)
        print(f"[ok] {symbol} {date_str}: {len(df)} trades → {part}", file=sys.stderr)
        current += dt.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD")
    ap.add_argument("--months-back", type=int, help="alt to start/end: N months back from today")
    ap.add_argument("--out", default=str(Path("~/multica/quant-loop/data/trades/").expanduser()))
    args = ap.parse_args()

    if args.months_back:
        end = dt.datetime.utcnow()
        start = end - dt.timedelta(days=30 * args.months_back)
    else:
        start = dt.datetime.fromisoformat(args.start)
        end = dt.datetime.fromisoformat(args.end)

    backfill(args.symbol, start, end, Path(args.out).expanduser())


if __name__ == "__main__":
    main()
