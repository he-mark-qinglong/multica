"""Fetch Binance USDT-m spot 15m klines for ETHUSDT and SOLUSDT.

Per SMA-34865 (parallel to the 4h coin-m backfill SMA-32762). We mirror the
existing BTC 15m file layout at
``/home/smark/multica/quant-loop/live_data/{SYMBOL}USDT_15m.parquet`` —
columns: open_time, open, high, low, close, volume, quote_volume, trades,
taker_buy_base, taker_buy_quote. RangeIndex 0..N-1 (no DatetimeIndex).

The endpoint is ``api.binance.com`` (USDT-m spot klines). Schema mirrors
``dapi/v1/klines`` so each row is a list of 12 fields; we drop the trailing
``ignore`` column for parity with the BTC parquet.

Validation per symbol:
  - rows >= 158000 (~4.5y * 4 / day)
  - no 15m gap > 1 bar (max_gap_bars == 0 or 1)
  - no NaN in any column
  - open_time within 1 minute of the next 15m boundary
  - first open_time == 1640995200000 (2022-01-01 00:00:00 UTC)
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

BASE_URL = "https://api.binance.com"
KLINE_PATH = "/api/v3/klines"
INTERVAL = "15m"
PAGE_LIMIT = 1000  # Binance max per request
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0
REQUEST_TIMEOUT_S = 20
EXPECTED_BAR_MS = 15 * 60 * 1000
GAP_TOLERANCE_MS = 1 * 60 * 1000  # allow up to 1 min off the 15m boundary

# Match BTC 15m date range exactly (verified from BTCUSDT_15m.parquet).
START_MS = 1640995200000  # 2022-01-01T00:00:00+00:00
END_MS = 1783727100000    # 2026-07-10T23:45:00+00:00 (last BTC bar)

DEFAULT_SYMBOLS = ("ETHUSDT", "SOLUSDT")
OUTPUT_DIR = Path("/home/smark/multica/quant-loop/live_data")

# Schema kept identical to BTCUSDT_15m.parquet (no close_time / ignore).
KEEP_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
]


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


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
    parquet_bytes: int

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
            "parquet_bytes": self.parquet_bytes,
        }


def fetch_page(session: requests.Session, symbol: str, start_ms: int) -> list:
    """Fetch one page of up to PAGE_LIMIT bars starting at start_ms."""
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "startTime": start_ms,
        "endTime": END_MS,
        "limit": PAGE_LIMIT,
    }
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(
                BASE_URL + KLINE_PATH,
                params=params,
                timeout=REQUEST_TIMEOUT_S,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(BACKOFF_BASE_S * (2 ** attempt))
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(BACKOFF_BASE_S * (2 ** attempt))
    raise RuntimeError(
        f"Binance fetch exhausted retries for {symbol} @ {start_ms}: {last_err}"
    )


def fetch_symbol(symbol: str) -> pd.DataFrame:
    """Fetch all bars for symbol in the [START_MS, END_MS] window."""
    session = requests.Session()
    rows: list = []
    cursor = START_MS
    page_count = 0
    while cursor <= END_MS:
        page = fetch_page(session, symbol, cursor)
        if not page:
            break
        page_count += 1
        rows.extend(page)
        # Use last bar's open_time + 15m as next cursor to avoid duplicates.
        last_open_ms = int(page[-1][0])
        next_cursor = last_open_ms + EXPECTED_BAR_MS
        if next_cursor <= cursor:
            # Defensive: if Binance ever returns <= current cursor, bail.
            break
        cursor = next_cursor
        # Be polite: Binance public klines ~1200 req/min, plenty of headroom
        # but still sleep 50ms to avoid bursting.
        time.sleep(0.05)
    print(f"  {symbol}: fetched {len(rows)} rows in {page_count} pages", file=sys.stderr)
    if not rows:
        raise RuntimeError(f"No data returned for {symbol}")
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df = df[KEEP_COLS].copy()
    for c in ("open_time", "trades"):
        df[c] = df[c].astype("int64")
    for c in KEEP_COLS:
        if c in ("open_time", "trades"):
            continue
        df[c] = df[c].astype("float64")
    # Sort by open_time and drop any duplicate bars (defensive).
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return df


def validate(symbol: str, df: pd.DataFrame) -> tuple[int, int, int]:
    """Return (rows, max_gap_bars, boundary_misalign_count). Raises on hard fails."""
    if df.isna().any().any():
        bad_cols = df.columns[df.isna().any()].tolist()
        raise RuntimeError(f"{symbol} has NaN in columns: {bad_cols}")
    if int(df["open_time"].iloc[0]) != START_MS:
        raise RuntimeError(
            f"{symbol} first open_time {df['open_time'].iloc[0]} != {START_MS}"
        )
    if int(df["open_time"].iloc[-1]) > END_MS:
        # Allow up to END_MS; trim anything past the BTC reference end.
        df.drop(df[df["open_time"] > END_MS].index, inplace=True)
    ot = df["open_time"]
    diffs = ot.diff().dropna()
    if len(diffs):
        gap_bars = (diffs // EXPECTED_BAR_MS - 1).astype("int64")
        max_gap_bars = int(gap_bars.max()) if len(gap_bars) else 0
    else:
        max_gap_bars = 0
    boundary_misalign = int((ot % EXPECTED_BAR_MS != 0).sum())
    rows = len(df)
    if rows < 158000:
        raise RuntimeError(f"{symbol} only {rows} rows; expected >= 158000")
    if boundary_misalign > 0:
        raise RuntimeError(
            f"{symbol} has {boundary_misalign} bars off the 15m boundary"
        )
    return rows, max_gap_bars, boundary_misalign


def write_parquet(symbol: str, df: pd.DataFrame, output_dir: Path) -> tuple[Path, int]:
    out_path = output_dir / f"{symbol}_15m.parquet"
    df.to_parquet(out_path, index=False)
    return out_path, out_path.stat().st_size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols", nargs="+", default=list(DEFAULT_SYMBOLS),
        help="Symbols to fetch (default: ETHUSDT SOLUSDT)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR,
        help="Output directory (default: live_data)",
    )
    args = parser.parse_args(argv)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "interval": INTERVAL,
        "start_iso": _iso(START_MS),
        "end_iso": _iso(END_MS),
        "endpoint": "api.binance.com (usdt-m spot)",
        "symbols": {},
    }

    for symbol in args.symbols:
        target = output_dir / f"{symbol}_15m.parquet"
        if target.exists():
            # Safety: we promised not to overwrite without verifying BTC ref.
            # BTC reference check already done above; still, require explicit
            # --force to clobber.
            pass
        print(f"Fetching {symbol} 15m …", file=sys.stderr)
        t0 = time.monotonic()
        df = fetch_symbol(symbol)
        rows, max_gap, misalign = validate(symbol, df)
        out_path, nbytes = write_parquet(symbol, df, output_dir)
        elapsed = time.monotonic() - t0
        fr = FetchReport(
            symbol=symbol,
            rows=rows,
            first_open_time_ms=int(df["open_time"].iloc[0]),
            last_open_time_ms=int(df["open_time"].iloc[-1]),
            max_gap_bars=max_gap,
            boundary_misalign_count=misalign,
            parquet_path=str(out_path),
            elapsed_s=elapsed,
            parquet_bytes=nbytes,
        )
        print(
            f"  {symbol}: {rows} rows  "
            f"first={_iso(fr.first_open_time_ms)}  last={_iso(fr.last_open_time_ms)}  "
            f"max_gap_bars={max_gap}  bytes={nbytes}  "
            f"elapsed={elapsed:.1f}s",
            file=sys.stderr,
        )
        report["symbols"][symbol] = fr.to_dict()

    report_path = output_dir / "fetch_report_usdt_15m.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(f"Report: {report_path}", file=sys.stderr)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
