"""Fetch Binance USDT-M perpetual 4h klines.

Used to fix SMA-34866 (replace ETHUSDT/SOLUSDT 4h symlinks that pointed at
BTCUSD_4h) and to refresh BTCUSDT_4h.

Mirrors `fetch_binance_usdm_1m.py` and `fetch_binance_coinm_4h.py`:

  - Endpoint: fapi.binance.com (USDT-M futures)
  - Interval: 4h
  - Output:    <out-dir>/{SYMBOL}_4h.parquet
  - Schema:    open_time, open, high, low, close, volume, close_time,
               quote_volume, trades, taker_buy_base, taker_buy_quote,
               ignore — byte-compatible with the BTCUSD_4h.parquet
               emitted by fetch_binance_coinm_4h.py.

Validation (matches the 12-column v2 contract used by the catalog):
  - bars fetched per symbol >= 5400 (4.5y * 6 / day)
  - no 4h gap > 1 bar between consecutive open_time values
  - open_time within 5min of expected 4h boundary

Default window is 2022-01-01 .. "now" UTC — that matches the
BTCUSD_4h.parquet coverage so callers see one continuous series.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://fapi.binance.com"
KLINE_PATH = "/fapi/v1/klines"
INTERVAL = "4h"
PAGE_LIMIT = 1000
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0
REQUEST_TIMEOUT_S = 15
EXPECTED_BAR_MS = 4 * 60 * 60 * 1000
GAP_TOLERANCE_MS = 5 * 60 * 1000
DEFAULT_START = "2022-01-01"


@dataclass
class FetchReport:
    symbol: str
    rows: int
    first_open_time_ms: int
    last_open_time_ms: int
    max_gap_bars: int
    boundary_misalign_count: int
    parquet_path: str
    elapsed_s: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "rows": self.rows,
            "first_open_time_ms": self.first_open_time_ms,
            "last_open_time_ms": self.last_open_time_ms,
            "first_open_time_iso": _iso(self.first_open_time_ms),
            "last_open_time_iso": _iso(self.last_open_time_ms),
            "max_gap_bars": self.max_gap_bars,
            "boundary_misalign_count": self.boundary_misalign_count,
            "parquet_path": self.parquet_path,
            "elapsed_s": round(self.elapsed_s, 2),
        }


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _fetch_one_page(session: requests.Session, symbol: str, start_ms: int | None) -> list[list]:
    params: dict = {"symbol": symbol, "interval": INTERVAL, "limit": PAGE_LIMIT}
    if start_ms is not None:
        params["startTime"] = start_ms
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(BASE_URL + KLINE_PATH, params=params, timeout=REQUEST_TIMEOUT_S)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (418, 429):
                wait = BACKOFF_BASE_S * (2 ** attempt)
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


def fetch_symbol(session: requests.Session, symbol: str,
                 start_ms: int, end_ms: int) -> pd.DataFrame:
    all_rows: list[list] = []
    cursor_ms: int | None = start_ms
    while True:
        rows = _fetch_one_page(session, symbol, cursor_ms)
        if not rows:
            break
        all_rows.extend(rows)
        last_open = rows[-1][0]
        next_cursor = last_open + EXPECTED_BAR_MS
        if last_open >= end_ms or len(rows) < PAGE_LIMIT:
            break
        cursor_ms = next_cursor
        time.sleep(0.05)
    if not all_rows:
        raise RuntimeError(f"no klines returned for {symbol} in window")
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(all_rows, columns=cols)
    df = df[(df["open_time"] >= start_ms) & (df["open_time"] < end_ms)].copy()
    for c in ("open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_base", "taker_buy_quote"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("open_time", "close_time", "trades", "ignore"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("int64")
    df.sort_values("open_time", inplace=True)
    df.drop_duplicates(subset=["open_time"], keep="last", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def validate(df: pd.DataFrame) -> tuple[int, int]:
    if df.empty:
        return 0, 0
    diffs = df["open_time"].diff().fillna(EXPECTED_BAR_MS).astype("int64")
    gap_bars = (diffs / EXPECTED_BAR_MS).round().astype("int64")
    max_gap = int(gap_bars.max())
    misalign = int((df["open_time"] % EXPECTED_BAR_MS).abs().gt(GAP_TOLERANCE_MS).sum())
    return max_gap, misalign


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="ETHUSDT,SOLUSDT",
                    help="USDT-M perp symbols, comma-separated")
    ap.add_argument("--start", default=DEFAULT_START,
                    help="ISO date YYYY-MM-DD (UTC)")
    ap.add_argument("--end", default=None,
                    help="ISO date YYYY-MM-DD (UTC); default = now")
    ap.add_argument("--out-dir", default="/home/smark/multica/quant-loop/live_data",
                    help="output directory (default ~/multica/quant-loop/live_data)")
    args = ap.parse_args()

    start_ms = int(datetime.fromisoformat(args.start).replace(
        tzinfo=timezone.utc).timestamp() * 1000)
    if args.end is None:
        end_dt = datetime.now(timezone.utc)
    else:
        end_dt = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    end_ms = int(end_dt.timestamp() * 1000)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "interval": INTERVAL,
        "start_iso": args.start,
        "end_iso": end_dt.date().isoformat(),
        "endpoint": "fapi.binance.com (usdt-m perp)",
        "symbols": {},
    }
    overall_ok = True
    with requests.Session() as session:
        session.headers.update({"User-Agent": "vpvr-fetch/fetch_binance_usdm_4h (SMA-34866)"})

        for symbol in symbols:
            print(f"[fetch] {symbol} {INTERVAL} {args.start} -> "
                  f"{end_dt.date().isoformat()}", file=sys.stderr)
            t0 = time.time()
            try:
                df = fetch_symbol(session, symbol, start_ms, end_ms)
            except Exception as e:
                print(f"[fetch] {symbol}: FAILED {e}", file=sys.stderr)
                report["symbols"][symbol] = {"error": str(e)}
                overall_ok = False
                continue
            elapsed = time.time() - t0
            max_gap, misalign = validate(df)

            parquet_path = out_dir / f"{symbol}_4h.parquet"
            df.to_parquet(parquet_path, engine="pyarrow", index=False)

            rpt = FetchReport(
                symbol=symbol,
                rows=len(df),
                first_open_time_ms=int(df["open_time"].iloc[0]),
                last_open_time_ms=int(df["open_time"].iloc[-1]),
                max_gap_bars=max_gap,
                boundary_misalign_count=misalign,
                parquet_path=str(parquet_path),
                elapsed_s=elapsed,
            )
            print(f"[fetch] {symbol}: {rpt.rows} bars, "
                  f"max_gap={max_gap} bars, misalign={misalign}, "
                  f"elapsed={elapsed:.1f}s -> {parquet_path}", file=sys.stderr)
            report["symbols"][symbol] = rpt.to_dict()
            if rpt.rows < 5400 or max_gap > 1 or misalign > 0:
                overall_ok = False

    report_path = out_dir / "fetch_report_usdm_4h.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"[fetch] report: {report_path}", file=sys.stderr)
    print(f"[fetch] overall_ok={overall_ok}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
