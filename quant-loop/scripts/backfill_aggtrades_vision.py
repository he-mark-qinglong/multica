#!/usr/bin/env python3
"""Backfill Binance USDⓈ-M perp aggTrades from data.binance.vision (offline batch).

Why vision instead of the fapi REST endpoint: no request-weight budget, one
file per symbol-month (or symbol-day), exact same payload as /fapi/v1/aggTrades.

Output layout (joinable with perp_1m/perp_30m/funding on symbol + ts):

    <out-dir>/<SYMBOL>_aggtrades.parquet/year=YYYY/month=M/data.parquet

i.e. each symbol is a hive-partitioned parquet *dataset* directory readable
with a single pd.read_parquet("<out-dir>/<SYMBOL>_aggtrades.parquet").
Monthly partitioning per SMA-34992; raw ms timestamps preserved (no bucketing).

Schema written (mirrors funding/README.md conventions):

    ts              timestamp[ms, tz=UTC]   (= transact_time)
    symbol          str
    agg_id          int64                   (a)
    price           float64                 (p)
    qty             float64                 (q)
    first_id        int64                   (f)
    last_id         int64                   (l)
    is_buyer_maker  bool                    (m; True => seller-initiated)

Idempotent: an existing non-empty data.parquet partition is skipped, so
re-running the same command resumes after an interruption.

Usage:
    python3 backfill_aggtrades_vision.py \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT \
        --start 2026-04-19 --end 2026-07-18 \
        --out-dir /home/smark/multica/quant-loop/data/trades
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

VISION_BASE = "https://data.binance.vision/data/futures/um"
CHUNK_ROWS = 2_000_000
MIN_PARTITION_BYTES = 64  # below this a partition is treated as broken/empty

SCHEMA = pa.schema(
    [
        ("ts", pa.timestamp("ms", tz="UTC")),
        ("symbol", pa.string()),
        ("agg_id", pa.int64()),
        ("price", pa.float64()),
        ("qty", pa.float64()),
        ("first_id", pa.int64()),
        ("last_id", pa.int64()),
        ("is_buyer_maker", pa.bool_()),
    ]
)

COLUMNS = {
    "agg_trade_id": "agg_id",
    "price": "price",
    "quantity": "qty",
    "first_trade_id": "first_id",
    "last_trade_id": "last_id",
    "transact_time": "ts",
    "is_buyer_maker": "is_buyer_maker",
}


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}] {msg}", file=sys.stderr, flush=True)


def download(url: str, dest: Path, retries: int = 5) -> bool:
    """curl download with retries; False if the object does not exist (404)."""
    for attempt in range(retries):
        rc = subprocess.run(
            ["curl", "-fSL", "--retry", "3", "--connect-timeout", "30",
             "--max-time", "1800", "-o", str(dest), url],
            capture_output=True, text=True,
        ).returncode
        if rc == 0:
            return True
        if rc == 22:  # HTTP error (404 etc.) — do not retry
            return False
        wait = 5 * (attempt + 1)
        log(f"  download rc={rc} for {url}, retry {attempt + 1}/{retries} in {wait}s")
        time.sleep(wait)
    raise RuntimeError(f"download failed after {retries} attempts: {url}")


def month_starts(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    """(year, month) pairs intersecting [start, end)."""
    out = []
    y, m = start.year, start.month
    while dt.date(y, m, 1) < end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    first = dt.date(year, month, 1)
    nxt = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return first, nxt


def normalize(chunk: pd.DataFrame, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    chunk = chunk.rename(columns=COLUMNS)
    # Defend against µs timestamps (some vision datasets switched units in the past).
    if chunk["ts"].iloc[0] > 10**14:
        chunk["ts"] = chunk["ts"] // 1000
    chunk = chunk[(chunk["ts"] >= start_ms) & (chunk["ts"] < end_ms)]
    if chunk.empty:
        return chunk
    chunk["ts"] = pd.to_datetime(chunk["ts"], unit="ms", utc=True)
    chunk["symbol"] = symbol
    chunk["price"] = chunk["price"].astype("float64")
    chunk["qty"] = chunk["qty"].astype("float64")
    chunk["is_buyer_maker"] = chunk["is_buyer_maker"].astype(str).str.lower().map(
        {"true": True, "false": False}
    )
    for c in ("agg_id", "first_id", "last_id"):
        chunk[c] = chunk[c].astype("int64")
    return chunk[["ts", "symbol", "agg_id", "price", "qty", "first_id", "last_id", "is_buyer_maker"]]


def stream_zip_to_writer(zip_path: Path, writer: pq.ParquetWriter, symbol: str,
                         start_ms: int, end_ms: int) -> int:
    rows = 0
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            raise RuntimeError(f"no csv inside {zip_path}")
        with zf.open(names[0]) as fh:
            # Older vision dumps (pre-2022-ish) have no header row; newer ones do.
            first_line = fh.readline().decode("utf-8", errors="replace")
            fh.seek(0)
            if "transact_time" in first_line:
                reader = pd.read_csv(fh, chunksize=CHUNK_ROWS)
            else:
                reader = pd.read_csv(fh, chunksize=CHUNK_ROWS, header=None,
                                     names=list(COLUMNS.keys()))
            for chunk in reader:
                chunk = normalize(chunk, symbol, start_ms, end_ms)
                if chunk.empty:
                    continue
                writer.write_table(pa.Table.from_pandas(chunk, schema=SCHEMA, preserve_index=False))
                rows += len(chunk)
    return rows


def backfill_symbol(symbol: str, start: dt.date, end: dt.date, out_dir: Path,
                    tmp_dir: Path) -> dict:
    start_ms = int(dt.datetime.combine(start, dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000)
    end_ms = int(dt.datetime.combine(end, dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000)
    today = dt.datetime.now(dt.timezone.utc).date()
    sym_report = {"symbol": symbol, "partitions": {}, "rows": 0, "skipped": 0}

    for year, month in month_starts(start, end):
        m_first, m_next = month_range(year, month)
        win_start, win_end = max(start, m_first), min(end, m_next)
        if win_start >= win_end:
            continue
        part = out_dir / f"{symbol}_aggtrades.parquet" / f"year={year}" / f"month={month}" / "data.parquet"
        if part.exists() and part.stat().st_size > MIN_PARTITION_BYTES:
            log(f"{symbol} {year}-{month:02d}: partition exists ({part.stat().st_size} bytes), skip")
            sym_report["skipped"] += 1
            continue
        part.parent.mkdir(parents=True, exist_ok=True)
        tmp_parquet = part.with_suffix(".parquet.tmp")
        writer = pq.ParquetWriter(tmp_parquet, SCHEMA, compression="snappy")
        part_rows = 0
        month_missing = False
        t0 = time.time()
        try:
            use_monthly = m_next <= today  # completed month => monthly zip exists
            if use_monthly:
                url = f"{VISION_BASE}/monthly/aggTrades/{symbol}/{symbol}-aggTrades-{year}-{month:02d}.zip"
                zip_path = tmp_dir / f"{symbol}-{year}-{month:02d}.zip"
                if not download(url, zip_path):
                    # Pre-listing month (or a gap on the vision mirror): skip so one
                    # missing zip does not kill the symbol's whole backfill. The
                    # final verify report exposes any real hole in coverage.
                    log(f"{symbol} {year}-{month:02d}: monthly zip not published, skipping month")
                    month_missing = True
                else:
                    part_rows = stream_zip_to_writer(zip_path, writer, symbol, start_ms, end_ms)
                    zip_path.unlink()
            else:
                day = win_start
                while day < win_end:
                    url = f"{VISION_BASE}/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day.isoformat()}.zip"
                    zip_path = tmp_dir / f"{symbol}-{day.isoformat()}.zip"
                    if download(url, zip_path):
                        part_rows += stream_zip_to_writer(zip_path, writer, symbol, start_ms, end_ms)
                        zip_path.unlink()
                    else:
                        log(f"{symbol} {day}: daily zip not published yet, skipping")
                    day += dt.timedelta(days=1)
        finally:
            writer.close()
        if month_missing:
            tmp_parquet.unlink(missing_ok=True)
            continue
        if part_rows == 0:
            tmp_parquet.unlink(missing_ok=True)
            raise RuntimeError(f"{symbol} {year}-{month:02d}: 0 rows collected, refusing to write partition")
        tmp_parquet.rename(part)
        elapsed = time.time() - t0
        log(f"{symbol} {year}-{month:02d}: {part_rows:,} rows -> {part} ({part.stat().st_size / 1e6:.1f} MB, {elapsed:.0f}s)")
        sym_report["partitions"][f"{year}-{month:02d}"] = part_rows
        sym_report["rows"] += part_rows

    return sym_report


def verify(out_dir: Path, symbols: list[str]) -> dict:
    """Read back every partition: rows + ts bounds per symbol."""
    report = {}
    for symbol in symbols:
        ds = out_dir / f"{symbol}_aggtrades.parquet"
        parts = sorted(ds.glob("year=*/month=*/data.parquet"))
        rows, first_ts, last_ts = 0, None, None
        per_part = {}
        for p in parts:
            meta = pq.read_metadata(p)
            tbl = pq.read_table(p, columns=["ts"])
            ts = tbl.column("ts").to_pandas()
            per_part[p.parent.parent.name + "/" + p.parent.name] = len(ts)
            rows += len(ts)
            lo, hi = ts.min(), ts.max()
            first_ts = lo if first_ts is None or lo < first_ts else first_ts
            last_ts = hi if last_ts is None or hi > last_ts else last_ts
        report[symbol] = {
            "rows": rows,
            "partitions": per_part,
            "first_ts": first_ts.isoformat() if first_ts is not None else None,
            "last_ts": last_ts.isoformat() if last_ts is not None else None,
            "bytes": sum(p.stat().st_size for p in parts),
        }
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True, help="comma-separated")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD, exclusive")
    ap.add_argument("--out-dir", default="/home/smark/multica/quant-loop/data/trades")
    ap.add_argument("--report", default=None)
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="aggtrades_", dir=out_dir))
    log(f"symbols={symbols} window=[{start}, {end}) out={out_dir}")

    if not args.verify_only:
        for symbol in symbols:
            log(f"=== {symbol} ===")
            backfill_symbol(symbol, start, end, out_dir, tmp_dir)
        # clean temp dir (best effort)
        for p in tmp_dir.glob("*"):
            p.unlink()
        tmp_dir.rmdir()

    log("verifying ...")
    vrep = verify(out_dir, symbols)
    total = sum(r["rows"] for r in vrep.values())
    vrep["_total_rows"] = total
    vrep["_window"] = {"start": start.isoformat(), "end_exclusive": end.isoformat()}
    report_path = args.report or str(out_dir / "fetch_report_aggtrades.json")
    Path(report_path).write_text(json.dumps(vrep, indent=2))
    log(f"total rows: {total:,} -> report {report_path}")
    for s, r in vrep.items():
        if s.startswith("_"):
            continue
        print(f"{s}: {r['rows']:,} rows, {r['first_ts']} .. {r['last_ts']}, {r['bytes'] / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
