"""Atomic incremental refresher for live_data/{15m,1h}/{SYMBOL}USDT_TF.parquet.

Mirrors the schema of existing files (per-file, not per-symbol) by inspecting
the destination's dtypes and column list, then appends only bars strictly
after the existing `last_open_time`. Writes through .tmp + rename so a
crash mid-rename cannot leave the live file half-written or symlinked.

Endpoints:
  - 15m / 1h -> https://api.binance.com/api/v3/klines (USDT-m spot)
  - 1m       -> https://fapi.binance.com/fapi/v1/klines (USDT-m perp)

Each file ends at 2026-07-10T23:45 (15m) / 2026-07-10T22-23 (1h); refresh
extends through "now-1interval" so the 7d completeness window passes.

Per SMA-34871 acceptance: non-empty, non-symlink, monotonic, schema unchanged,
gap <= 1 bar at the leading edge (Binance historical maintenance).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

LIVE_DATA = Path("/home/smark/multica/quant-loop/live_data")
PERP_1M = Path("/home/smark/multica/quant-loop/data/perp_1m")

INTERVAL_MS = {"1m": 60_000, "15m": 15 * 60_000, "1h": 60 * 60_000}
ENDPOINTS = {
    "1m": ("perp", "https://fapi.binance.com/fapi/v1/klines"),
    "15m": ("spot", "https://api.binance.com/api/v3/klines"),
    "1h": ("spot", "https://api.binance.com/api/v3/klines"),
}
PAGE_LIMIT = 1000
SLEEP_BETWEEN_PAGES_S = 0.05  # 20 req/s, well below 1200 weight/min budget
REQUEST_TIMEOUT_S = 15
MAX_RETRIES = 5
KEEP_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_taker_buy_quote",
]  # placeholder; actual cols determined per file below


@dataclass
class RefreshResult:
    symbol: str
    interval: str
    src_path: str
    before_last_open_iso: str
    after_last_open_iso: str
    rows_before: int
    rows_appended: int
    rows_after: int
    max_gap_in_new_bars: int
    out_path: str
    elapsed_s: float
    dry_run: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _iso(ms: int | None) -> str:
    if ms is None:
        return "-"
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc).isoformat()


def _fetch_page(session: requests.Session, market: str, url: str,
                symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": PAGE_LIMIT,
    }
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
            if r.status_code == 200:
                return r.json() or []
            if r.status_code in (418, 429) or r.status_code >= 500:
                time.sleep(1.0 * (2 ** attempt))
                continue
            r.raise_for_status()
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(1.0 * (2 ** attempt))
    raise RuntimeError(f"fetch failed for {symbol} {interval}: {last_err}")


def _existing_file(path: Path) -> pd.DataFrame | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    return pd.read_parquet(path)


def _coerce_columns(df_new: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    """Match the reference file's column order and dtypes where overlapping."""
    ref_cols = list(ref.columns)
    out = df_new.copy()
    # Drop any columns not in ref schema
    for c in ref_cols:
        if c not in out.columns:
            # If ref expects a missing col, leave it absent for now
            continue
    out = out[[c for c in ref_cols if c in out.columns]]
    # Reorder to ref_col order, fill missing with NaN
    for c in ref_cols:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[ref_cols]
    # Coerce dtypes
    for c in ref_cols:
        rt = ref[c].dtype
        if pd.api.types.is_integer_dtype(rt):
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("int64")
        elif pd.api.types.is_float_dtype(rt):
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
        else:
            out[c] = out[c].astype(rt)
    return out


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, engine="pyarrow", index=False)
    tmp.replace(path)


