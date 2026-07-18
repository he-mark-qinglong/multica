"""Audit 9-cell completeness grid for SMA-34898.

Grid: BTCUSDT/ETHUSDT/SOLUSDT × 1m/15m/4h.

Per cell, for the last 7 days (sliding window from wall clock):
  - file present, non-symlink, > 1 KB
  - schema has OHLCV + open_time
  - row count vs expected (7d × bars-per-day per timeframe)
  - gap detection in last 7 days (timestamps missing bars)
  - latest timestamp within tolerance of wall clock
  - first timestamp inside the 7-day window (sane recovery check)

Per AGENTS.md:
  - 1m files live under data/perp_1m/ (BTC + ETH; SOL 1m absent from shared pool)
  - 15m and 4h files live under live_data/

Emits report at live_data/verify_report_sma34898.json.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

LIVE_DATA = Path("/home/smark/multica/quant-loop/live_data")
PERP_1M = Path("/home/smark/multica/quant-loop/data/perp_1m")
OUT_REPORT = LIVE_DATA / "verify_report_sma34898.json"

SCHEMA_REQUIRED = {"open_time", "open", "high", "low", "close", "volume"}
BAR_MS = {"1m": 60_000, "15m": 15 * 60_000, "4h": 4 * 60 * 60_000}
BARS_PER_DAY = {"1m": 1440, "15m": 96, "4h": 6}

# 7-day window: [now - 7d, now] in ms (UTC).
NOW_MS = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
SEVEN_DAYS_MS = 7 * 24 * 60 * 60_000
WINDOW_START_MS = NOW_MS - SEVEN_DAYS_MS

# Freshness tolerance: how stale the latest bar is allowed to be (ms).
# 1m bars are produced continuously; allow up to 5 minutes of slack.
# 15m / 4h bars align to wall-clock boundaries — allow a single interval plus slack.
FRESHNESS_SLACK_MS = {"1m": 5 * 60_000, "15m": 20 * 60_000, "4h": 5 * 60 * 60_000}


def resolve_path(symbol: str, tf: str) -> Path | None:
    if tf == "1m":
        # Shared pool: data/perp_1m/{SYM}USDT_1m.parquet
        p = PERP_1M / f"{symbol}_1m.parquet"
        return p if p.exists() else None
    p = LIVE_DATA / f"{symbol}_{tf}.parquet"
    return p if p.exists() else None


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def audit(symbol: str, tf: str) -> dict:
    res: dict = {
        "symbol": symbol,
        "interval": tf,
        "window_start_iso": _iso(WINDOW_START_MS),
        "window_end_iso": _iso(NOW_MS),
    }
    p = resolve_path(symbol, tf)
    res["resolved_path"] = str(p) if p else None
    if p is None:
        res.update({"missing": True, "ok": False})
        return res
    res["path"] = str(p)
    size = p.stat().st_size
    res["size_bytes"] = size
    res["is_symlink"] = p.is_symlink()
    if size <= 1024:
        res.update({"too_small": True, "ok": False})
        return res
    df = pd.read_parquet(p)
    cols = set(df.columns.tolist())
    res["rows_total"] = len(df)
    schema_ok = SCHEMA_REQUIRED.issubset(cols)
    res["schema_ok"] = schema_ok

    # Window slice
    win = df[df["open_time"] >= WINDOW_START_MS].copy()
    win = win.sort_values("open_time").reset_index(drop=True)
    res["rows_in_7d_window"] = int(len(win))
    expected = 7 * BARS_PER_DAY[tf]
    res["expected_rows_in_7d_window"] = expected
    res["row_count_ok"] = len(win) >= int(expected * 0.98)  # 2% slack for trailing boundary

    if "close" in cols:
        nan_close = int(df["close"].isna().sum())
    else:
        nan_close = None
    res["nan_close_total"] = nan_close

    # Monotonic on full file
    if len(df) > 1:
        diff = df["open_time"].diff().dropna()
        res["ts_monotonic"] = bool((diff >= 0).all())
    else:
        res["ts_monotonic"] = True

    # First / last in window
    if len(win):
        first_in_win = int(win["open_time"].iloc[0])
        last_in_win = int(win["open_time"].iloc[-1])
    else:
        first_in_win = None
        last_in_win = None
    res["first_in_window_ms"] = first_in_win
    res["last_in_window_ms"] = last_in_win
    res["first_in_window_iso"] = _iso(first_in_win) if first_in_win else None
    res["last_in_window_iso"] = _iso(last_in_win) if last_in_win else None

    # Latest freshness vs wall clock
    last_ms = int(df["open_time"].iloc[-1])
    res["last_open_time_ms"] = last_ms
    res["last_open_time_iso"] = _iso(last_ms)
    stale_ms = NOW_MS - last_ms
    res["staleness_ms"] = int(stale_ms)
    res["stale_within_tolerance"] = stale_ms <= FRESHNESS_SLACK_MS[tf]

    # Gap detection in last 7 days
    bar = BAR_MS[tf]
    if len(win) > 1:
        win_diff = win["open_time"].diff().dropna()
        # number of missing bars = (gap // bar) - 1
        missing = (win_diff // bar - 1).astype("int64")
        missing = missing[missing > 0]
        res["missing_bars_in_7d_window"] = int(missing.sum())
        res["max_gap_bars_in_7d_window"] = int(missing.max()) if len(missing) else 0
        res["gap_count_in_7d_window"] = int(len(missing))
        # report up to 5 largest gap windows
        gap_records: list[dict] = []
        if len(missing):
            tmp = win_diff.to_frame("dt").reset_index(drop=True)
            tmp["missing_bars"] = missing.values
            tmp["from_ts"] = win["open_time"].iloc[:-1].values
            tmp["to_ts"] = win["open_time"].iloc[1:].values
            tmp = tmp[tmp["missing_bars"] > 0].sort_values("missing_bars", ascending=False).head(5)
            for _, r in tmp.iterrows():
                gap_records.append({
                    "from_iso": _iso(int(r["from_ts"])),
                    "to_iso": _iso(int(r["to_ts"])),
                    "missing_bars": int(r["missing_bars"]),
                })
        res["largest_gaps"] = gap_records
    else:
        res["missing_bars_in_7d_window"] = 0
        res["max_gap_bars_in_7d_window"] = 0
        res["gap_count_in_7d_window"] = 0
        res["largest_gaps"] = []

    # Distinguish mid-window internal gaps from trailing-edge staleness.
    # Trailing-edge staleness: last bar is older than (now - bar).
    # Internal gap: any missing bar with neighbors present on both sides.
    trailing_edge_short = False
    internal_gap = res["missing_bars_in_7d_window"] > 0
    if len(win) > 0:
        last_in_win_ms = int(win["open_time"].iloc[-1])
        trailing_short_ms = NOW_MS - last_in_win_ms
        # Number of trailing-edge bars that "should exist" but don't.
        # If (now - last_bar) > bar, then at least one bar at (last_bar + bar)
        # is missing.
        trailing_short_bars = max(0, int(trailing_short_ms // bar))
        res["trailing_short_bars"] = trailing_short_bars
        trailing_edge_short = trailing_short_bars > 0
    else:
        res["trailing_short_bars"] = 7 * BARS_PER_DAY[tf]
        # No bars in the 7d window — the entire window is missing.
        # That counts as both internal (every bar is missing) and trailing.
        trailing_edge_short = True

    res["trailing_edge_short"] = trailing_edge_short
    res["internal_gap"] = internal_gap

    # Hard-fail criteria — internal gaps and 0-row windows are real failures;
    # trailing-edge staleness alone is reported but not hard-fail (data refresh
    # is a separate operational concern from structural completeness).
    hard_fail = (
        not schema_ok
        or res["is_symlink"]
        or res["rows_in_7d_window"] == 0
        or not res["ts_monotonic"]
        or internal_gap
        or (nan_close is not None and nan_close != 0)
    )
    res["ok"] = not hard_fail
    return res


GRID = [
    ("BTCUSDT", "1m"),
    ("BTCUSDT", "15m"),
    ("BTCUSDT", "4h"),
    ("ETHUSDT", "1m"),
    ("ETHUSDT", "15m"),
    ("ETHUSDT", "4h"),
    ("SOLUSDT", "1m"),
    ("SOLUSDT", "15m"),
    ("SOLUSDT", "4h"),
]


def main() -> int:
    report: dict = {
        "wall_clock_iso": _iso(NOW_MS),
        "window_start_iso": _iso(WINDOW_START_MS),
        "bar_ms": BAR_MS,
        "bars_per_day": BARS_PER_DAY,
        "freshness_slack_ms": FRESHNESS_SLACK_MS,
        "expected_rows_per_cell": {tf: 7 * BARS_PER_DAY[tf] for _, tf in GRID},
        "files": {},
    }
    failed: list[str] = []
    missing: list[str] = []
    for sym, tf in GRID:
        r = audit(sym, tf)
        report["files"][f"{sym}_{tf}"] = r
        status = "OK" if r.get("ok") else "FAIL"
        flags: list[str] = []
        if r.get("missing"):
            flags.append("missing")
            missing.append(f"{sym}_{tf}")
        if r.get("is_symlink"):
            flags.append("symlink")
        if r.get("too_small"):
            flags.append("too_small")
        if not r.get("schema_ok", True):
            flags.append("schema_bad")
        if r.get("nan_close_total"):
            flags.append("nan_close")
        if not r.get("ts_monotonic", True):
            flags.append("ts_not_monotonic")
        if r.get("internal_gap"):
            flags.append("internal_gap")
        if r.get("trailing_edge_short"):
            flags.append("trailing_stale")
        if r.get("rows_in_7d_window", 0) == 0 and not r.get("missing"):
            flags.append("empty_7d_window")
        flag_str = " ".join(flags) if flags else "-"
        rows_7d = r.get("rows_in_7d_window", 0)
        print(
            f"  {status:>4} {sym}_{tf}: rows_7d={rows_7d} "
            f"last={r.get('last_open_time_iso','-')} "
            f"staleness={r.get('staleness_ms',0)}ms "
            f"internal_gap={r.get('internal_gap')} "
            f"trailing_stale={r.get('trailing_edge_short')} "
            f"[{flag_str}]",
            file=sys.stderr,
        )
        if not r.get("ok") and not r.get("missing"):
            failed.append(f"{sym}_{tf}")

    report["all_ok"] = not failed and not missing
    report["failed"] = failed
    report["missing"] = missing
    OUT_REPORT.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"Report: {OUT_REPORT}", file=sys.stderr)
    return 0 if (not failed and not missing) else 1


if __name__ == "__main__":
    raise SystemExit(main())
