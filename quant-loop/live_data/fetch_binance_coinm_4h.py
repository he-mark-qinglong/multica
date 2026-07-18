"""Fetch Binance COIN-M (inverse) 4h klines for vpvr_inverse_reversion_4h.

Per SMA-32633 V2 spec (iter#71, NEW inverse axis) and B3 task SMA-32762.
Inverse contract endpoint is dapi.binance.com/dapi/v1/klines; the schema
mirrors api/v3/klines so the parquet we emit is byte-for-byte compatible
with the existing BTCUSDT_4h.parquet (used by the USDT-margined catalog).

Schema: open_time (ms), open, high, low, close, volume, close_time,
        quote_volume, trades, taker_buy_base, taker_buy_quote, ignore.

Validation:
  - bars fetched per symbol >= 5400 (4.5y * 6 / day)
  - no 4h gap > 1 bar between consecutive open_time values
  - open_time within 5min of expected 4h boundary
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

BASE_URL = "https://dapi.binance.com"
KLINE_PATH = "/dapi/v1/klines"
INTERVAL = "4h"
PAGE_LIMIT = 1000
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0
REQUEST_TIMEOUT_S = 15
EXPECTED_BAR_MS = 4 * 60 * 60 * 1000
GAP_TOLERANCE_MS = 5 * 60 * 1000


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


def fetch_one_page(session: requests.Session, symbol: str, start_ms: int | None) -> list[list]:
    params = {"symbol": symbol, "interval": INTERVAL, "limit": PAGE_LIMIT}
    if start_ms is not None:
        params["startTime"] = start_ms
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(BASE_URL + KLINE_PATH, params=params, timeout=REQUEST_TIMEOUT_S)
            if r.status_code == 200:
                return r.json()
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


def fetch_symbol(session: requests.Session, symbol: str,
                 start_ms: int, end_ms: int) -> pd.DataFrame:
    all_rows: list[list] = []
    cursor_ms: int | None = start_ms
    page = 0
    while True:
        page += 1
        rows = fetch_one_page(session, symbol, cursor_ms)
        if not rows:
            break
        next_cursor = rows[-1][0] + EXPECTED_BAR_MS
        all_rows.extend(rows)
        last_open = rows[-1][0]
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
    ap.add_argument("--symbols", default="BTCUSD_PERP",
                    help="Coin-M perp symbol(s); default BTCUSD_PERP")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-07-11")
    ap.add_argument("--out-dir", default=str(Path(__file__).parent))
    args = ap.parse_args()

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp() * 1000)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, FetchReport | dict] = {
        "interval": INTERVAL,
        "start_iso": args.start,
        "end_iso": args.end,
        "endpoint": "dapi.binance.com (coin-m)",
        "symbols": {},
    }
    overall_ok = True
    with requests.Session() as session:
        session.headers.update({"User-Agent": "vpvr-backtest-runner/fetch_binance_coinm_4h (SMA-32762)"})

        for symbol in symbols:
            print(f"[fetch] {symbol} {INTERVAL} {args.start} -> {args.end}", file=sys.stderr)
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

            # Output name matches existing convention: strip "_PERP" suffix so
            # the strategy data_loader (which expects BTCUSD_4h.parquet) finds it.
            out_name = symbol.replace("_PERP", "")
            parquet_path = out_dir / f"{out_name}_4h.parquet"
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

    out_json = out_dir / "fetch_report_coinm_4h.json"
    out_json.write_text(json.dumps(report, indent=2))
    print(f"[fetch] report: {out_json}", file=sys.stderr)
    print(f"[fetch] overall_ok={overall_ok}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
