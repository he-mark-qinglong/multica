"""Audit all live_data parquets against SMA-34872 acceptance criteria.

Checks per file:
  - exists, > 1 KB
  - schema has OHLCV (open, high, low, close, volume) + open_time
  - open_time monotonic non-decreasing
  - close NaN count == 0
  - first/last open_time reported

For 4h files: BTCUSDT, ETHUSDT, SOLUSDT must all be present + non-symlink +
matching first_open_time (2022-01-01T00:00:00Z = 1640995200000).

Emits report at live_data/verify_report_sma34872.json.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

OUT_DIR = Path("/home/smark/multica/quant-loop/live_data")
SCHEMA_REQUIRED = {"open_time", "open", "high", "low", "close", "volume"}
EXPECTED_FIRST_MS = 1640995200000  # 2022-01-01T00:00:00+00:00
EXPECTED_BAR_MS = {
    "1h": 60 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}
ALLOWED_GAP_BARS = {"1h": 2, "15m": 5, "4h": 1}  # per existing reports


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def audit(symbol: str, tf: str) -> dict:
    p = OUT_DIR / f"{symbol}_{tf}.parquet"
    res: dict = {"symbol": symbol, "interval": tf, "path": str(p)}
    if not p.exists():
        res.update({"missing": True, "ok": False})
        return res
    size = p.stat().st_size
    res["size_bytes"] = size
    res["is_symlink"] = p.is_symlink()
    if size <= 1024:
        res.update({"too_small": True, "ok": False})
        return res
    df = pd.read_parquet(p)
    cols = set(df.columns.tolist())
    res["rows"] = len(df)
    schema_ok = SCHEMA_REQUIRED.issubset(cols)
    res["schema_ok"] = schema_ok
    nan_close = int(df["close"].isna().sum()) if "close" in df.columns else None
    res["nan_close"] = nan_close
    ts_diff = df["open_time"].diff().dropna()
    ts_mono = bool((ts_diff >= 0).all()) if len(ts_diff) else True
    res["ts_monotonic"] = ts_mono
    first_ms = int(df["open_time"].iloc[0])
    last_ms = int(df["open_time"].iloc[-1])
    res["first_open_time_ms"] = first_ms
    res["last_open_time_ms"] = last_ms
    res["first_open_time_iso"] = _iso(first_ms)
    res["last_open_time_iso"] = _iso(last_ms)
    bar_ms = EXPECTED_BAR_MS[tf]
    if len(ts_diff):
        gap_bars = (ts_diff // bar_ms - 1).astype("int64")
        max_gap = int(gap_bars.max()) if len(gap_bars) else 0
    else:
        max_gap = 0
    res["max_gap_bars"] = max_gap
    allowed = ALLOWED_GAP_BARS[tf]
    res["gap_within_allowed"] = max_gap <= allowed
    res["first_matches_baseline"] = first_ms == EXPECTED_FIRST_MS

    hard_fail = (
        not schema_ok
        or nan_close != 0
        or not ts_mono
        or not res["gap_within_allowed"]
        or res["is_symlink"]
    )
    res["ok"] = not hard_fail
    return res


def main() -> int:
    grid = [
        ("BTCUSDT", "1h"),
        ("BTCUSDT", "15m"),
        ("BTCUSDT", "4h"),
        ("ETHUSDT", "1h"),
        ("ETHUSDT", "15m"),
        ("ETHUSDT", "4h"),
        ("SOLUSDT", "1h"),
        ("SOLUSDT", "15m"),
        ("SOLUSDT", "4h"),
    ]
    report: dict = {
        "interval_bar_ms": EXPECTED_BAR_MS,
        "expected_first_ms": EXPECTED_FIRST_MS,
        "expected_first_iso": _iso(EXPECTED_FIRST_MS),
        "allowed_gap_bars": ALLOWED_GAP_BARS,
        "required_ohlcv_columns": sorted(SCHEMA_REQUIRED),
        "files": {},
    }
    failed: list[str] = []
    for sym, tf in grid:
        r = audit(sym, tf)
        report["files"][f"{sym}_{tf}"] = r
        status = "OK" if r.get("ok") else "FAIL"
        flags = []
        if r.get("is_symlink"):
            flags.append("symlink")
        if r.get("missing"):
            flags.append("missing")
        if r.get("too_small"):
            flags.append("too_small")
        if not r.get("schema_ok", True):
            flags.append("schema_bad")
        if r.get("nan_close"):
            flags.append("nan_close")
        if not r.get("ts_monotonic", True):
            flags.append("ts_not_monotonic")
        if not r.get("gap_within_allowed", True):
            flags.append("gap_too_large")
        flag_str = " ".join(flags) if flags else "-"
        print(
            f"  {status:>4} {sym}_{tf}: rows={r.get('rows')} first={r.get('first_open_time_iso','-')} "
            f"last={r.get('last_open_time_iso','-')} max_gap={r.get('max_gap_bars')} "
            f"size={r.get('size_bytes')} symlink={r.get('is_symlink')} [{flag_str}]",
            file=sys.stderr,
        )
        if not r.get("ok"):
            failed.append(f"{sym}_{tf}")
    report["all_ok"] = not failed
    report["failed"] = failed
    out = OUT_DIR / "verify_report_sma34872.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"Report: {out}", file=sys.stderr)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())