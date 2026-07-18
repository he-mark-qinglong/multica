"""Fetch Binance USDT-M perpetual 1m klines for VPVR/scalp strategies.

Mirrors fetch_binance_usdm_30m.py:
- Endpoint: fapi.binance.com (USDT-M futures)
- Interval: 1m
- Output: ~/multica/quant-loop/data/perp_1m/{SYMBOL}_1m.parquet
- Schema identical to the canonical 30m file (open_time, open, high, low,
  close, volume, close_time, quote_volume, trades, taker_buy_base,
  taker_buy_quote, ignore).

Notes on volume
---------------
1m klines for a 4.5y window are large (~2.4M rows/symbol). On disk the
snappy-compressed parquet typically lands between 60 and 200 MB per symbol
depending on the trade density of the pair. We use limit=1000 per page
(Binance max) and a 30ms sleep, which leaves ample headroom on the
`REQUEST_WEIGHT = 1200/min` budget (each `/klines` page = 2 weight units).

Boundary alignment
------------------
For a 1m cadence the natural bar width is 60_000 ms. We allow a
1500ms slack in boundary alignment so a couple of micro-off boundaries
don't fail validation.
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
INTERVAL = "1m"
PAGE_LIMIT = 1000          # Binance max for /klines
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0
REQUEST_TIMEOUT_S = 15
EXPECTED_BAR_MS = 60 * 1000
GAP_TOLERANCE_MS = 1500   # 1.5s slack for boundary alignment
DEFAULT_START = "auto"    # 'auto' => probe earliest available per symbol
DEFAULT_END = None        # default = "now" UTC
DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/data/perp_1m"
PROVENANCE_SOURCE = "binance fapi klines"

@dataclass
class FetchReport:
    symbol: str
    rows: int
    first_open_time_ms: int
    last_open_time_ms: int
    max_gap_bars: int
    boundary_misalign_count: int
    parquet_path: str
    csv_path: str
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
            "csv_path": self.csv_path,
            "elapsed_s": round(self.elapsed_s, 2),
        }


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _walk_first_bar(session: requests.Session, symbol: str) -> int:
    """Probe the earliest 1m bar for `symbol` on fapi.binance.com.

    Walks forward from 2010-01-01 in 6-month windows until a non-empty page
    is returned; the first row of that page is the symbol's earliest bar.
    Used when --start is 'auto'.
    """
    probe_cursor = int(datetime(2010, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    rows: list[list] = []
    while True:
        params = {"symbol": symbol, "interval": INTERVAL, "limit": PAGE_LIMIT, "startTime": probe_cursor}
        for attempt in range(MAX_RETRIES):
            try:
                r = session.get(BASE_URL + KLINE_PATH, params=params, timeout=REQUEST_TIMEOUT_S)
                if r.status_code == 200:
                    rows = r.json() or []
                    break
                if r.status_code in (418, 429):
                    time.sleep(BACKOFF_BASE_S * (2 ** attempt))
                    continue
                r.raise_for_status()
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise RuntimeError(f"probe failed for {symbol}: {e}")
                time.sleep(BACKOFF_BASE_S * (2 ** attempt))
        if rows:
            return int(rows[0][0])
        probe_cursor += 6 * 30 * 24 * 3600 * 1000
        if probe_cursor > int(time.time() * 1000):
            raise RuntimeError(f"no 1m bars found for {symbol} up to now")


def _write_csv_with_provenance(df: pd.DataFrame, csv_path: Path, symbol: str, total_bars: int) -> None:
    """Write the canonical CSV mirror with the provenance footer line.

    Footer format (matches SMA-34864 acceptance):
        # source: <PROVENANCE_SOURCE>, fetched YYYY-MM-DD, total bars: N, symbol: SYM
    """
    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    provenance = (
        f"# source: {PROVENANCE_SOURCE}, fetched {fetched}, "
        f"total bars: {total_bars}, symbol: {symbol}"
    )
    csv_path.write_text(provenance + "\n" + df.to_csv(index=False))


def fetch_symbol(session: requests.Session, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    all_rows: list[list] = []
    cursor_ms = start_ms
    page = 0
    while cursor_ms < end_ms:
        params = {"symbol": symbol, "interval": INTERVAL, "limit": PAGE_LIMIT, "startTime": cursor_ms, "endTime": end_ms}
        last_err: Exception | None = None
        rows = None
        for attempt in range(MAX_RETRIES):
            try:
                r = session.get(BASE_URL + KLINE_PATH, params=params, timeout=REQUEST_TIMEOUT_S)
                if r.status_code == 200:
                    rows = r.json()
                    break
                if r.status_code in (418, 429):
                    wait = BACKOFF_BASE_S * (2 ** attempt)
                    print(f"[fetch] {symbol} HTTP {r.status_code} attempt={attempt} sleeping {wait:.1f}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
            except Exception as e:
                last_err = e
                wait = BACKOFF_BASE_S * (2 ** attempt)
                print(f"[fetch] {symbol} err {e!r} attempt={attempt} sleeping {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
        if rows is None:
            raise RuntimeError(f"fetch failed for {symbol}: {last_err}")
        if not rows:
            break
        all_rows.extend(rows)
        page += 1
        next_cursor = rows[-1][0] + EXPECTED_BAR_MS
        # Defensive: stop if cursor stalls (paranoia against duplicate rows)
        if next_cursor <= cursor_ms:
            break
        cursor_ms = next_cursor
        # Cheap heartbeat every 200 pages (~33h of bars) so long fetches
        # don't look dead.
        if page % 200 == 0:
            print(f"[fetch] {symbol} page={page} rows={len(all_rows)} cursor={_iso(cursor_ms)}", file=sys.stderr)
        time.sleep(0.03)  # ~33 req/s, each page = 2 weight units => ~66 weight/s
    if not all_rows:
        raise RuntimeError(f"no klines returned for {symbol}")
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(all_rows, columns=cols)
    df = df[(df["open_time"] >= start_ms) & (df["open_time"] < end_ms)].copy()
    for c in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"):
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
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--start", default=DEFAULT_START,
                    help="ISO date 'YYYY-MM-DD' or 'auto' to probe earliest available")
    ap.add_argument("--end", default=DEFAULT_END, help="ISO date or datetime, UTC. Default = now.")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--format", default="parquet,csv",
                    help="comma-separated subset of {parquet, csv}")
    args = ap.parse_args()

    if args.start == "auto":
        start_iso = "auto"
        start_ms = None  # resolved per-symbol below
    else:
        start_iso = args.start
        start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    if args.end is None:
        end_dt = datetime.now(tz=timezone.utc)
    else:
        end_dt = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    end_ms = int(end_dt.timestamp() * 1000)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    formats = {f.strip().lower() for f in args.format.split(",") if f.strip()}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "interval": INTERVAL,
        "start_iso": start_iso,
        "end_iso": end_dt.date().isoformat(),
        "endpoint": "fapi.binance.com (usdt-m perp)",
        "out_dir": str(out_dir),
        "formats": sorted(formats),
        "symbols": {},
    }
    overall_ok = True
    with requests.Session() as session:
        session.headers.update({"User-Agent": "vpvr-1m-fetch/fetch_binance_usdm_1m"})
        for symbol in symbols:
            if start_ms is None:
                t_probe = time.time()
                probed_start_ms = _walk_first_bar(session, symbol)
                print(
                    f"[fetch] {symbol} probed earliest 1m bar at {_iso(probed_start_ms)} "
                    f"({time.time() - t_probe:.1f}s)",
                    file=sys.stderr,
                )
                symbol_start_ms = probed_start_ms
            else:
                symbol_start_ms = start_ms
            print(
                f"[fetch] {symbol} {INTERVAL} {_iso(symbol_start_ms)} -> {end_dt.date().isoformat()}",
                file=sys.stderr,
            )
            t0 = time.time()
            try:
                df = fetch_symbol(session, symbol, symbol_start_ms, end_ms)
            except Exception as e:
                print(f"[fetch] {symbol}: FAILED {e}", file=sys.stderr)
                report["symbols"][symbol] = {"error": str(e)}
                overall_ok = False
                continue
            elapsed = time.time() - t0
            max_gap, misalign = validate(df)
            parquet_path = out_dir / f"{symbol}_1m.parquet"
            csv_path = out_dir / f"{symbol}_1m.csv"
            if "parquet" in formats:
                df.to_parquet(parquet_path, engine="pyarrow", index=False)
            if "csv" in formats:
                _write_csv_with_provenance(df, csv_path, symbol, len(df))
            rpt = FetchReport(
                symbol=symbol,
                rows=len(df),
                first_open_time_ms=int(df["open_time"].iloc[0]),
                last_open_time_ms=int(df["open_time"].iloc[-1]),
                max_gap_bars=max_gap,
                boundary_misalign_count=misalign,
                parquet_path=str(parquet_path),
                csv_path=str(csv_path),
                elapsed_s=elapsed,
            )
            print(
                f"[fetch] {symbol}: {rpt.rows} bars, gap={max_gap}, misalign={misalign}, "
                f"{elapsed:.1f}s -> {parquet_path}",
                file=sys.stderr,
            )
            report["symbols"][symbol] = rpt.to_dict()
            # Acceptance from SMA-34864: non-empty, monotonic, no overlap,
            # gaps <= 5 minutes, no boundary misalignment. Rows check is loose
            # (>= 100k ≈ 70 days) because each symbol's full history varies
            # (BTC ~2019, ETH ~2019, SOL ~2020).
            if rpt.rows < 100_000 or max_gap > 5 or misalign > 0:
                overall_ok = False

    out_json = out_dir / "fetch_report_usdm_1m.json"
    out_json.write_text(json.dumps(report, indent=2))
    print(f"[fetch] report: {out_json}", file=sys.stderr)
    print(f"[fetch] overall_ok={overall_ok}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
