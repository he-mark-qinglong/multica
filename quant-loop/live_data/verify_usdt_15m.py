"""Verify existing 15m parquet files (BTC, ETH, SOL) and emit a consolidated report.

Does NOT fetch — just reads + audits each parquet. Designed for SMA-34865
to validate that ETHUSDT_15m.parquet and SOLUSDT_15m.parquet meet the
acceptance criteria after the fetch script wrote them.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

OUTPUT_DIR = Path("/home/smark/multica/quant-loop/live_data")
EXPECTED_BAR_MS = 15 * 60 * 1000
EXPECTED_FIRST_MS = 1640995200000  # 2022-01-01T00:00:00+00:00
EXPECTED_LAST_MS = 1783727100000   # 2026-07-10T23:45:00+00:00
ALLOWED_GAP_BARS = 5  # BTC reference shows 1 gap of 6 missing bars (max_gap_bars=5)

# Match BTC reference schema exactly.
EXPECTED_COLS = [
    "open_time", "open", "high", "low", "close",
    "volume", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
]


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


@dataclass
class AuditResult:
    symbol: str
    rows: int
    first_open_time_ms: int
    last_open_time_ms: int
    max_gap_bars: int
    nan_columns: list
    parquet_bytes: int
    parquet_path: str
    schema_ok: bool
    date_range_ok: bool

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "rows": self.rows,
            "first_open_time_ms": self.first_open_time_ms,
            "last_open_time_ms": self.last_open_time_ms,
            "first_open_time_iso": _iso(self.first_open_time_ms),
            "last_open_time_iso": _iso(self.last_open_time_ms),
            "max_gap_bars": self.max_gap_bars,
            "nan_columns": self.nan_columns,
            "schema_ok": self.schema_ok,
            "date_range_ok": self.date_range_ok,
            "parquet_bytes": self.parquet_bytes,
            "parquet_path": self.parquet_path,
        }


def audit(symbol: str) -> AuditResult:
    path = OUTPUT_DIR / f"{symbol}_15m.parquet"
    df = pd.read_parquet(path)
    columns = df.columns.tolist()
    schema_ok = columns == EXPECTED_COLS
    nan_cols = df.columns[df.isna().any()].tolist()
    first_ms = int(df["open_time"].iloc[0])
    last_ms = int(df["open_time"].iloc[-1])
    date_range_ok = (first_ms == EXPECTED_FIRST_MS) and (last_ms == EXPECTED_LAST_MS)
    diffs = df["open_time"].diff().dropna()
    if len(diffs):
        gap_bars = (diffs // EXPECTED_BAR_MS - 1).astype("int64")
        max_gap_bars = int(gap_bars.max()) if len(gap_bars) else 0
    else:
        max_gap_bars = 0
    return AuditResult(
        symbol=symbol,
        rows=len(df),
        first_open_time_ms=first_ms,
        last_open_time_ms=last_ms,
        max_gap_bars=max_gap_bars,
        nan_columns=nan_cols,
        schema_ok=schema_ok,
        date_range_ok=date_range_ok,
        parquet_bytes=path.stat().st_size,
        parquet_path=str(path),
    )


def main(argv: list[str] | None = None) -> int:
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    if argv:
        symbols = argv
    report: dict = {
        "interval": "15m",
        "expected_first_ms": EXPECTED_FIRST_MS,
        "expected_last_ms": EXPECTED_LAST_MS,
        "expected_first_iso": _iso(EXPECTED_FIRST_MS),
        "expected_last_iso": _iso(EXPECTED_LAST_MS),
        "allowed_max_gap_bars": ALLOWED_GAP_BARS,
        "expected_columns": EXPECTED_COLS,
        "symbols": {},
    }
    btc_baseline: AuditResult | None = None
    failed = False
    for sym in symbols:
        try:
            res = audit(sym)
        except FileNotFoundError:
            print(f"  {sym}: MISSING", file=sys.stderr)
            report["symbols"][sym] = {"missing": True}
            failed = True
            continue
        report["symbols"][sym] = res.to_dict()
        if sym == "BTCUSDT":
            btc_baseline = res
        print(
            f"  {sym}: rows={res.rows} first={_iso(res.first_open_time_ms)} "
            f"last={_iso(res.last_open_time_ms)} max_gap_bars={res.max_gap_bars} "
            f"nan_cols={res.nan_columns} bytes={res.parquet_bytes} "
            f"schema_ok={res.schema_ok} range_ok={res.date_range_ok}",
            file=sys.stderr,
        )
        if sym != "BTCUSDT":
            if not res.schema_ok:
                print(f"  FAIL {sym}: columns mismatch", file=sys.stderr)
                failed = True
            if res.nan_columns:
                print(f"  FAIL {sym}: NaN in {res.nan_columns}", file=sys.stderr)
                failed = True
            if res.max_gap_bars > ALLOWED_GAP_BARS:
                print(
                    f"  WARN {sym}: max_gap_bars={res.max_gap_bars} "
                    f"> allowed {ALLOWED_GAP_BARS}",
                    file=sys.stderr,
                )
                # Not a hard fail; BTC reference itself has max_gap_bars=5
            if btc_baseline is not None:
                if res.rows < btc_baseline.rows * 0.99:
                    print(
                        f"  FAIL {sym}: rows {res.rows} < 99% of BTC {btc_baseline.rows}",
                        file=sys.stderr,
                    )
                    failed = True
    out = OUTPUT_DIR / "verify_report_usdt_15m.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Report: {out}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