def _validate_no_internal_gap(df_new: pd.DataFrame, interval_ms: int) -> tuple[int, int]:
    if df_new.empty or len(df_new) < 2:
        return 0, 0
    diffs = df_new["open_time"].diff().dropna()
    gap_bars = (diffs // interval_ms - 1).astype("int64")
    gap_bars = gap_bars[gap_bars > 0]
    max_gap = int(gap_bars.max()) if len(gap_bars) else 0
    misalign = int((df_new["open_time"] % interval_ms != 0).sum())
    return max_gap, misalign


def refresh_one(symbol: str, interval: str, *,
                force: bool = False,
                dry_run: bool = False) -> RefreshResult:
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval {interval}")
    market, url = ENDPOINTS[interval]
    interval_ms = INTERVAL_MS[interval]

    if interval in ("1m",):
        base = PERP_1M
        path = base / f"{symbol}_1m.parquet"
    else:
        base = LIVE_DATA
        path = base / f"{symbol}_{interval}.parquet"

    existing = _existing_file(path)
    if existing is None:
        return RefreshResult(
            symbol=symbol, interval=interval, src_path=str(path),
            before_last_open_iso="-", after_last_open_iso="-",
            rows_before=0, rows_appended=0, rows_after=0,
            max_gap_in_new_bars=0, out_path=str(path),
            elapsed_s=0.0, dry_run=dry_run,
        )

    last_ms = int(existing["open_time"].iloc[-1])
    # Advance past the last bar's open_time by exactly one interval boundary.
    start_ms = last_ms + interval_ms
    # Cap end at "now - 1 interval" so the most-recent bar is fully closed.
    import datetime as _dt
    now_ms = int(_dt.datetime.now(tz=_dt.timezone.utc).timestamp() * 1000)
    end_ms = now_ms - interval_ms

    if not force and start_ms >= end_ms:
        return RefreshResult(
            symbol=symbol, interval=interval, src_path=str(path),
            before_last_open_iso=_iso(last_ms),
            after_last_open_iso=_iso(last_ms),
            rows_before=len(existing), rows_appended=0, rows_after=len(existing),
            max_gap_in_new_bars=0, out_path=str(path),
            elapsed_s=0.0, dry_run=dry_run,
        )

    t0 = time.monotonic()
    fetched_rows: list[list] = []
    session = requests.Session()
    session.headers.update({"User-Agent": "vpvr-refresh-sma34871"})
    cursor = start_ms
    page = 0
    while cursor <= end_ms:
        rows = _fetch_page(session, market, url, symbol, interval, cursor, end_ms)
        if not rows:
            break
        fetched_rows.extend(rows)
        page += 1
        nxt = int(rows[-1][0]) + interval_ms
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(SLEEP_BETWEEN_PAGES_S)
    elapsed = time.monotonic() - t0

    if not fetched_rows:
        return RefreshResult(
            symbol=symbol, interval=interval, src_path=str(path),
            before_last_open_iso=_iso(last_ms),
            after_last_open_iso=_iso(last_ms),
            rows_before=len(existing), rows_appended=0, rows_after=len(existing),
            max_gap_in_new_bars=0, out_path=str(path),
            elapsed_s=elapsed, dry_run=dry_run,
        )

    # Binance spot kline schema (12 cols); perp identical.
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    new_df = pd.DataFrame(fetched_rows, columns=cols)
    # Drop close_time / ignore for spot kline consumer parity (existing 15m/1h files
    # have no close_time / ignore). 1m perp KEEP them.
    if interval in ("15m", "1h"):
        drop = [c for c in ("close_time", "ignore") if c in new_df.columns]
        new_df = new_df.drop(columns=drop)
    # Numeric coercion
    for c in new_df.columns:
        if c in ("open_time", "close_time", "trades", "ignore"):
            new_df[c] = pd.to_numeric(new_df[c], errors="coerce").astype("int64")
        else:
            new_df[c] = pd.to_numeric(new_df[c], errors="coerce").astype("float64")
    new_df = new_df.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)

    max_gap, misalign = _validate_no_internal_gap(new_df, interval_ms)
    if misalign > 0:
        raise RuntimeError(f"{symbol} {interval}: {misalign} bars off the boundary; aborting before write")

    # Coerce to existing-file schema (drop any cols we don't have a slot for)
    new_df = _coerce_columns(new_df, existing)
    combined = pd.concat([existing, new_df[new_df["open_time"] > last_ms]], ignore_index=True)
    combined = combined.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time").reset_index(drop=True)

    after_last_ms = int(combined["open_time"].iloc[-1])
    result = RefreshResult(
        symbol=symbol, interval=interval, src_path=str(path),
        before_last_open_iso=_iso(last_ms),
        after_last_open_iso=_iso(after_last_ms),
        rows_before=len(existing),
        rows_appended=int((combined["open_time"] > last_ms).sum()),
        rows_after=len(combined),
        max_gap_in_new_bars=max_gap,
        out_path=str(path),
        elapsed_s=round(elapsed, 2),
        dry_run=dry_run,
    )

    if not dry_run:
        _atomic_write_parquet(combined, path)

    return result


def _resolve_targets(args_symbols: Iterable[str],
                     args_intervals: Iterable[str]) -> list[tuple[str, str]]:
    out = []
    for sym in args_symbols:
        for itv in args_intervals:
            out.append((sym, itv))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--intervals", nargs="+", default=["15m", "1h"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    overall_ok = True
    report: dict = {
        "interval_ms": {k: v for k, v in INTERVAL_MS.items()},
        "endpoints": ENDPOINTS,
        "results": [],
        "overall_ok": True,
    }
    targets = _resolve_targets(args.symbols, args.intervals)
    for sym, itv in targets:
        try:
            r = refresh_one(sym, itv, force=args.force, dry_run=args.dry_run)
        except Exception as exc:
            print(f"  FAIL {sym} {itv}: {exc}", file=sys.stderr)
            report["results"].append({"symbol": sym, "interval": itv, "error": str(exc)})
            overall_ok = False
            continue
        print(
            f"  {sym} {itv}: rows {r.rows_before} -> {r.rows_after} "
            f"(+{r.rows_appended})  last {r.before_last_open_iso} -> {r.after_last_open_iso}  "
            f"max_gap_in_new={r.max_gap_in_new_bars}  elapsed={r.elapsed_s}s",
            file=sys.stderr,
        )
        report["results"].append(r.to_dict())

    report["overall_ok"] = overall_ok
    report_path = LIVE_DATA / "refresh_report_sma34871.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"Report: {report_path}", file=sys.stderr)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
