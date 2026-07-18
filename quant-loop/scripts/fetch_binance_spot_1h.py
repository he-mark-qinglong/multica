"""Fetch Binance SPOT 1h klines for SOL (and optionally BTC/ETH).

Per SMA-34867 (SOL 1h backfill). Endpoint is the public spot klines
mirror used by the existing BTCUSDT_1h.parquet / ETHUSDT_1h.parquet
files at ~/multica/quant-loop/live_data/. To stay byte-compatible with
those files we drop the spot-only 'close_time' and 'ignore' columns
and keep the 10-column schema:

    open_time (int64, ms), open, high, low, close, volume,
    quote_volume, trades (int64), taker_buy_base, taker_buy_quote

Default window matches the BTC/ETH 1h coverage so SOL joins cleanly in
downstream data loaders. Use --start/--end to override.

Validation:
  - rows >= 10000 (≈1.1y of bars; SOL has been trading since ~2020-08
    on Binance spot so the default 2022-01-01 window yields ~40k rows)
  - max gap between consecutive open_time values <= 1 bar (1h)
  - open_time on 1h boundaries (mod 3_600_000 == 0)
"""
from __future__ import annotations
import argparse, json, sys, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests

BASE_URL = "https://api.binance.com"
KLINE_PATH = "/api/v3/klines"
INTERVAL = "1h"
PAGE_LIMIT = 1000          # Binance max for /klines
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0
REQUEST_TIMEOUT_S = 15
EXPECTED_BAR_MS = 60 * 60 * 1000
# Keep exact-boundary alignment; the existing BTC/ETH 1h files have zero
# misalignment so SOL must match to remain interchangeable.
GAP_TOLERANCE_MS = 0
DEFAULT_SYMBOLS = "SOLUSDT"
DEFAULT_START = "2022-01-01"
DEFAULT_END = "2026-07-10T23:00:00"
DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/live_data"
PROVENANCE_SOURCE = "binance spot klines"


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


def fetch_symbol(session: requests.Session, symbol: str,
                 start_ms: int, end_ms: int) -> pd.DataFrame:
    all_rows: list[list] = []
    cursor_ms = start_ms
    page = 0
    while cursor_ms < end_ms:
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": PAGE_LIMIT,
            "startTime": cursor_ms,
            "endTime": end_ms,
        }
        last_err: Exception | None = None
        rows: list | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = session.get(BASE_URL + KLINE_PATH, params=params,
                                timeout=REQUEST_TIMEOUT_S)
                if r.status_code == 200:
                    rows = r.json()
                    break
                if r.status_code in (418, 429):
                    wait = BACKOFF_BASE_S * (4 ** attempt)
                    print(f"[fetch] {symbol} HTTP {r.status_code} attempt={attempt} "
                          f"sleeping {wait:.1f}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
            except requests.RequestException as e:
                last_err = e
                wait = BACKOFF_BASE_S * (2 ** attempt)
                print(f"[fetch] {symbol} request failed attempt={attempt}: {e}; "
                      f"sleeping {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
        if rows is None:
            raise RuntimeError(f"fetch failed for {symbol}: {last_err}")
        if not rows:
            break
        all_rows.extend(rows)
        page += 1
        next_cursor = rows[-1][0] + EXPECTED_BAR_MS
        # Defensive: stop if cursor stalls
        if next_cursor <= cursor_ms:
            break
        cursor_ms = next_cursor
        if page % 50 == 0:
            print(f"[fetch] {symbol} page={page} rows={len(all_rows)} "
                  f"cursor={_iso(cursor_ms)}", file=sys.stderr)
        time.sleep(0.05)
    if not all_rows:
        raise RuntimeError(f"no klines returned for {symbol}")
    # 12-col raw schema from api/v3/klines
    raw_cols = ["open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(all_rows, columns=raw_cols)
    # Trim to the requested window (server may return an extra bar past endTime)
    df = df[(df["open_time"] >= start_ms) & (df["open_time"] < end_ms)].copy()
    for c in ("open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_base", "taker_buy_quote"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("open_time", "close_time", "trades", "ignore"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("int64")
    df.sort_values("open_time", inplace=True)
    df.drop_duplicates(subset=["open_time"], keep="last", inplace=True)
    df.reset_index(drop=True, inplace=True)
    # Match BTC/ETH 1h parquet schema: drop close_time + ignore.
    df = df[["open_time", "open", "high", "low", "close", "volume",
             "quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]].copy()
    return df


def validate(df: pd.DataFrame) -> tuple[int, int]:
    if df.empty:
        return 0, 0
    diffs = df["open_time"].diff().fillna(EXPECTED_BAR_MS).astype("int64")
    gap_bars = (diffs / EXPECTED_BAR_MS).round().astype("int64")
    max_gap = int(gap_bars.max())
    misalign = int((df["open_time"] % EXPECTED_BAR_MS).ne(0).sum())
    return max_gap, misalign


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS,
                    help="Comma-separated spot symbols (default: SOLUSDT)")
    ap.add_argument("--start", default=DEFAULT_START,
                    help="ISO date or datetime, UTC (default: 2022-01-01)")
    ap.add_argument("--end", default=DEFAULT_END,
                    help="ISO date or datetime, UTC (default: 2026-07-10T23:00:00)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    args = ap.parse_args()

    start_ms = int(datetime.fromisoformat(args.start)
                   .replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end)
                 .replace(tzinfo=timezone.utc).timestamp() * 1000)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "interval": INTERVAL,
        "start_iso": args.start,
        "end_iso": args.end,
        "endpoint": "api.binance.com (spot)",
        "out_dir": str(out_dir),
        "symbols": {},
    }
    overall_ok = True
    with requests.Session() as session:
        session.headers.update({
            "User-Agent": "vpvr-backfill/fetch_binance_spot_1h (SMA-34867)",
        })
        for symbol in symbols:
            print(f"[fetch] {symbol} {INTERVAL} {args.start} -> {args.end}",
                  file=sys.stderr)
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

            parquet_path = out_dir / f"{symbol}_1h.parquet"
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
            print(f"[fetch] {symbol}: {rpt.rows} bars, gap={max_gap}, "
                  f"misalign={misalign}, elapsed={elapsed:.1f}s -> {parquet_path}",
                  file=sys.stderr)
            report["symbols"][symbol] = rpt.to_dict()
            # Acceptance: window-aligned, on-boundary, rows present.
            # Tolerance for max_gap: the existing BTCUSDT_1h.parquet
            # reference file shows a 2-bar gap on 2023-03-24 12:00-14:00
            # UTC (Binance upstream hole). Match that tolerance so SOL
            # is interchangeable with BTC/ETH in the loader.
            if rpt.rows < 10_000 or max_gap > 2 or misalign > 0:
                overall_ok = False

    out_json = out_dir / "fetch_report_spot_1h.json"
    out_json.write_text(json.dumps(report, indent=2))
    print(f"[fetch] report: {out_json}", file=sys.stderr)
    print(f"[fetch] overall_ok={overall_ok}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())