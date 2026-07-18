"""Fetch Binance USDT-M perp funding rates (8h).

Adapted from fetch_bybit_funding.py. Uses fapi.binance.com fundingRate endpoint.
Output: ~/multica/quant-loop/data/funding/{SYMBOL}.parquet
"""
from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests

BASE_URL = "https://fapi.binance.com"
PATH = "/fapi/v1/fundingRate"
PAGE_LIMIT = 1000
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0
DEFAULT_START = "2022-01-01"
DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/data/funding"

def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

def fetch_symbol(session, symbol, start_ms, end_ms):
    all_rows = []
    cursor_ms = start_ms
    while cursor_ms < end_ms:
        params = {"symbol": symbol, "limit": PAGE_LIMIT, "startTime": cursor_ms}
        last_err = None
        rows = None
        for attempt in range(MAX_RETRIES):
            try:
                r = session.get(BASE_URL + PATH, params=params, timeout=15)
                if r.status_code == 200:
                    rows = r.json()
                    break
                if r.status_code in (418, 429):
                    time.sleep(BACKOFF_BASE_S * (2 ** attempt))
                    continue
                r.raise_for_status()
            except Exception as e:
                last_err = e
                time.sleep(BACKOFF_BASE_S * (2 ** attempt))
        if rows is None:
            raise RuntimeError(f"funding fetch failed for {symbol}: {last_err}")
        if not rows:
            break
        all_rows.extend(rows)
        next_cursor = rows[-1]["fundingTime"] + 1
        if next_cursor <= cursor_ms:
            break
        cursor_ms = next_cursor
        time.sleep(0.05)
    if not all_rows:
        raise RuntimeError(f"no funding returned for {symbol}")
    df = pd.DataFrame(all_rows)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    return df[["ts","symbol","fundingRate","markPrice"]]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BNBUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default="2026-07-11")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    args = ap.parse_args()
    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp() * 1000)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"endpoint": "fapi.binance.com", "symbols": {}}
    with requests.Session() as sess:
        for s in symbols:
            print(f"[fund] {s}", file=sys.stderr)
            try:
                df = fetch_symbol(sess, s, start_ms, end_ms)
                out_path = out_dir / f"{s}.parquet"
                df.to_parquet(out_path, engine="pyarrow", index=False)
                print(f"[fund] {s}: {len(df)} rows -> {out_path}", file=sys.stderr)
                report["symbols"][s] = {"rows": len(df), "first": _iso(int(df["fundingTime"].iloc[0])), "last": _iso(int(df["fundingTime"].iloc[-1]))}
            except Exception as e:
                print(f"[fund] {s} FAILED: {e}", file=sys.stderr)
                report["symbols"][s] = {"error": str(e)}
    (out_dir / "fetch_report_funding.json").write_text(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
