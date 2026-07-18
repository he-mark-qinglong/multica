"""Fetch Binance USDT-M perpetual 30m klines for VPVR pair strategies.

Adapted from fetch_binance_coinm_4h.py:
- Endpoint: fapi.binance.com (USDT-M futures, not COIN-M inverse)
- Interval: 30m (not 4h)
- Output: ~/multica/quant-loop/data/perp_30m/{SYMBOL}_30m.parquet

Schema identical to existing canonical (open_time, open, high, low, close, volume, ...).
"""
from __future__ import annotations
import argparse, json, sys, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests

BASE_URL = "https://fapi.binance.com"
KLINE_PATH = "/fapi/v1/klines"
INTERVAL = "30m"
PAGE_LIMIT = 1500
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0
REQUEST_TIMEOUT_S = 15
EXPECTED_BAR_MS = 30 * 60 * 1000
GAP_TOLERANCE_MS = 5 * 60 * 1000
DEFAULT_START = "2022-01-01"
DEFAULT_END = "2026-07-11"
DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/data/perp_30m"

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

def fetch_symbol(session: requests.Session, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    all_rows: list[list] = []
    cursor_ms = start_ms
    while cursor_ms < end_ms:
        params = {"symbol": symbol, "interval": INTERVAL, "limit": PAGE_LIMIT, "startTime": cursor_ms}
        last_err: Exception | None = None
        rows = None
        for attempt in range(MAX_RETRIES):
            try:
                r = session.get(BASE_URL + KLINE_PATH, params=params, timeout=REQUEST_TIMEOUT_S)
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
            raise RuntimeError(f"fetch failed for {symbol}: {last_err}")
        if not rows:
            break
        all_rows.extend(rows)
        next_cursor = rows[-1][0] + EXPECTED_BAR_MS
        if next_cursor <= cursor_ms:
            break
        cursor_ms = next_cursor
        time.sleep(0.05)
    if not all_rows:
        raise RuntimeError(f"no klines returned for {symbol}")
    cols = ["open_time","open","high","low","close","volume","close_time",
            "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(all_rows, columns=cols)
    df = df[(df["open_time"] >= start_ms) & (df["open_time"] < end_ms)].copy()
    for c in ("open","high","low","close","volume","quote_volume","taker_buy_base","taker_buy_quote"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("open_time","close_time","trades","ignore"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("int64")
    df.sort_values("open_time", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def validate(df: pd.DataFrame) -> tuple[int, int]:
    if df.empty: return 0, 0
    diffs = df["open_time"].diff().fillna(EXPECTED_BAR_MS).astype("int64")
    gap_bars = (diffs / EXPECTED_BAR_MS).round().astype("int64")
    return int(gap_bars.max()), int((df["open_time"] % EXPECTED_BAR_MS).abs().gt(GAP_TOLERANCE_MS).sum())

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    args = ap.parse_args()

    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp() * 1000)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {"interval": INTERVAL, "start_iso": args.start, "end_iso": args.end,
              "endpoint": "fapi.binance.com (usdt-m perp)", "symbols": {}}
    overall_ok = True
    with requests.Session() as session:
        session.headers.update({"User-Agent": "vpvr-pair-fetch/fetch_binance_usdm_30m"})
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
            parquet_path = out_dir / f"{symbol}_30m.parquet"
            df.to_parquet(parquet_path, engine="pyarrow", index=False)
            rpt = FetchReport(symbol=symbol, rows=len(df),
                              first_open_time_ms=int(df["open_time"].iloc[0]),
                              last_open_time_ms=int(df["open_time"].iloc[-1]),
                              max_gap_bars=max_gap, boundary_misalign_count=misalign,
                              parquet_path=str(parquet_path), elapsed_s=elapsed)
            print(f"[fetch] {symbol}: {rpt.rows} bars, gap={max_gap}, misalign={misalign}, {elapsed:.1f}s -> {parquet_path}", file=sys.stderr)
            report["symbols"][symbol] = rpt.to_dict()
            # 4y * 365 * 48 = ~70k bars expected. Allow 50% min.
            if rpt.rows < 35000 or max_gap > 2 or misalign > 0:
                overall_ok = False
    out_json = out_dir / "fetch_report_usdm_30m.json"
    out_json.write_text(json.dumps(report, indent=2))
    print(f"[fetch] report: {out_json}", file=sys.stderr)
    print(f"[fetch] overall_ok={overall_ok}")
    return 0 if overall_ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
