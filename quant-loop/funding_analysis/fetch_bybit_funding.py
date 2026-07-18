"""Fetch Bybit linear 8h funding rates for BTC/ETH/SOL, 2022-01-01 -> 2026-07-11.

Endpoint: GET https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=200&endTime=<ms>

Bybit's funding/history returns DESC order on fundingRateTimestamp when paginated
with endTime. The startTime parameter alone errors with "Time Is Invalid" so we
page backwards from the requested end. Page size 200 (max).
"""
from __future__ import annotations

import sys
import time
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

import requests
import pandas as pd

BASE_URL = "https://api.bybit.com"
PATH = "/v5/market/funding/history"
PAGE_LIMIT = 200  # Bybit max
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def fetch_one_page(session: requests.Session, symbol: str, end_ms: int) -> list[dict]:
    """Page through Bybit funding history DESC order using endTime."""
    params = {
        "category": "linear",
        "symbol": symbol,
        "limit": PAGE_LIMIT,
        "endTime": end_ms,
    }
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(BASE_URL + PATH, params=params, timeout=15)
            if r.status_code == 200:
                payload = r.json()
                if payload.get("retCode") != 0:
                    raise RuntimeError(f"bybit api error retCode={payload.get('retCode')} {payload.get('retMsg')}")
                return payload.get("result", {}).get("list", []) or []
            if r.status_code in (418, 429):
                wait = BACKOFF_BASE_S * (4 ** attempt)
                print(f"  rate limit hit, sleeping {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            last_err = e
            wait = BACKOFF_BASE_S * (2 ** attempt)
            print(f"  request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}; "
                  f"sleeping {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"fetch failed after {MAX_RETRIES} retries: {last_err}")


def fetch_symbol(session: requests.Session, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Walk DESC from end_ms, accept rows with fundingTime >= start_ms."""
    all_rows: list[dict] = []
    cursor_end = end_ms
    page = 0
    while True:
        page += 1
        rows = fetch_one_page(session, symbol, cursor_end)
        if not rows:
            break
        earliest_in_page = None
        for r in rows:
            ft = int(r["fundingRateTimestamp"])
            if ft < start_ms or ft >= end_ms:
                continue
            all_rows.append({
                "symbol": symbol,
                "fundingTime": ft,
                "fundingRate": float(r["fundingRate"]),
            })
            if earliest_in_page is None or ft < earliest_in_page:
                earliest_in_page = ft
        # Bybit DESC order. The smallest ft in this page is the last element.
        sorted_rows = sorted(rows, key=lambda x: int(x["fundingRateTimestamp"]))
        last_ft = int(sorted_rows[0]["fundingRateTimestamp"])
        # Stop when page covers start_ms or page < limit.
        if last_ft <= start_ms or len(rows) < PAGE_LIMIT:
            break
        # Move endTime cursor to last_ft - 1 (continue backward).
        cursor_end = last_ft - 1
        if page % 20 == 0:
            print(f"    page {page}: cursor_end={_iso(cursor_end)} rows={len(all_rows)}",
                  file=sys.stderr)
        time.sleep(0.06)
    if not all_rows:
        raise RuntimeError(f"no funding rows returned for {symbol}")
    df = pd.DataFrame(all_rows).sort_values("fundingTime").reset_index(drop=True)
    df["fundingTime"] = df["fundingTime"].astype("int64")
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-07-11")
    ap.add_argument("--out-dir", default=str(Path(__file__).parent))
    args = ap.parse_args()

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp() * 1000)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {"start_iso": args.start, "end_iso": args.end, "symbols": {}}
    overall_ok = True
    with requests.Session() as session:
        session.headers.update({"User-Agent": "funding-rate-analyst/fetch_bybit"})
        for symbol in symbols:
            print(f"[fetch] bybit {symbol} {args.start} -> {args.end}", file=sys.stderr)
            t0 = time.time()
            try:
                df = fetch_symbol(session, symbol, start_ms, end_ms)
            except Exception as e:
                print(f"[fetch] {symbol}: FAILED {e}", file=sys.stderr)
                report["symbols"][symbol] = {"error": str(e)}
                overall_ok = False
                continue
            elapsed = time.time() - t0
            out_path = out_dir / f"{symbol}_bybit_funding.parquet"
            df.to_parquet(out_path, engine="pyarrow", index=False)
            n = len(df)
            expected = (end_ms - start_ms) / (8 * 3600 * 1000)
            coverage = n / expected
            print(f"[fetch] {symbol}: {n} rows, coverage={coverage:.2%}, "
                  f"elapsed={elapsed:.1f}s -> {out_path}", file=sys.stderr)
            report["symbols"][symbol] = {
                "rows": n,
                "first_fundingTime_ms": int(df["fundingTime"].iloc[0]),
                "last_fundingTime_ms": int(df["fundingTime"].iloc[-1]),
                "first_iso": _iso(int(df["fundingTime"].iloc[0])),
                "last_iso": _iso(int(df["fundingTime"].iloc[-1])),
                "coverage": round(coverage, 4),
                "parquet_path": str(out_path),
                "elapsed_s": round(elapsed, 2),
            }
            if coverage < 0.95:
                overall_ok = False

    out_json = out_dir / "fetch_report_bybit.json"
    out_json.write_text(json.dumps(report, indent=2))
    print(f"[fetch] report: {out_json}", file=sys.stderr)
    print(f"[fetch] overall_ok={overall_ok}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
