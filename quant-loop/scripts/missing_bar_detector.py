#!/usr/bin/env python3
"""Missing-bar detector for quant-loop OHLCV parquet series.

Generalised, library-first port of ``live_data/verify_sma34898.py``. The CLI
emits a JSON report; the library exposes :func:`detect_series`,
:func:`detect_path`, :func:`find_canonical_specs`, and :func:`run_grid` so
other validators (``live_data/verify_sma34898.py``, ``live_data/verify_sma34872.py``,
``live_data/verify_usdt_15m.py``, the ``oos_harness``) can reuse the same
math without copy-pasting the logic.

For each (symbol x timeframe) series this module reports:

  * file presence (with symlink / size-floor checks)
  * schema validity (must contain ``open_time`` + OHLCV)
  * timestamp monotonicity on the full file
  * boundary alignment (``open_time`` lands on the timeframe grid)
  * missing-bar count inside a sliding window ``[now - window, now]``
  * missing-bar count across the full file (sampled if huge)
  * trailing-edge staleness (last bar older than expected by > bar interval)
  * up to N largest gap windows with from/to ISO timestamps + missing-bar count

Canonical bucket locations per ``quant-loop/AGENTS.md`` §2:

  * shared pool — perp OHLCV  ``data/perp_{1m,2h,30m}/`` + ``live_data/``
  * shared pool — funding      ``data/funding/`` (skipped, different schema)
  * freqtrade user_data        ``freqtrade_v10/user_data/data/``
  * strategy-local copies      ``strategies/*/data/`` (counted, never merged)

The detector is intentionally conservative:

  * trailing-edge staleness is *reported* but never *hard-failed* (data
    refresh is a separate operational concern from structural completeness)
  * internal gaps (missing bars with neighbours present on both sides)
    are always hard-failed
  * symlinks are flagged but not hard-failed (the SMA-34855 BTCUSD/4h
    symlink-to-BTCUSD bug is exposed by the report rather than crashed)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Timeframe constants
# ---------------------------------------------------------------------------

BAR_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}
BARS_PER_DAY: dict[str, int] = {tf: 86_400_000 // ms for tf, ms in BAR_MS.items()}
# Freshness slack: how stale the latest bar is allowed to be (ms). 1m bars
# are produced continuously; longer bars align to wall-clock boundaries.
FRESHNESS_SLACK_MS: dict[str, int] = {
    "1m": 5 * 60_000,
    "5m": 10 * 60_000,
    "15m": 20 * 60_000,
    "30m": 35 * 60_000,
    "1h": 65 * 60_000,
    "2h": 2 * 60 * 60_000 + 5 * 60_000,
    "4h": 5 * 60 * 60_000,
    "1d": 26 * 60 * 60_000,
}
# Below this many bytes the file is treated as too-small (not a real series).
SIZE_FLOOR_BYTES = 1024
# Cap on "largest gaps" entries to keep reports bounded.
TOP_N_GAPS = 5
# Full-file gap audit sampling stride: audit every Nth timestamp to bound
# cost on huge files. 1 means "audit every timestamp".
FULL_FILE_SAMPLE_STRIDE = 1

REQUIRED_COLS = {"open", "high", "low", "close", "volume"}
# Time column candidates — first match wins. ``open_time`` is the Binance
# canonical; ``date`` is the alpaca/yfinance convention used by most
# strategy-local snapshots and the freqtrade-feather exports.
TIMESTAMP_COLS = ("open_time", "date")


# ---------------------------------------------------------------------------
# SeriesSpec — what to detect
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeriesSpec:
    """A single (symbol, interval, path) tuple to audit."""
    symbol: str
    interval: str
    path: Path
    bucket: str  # "shared_pool" | "freqtrade_user_data" | "strategy_local"

    @property
    def key(self) -> str:
        """Unique-per-file key used as the report dict key.

        Includes the path so duplicate (symbol, interval) under different
        strategies (very common for strategy-local copies) stay distinct
        in the report.
        """
        return f"{self.symbol}_{self.interval}@{self.bucket}::{self.path}"

    @property
    def short_key(self) -> str:
        """Compact key used in rollup lists (no path)."""
        return f"{self.symbol}_{self.interval}@{self.bucket}"


# ---------------------------------------------------------------------------
# SeriesReport — what detection returned
# ---------------------------------------------------------------------------


@dataclass
class SeriesReport:
    symbol: str
    interval: str
    bucket: str
    path: str
    size_bytes: int = 0
    is_symlink: bool = False
    rows_total: int = 0
    schema_ok: bool = False
    schema_missing: list[str] = field(default_factory=list)
    nan_close_total: Optional[int] = None
    ts_monotonic: bool = True
    boundary_misalign_count: int = 0
    # window stats
    window_start_ms: int = 0
    window_end_ms: int = 0
    rows_in_window: int = 0
    expected_rows_in_window: int = 0
    row_count_ok: bool = False
    missing_bars_in_window: int = 0
    max_gap_bars_in_window: int = 0
    gap_count_in_window: int = 0
    largest_gaps_in_window: list[dict] = field(default_factory=list)
    # full-file stats
    full_file_missing_bars: int = 0
    full_file_max_gap_bars: int = 0
    full_file_largest_gaps: list[dict] = field(default_factory=list)
    full_file_audit_sampled: bool = False
    # staleness
    first_open_time_ms: int = 0
    last_open_time_ms: int = 0
    first_open_time_iso: str = ""
    last_open_time_iso: str = ""
    staleness_ms: int = 0
    trailing_short_bars: int = 0
    trailing_edge_short: bool = False
    # verdicts
    missing: bool = False
    too_small: bool = False
    internal_gap: bool = False
    ok: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Path discovery — implements AGENTS.md §2
# ---------------------------------------------------------------------------


_FREQTRADE_FEATHER_RE = re.compile(r"^(?P<sym>[A-Z0-9]+)-(?P<tf>[0-9]+[mhd])$")
_FREQTRADE_PARQUET_RE = re.compile(r"^(?P<sym>[A-Z0-9]+)_(?P<tf>[0-9]+[mhd])$")
_SHARED_POOL_RE = re.compile(r"^(?P<sym>[A-Z0-9]+)_(?P<tf>[0-9]+[mhd])$")
_FAPI_RE = re.compile(r"^fapi_(?P<sym>[A-Z0-9]+)__(?P<tf>[0-9]+[mhd])$")
_DOUBLE_UNDERSCORE_RE = re.compile(r"^(?P<sym>[A-Z0-9]+)__(?P<tf>[0-9]+[mhd])$")


def _parse_symbol_interval(stem: str) -> Optional[tuple[str, str]]:
    """Best-effort (symbol, interval) extraction from a filename stem.

    Handles the four canonical filename shapes we see in the wild:

      * ``BTCUSDT_15m``         (shared pool, freqtrade parquet)
      * ``BTCUSDT-30m``         (freqtrade feather)
      * ``BTCUSDT__1m``         (strategy-local copy)
      * ``fapi_BTCUSDT__1m``    (strategy-local fapi binance)

    Returns None if the stem does not look like an OHLCV filename.
    """
    for pat in (_FAPI_RE, _DOUBLE_UNDERSCORE_RE, _FREQTRADE_FEATHER_RE,
                _FREQTRADE_PARQUET_RE, _SHARED_POOL_RE):
        m = pat.match(stem)
        if m:
            return m.group("sym"), m.group("tf")
    return None


def find_canonical_specs(quant_loop_root: Path) -> list[SeriesSpec]:
    """Walk ``quant-loop`` and emit one :class:`SeriesSpec` per OHLCV file.

    Honours the audit-by-replication rule in ``AGENTS.md`` §1: we enumerate
    every parquet/feather under the canonical bucket locations rather than
    relying on a hard-coded file list.

    The four buckets are walked independently so the caller can report each
    bucket separately (see ``SeriesReport.bucket``). Missing bucket roots
    are silently skipped — that is the canonical failure mode for an
    audit (no 1m data -> ``missing=True`` per-file).
    """
    root = Path(quant_loop_root).resolve()
    specs: list[SeriesSpec] = []

    # --- shared pool — perp OHLCV ---
    # data/perp_1m/, data/perp_2h/, data/perp_30m/, live_data/
    shared_dirs = [
        root / "data" / "perp_1m",
        root / "data" / "perp_2h",
        root / "data" / "perp_30m",
        root / "live_data",
    ]
    for d in shared_dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.parquet")):
            parsed = _parse_symbol_interval(p.stem)
            if parsed is None:
                continue
            sym, tf = parsed
            if tf not in BAR_MS:
                continue
            specs.append(SeriesSpec(sym, tf, p, "shared_pool"))

    # --- freqtrade user_data ---
    ft_dir = root / "freqtrade_v10" / "user_data" / "data"
    if ft_dir.is_dir():
        for p in sorted(list(ft_dir.glob("*.parquet")) + list(ft_dir.glob("*.feather"))):
            parsed = _parse_symbol_interval(p.stem)
            if parsed is None:
                continue
            sym, tf = parsed
            if tf not in BAR_MS:
                continue
            specs.append(SeriesSpec(sym, tf, p, "freqtrade_user_data"))

    # --- strategy-local copies (counted, never merged with shared pool) ---
    strat_dir = root / "strategies"
    if strat_dir.is_dir():
        for data_dir in sorted(strat_dir.glob("*/data")):
            if not data_dir.is_dir():
                continue
            for p in sorted(list(data_dir.glob("*.parquet"))
                            + list(data_dir.glob("*.feather"))):
                parsed = _parse_symbol_interval(p.stem)
                if parsed is None:
                    continue
                sym, tf = parsed
                if tf not in BAR_MS:
                    continue
                specs.append(SeriesSpec(sym, tf, p, "strategy_local"))

    return specs


# ---------------------------------------------------------------------------
# Core detector
# ---------------------------------------------------------------------------


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _count_gaps(
    ts: pd.Series, bar_ms: int, top_n: int = TOP_N_GAPS,
) -> tuple[int, int, int, list[dict]]:
    """Return (missing_bars, max_gap_bars, gap_count, top_n_gap_records).

    ``ts`` must be sorted ascending and de-duplicated. Missing bars = (gap //
    bar_ms) - 1 for every adjacent pair whose delta > bar_ms. Returns
    zeros / empty list for ``len(ts) < 2``.
    """
    if len(ts) < 2:
        return 0, 0, 0, []
    diff = ts.diff().dropna()
    if diff.empty:
        return 0, 0, 0, []
    # Full-length missing count: 0 for normal gaps, >0 for under-filled gaps.
    missing_full = (diff // bar_ms - 1).clip(lower=0).astype("int64")
    if (missing_full > 0).sum() == 0:
        return 0, 0, 0, []
    missing_total = int(missing_full.sum())
    max_gap = int(missing_full.max())
    gap_count = int((missing_full > 0).sum())
    # top-N — work on the full-length frame so column assignment lines up.
    tmp = diff.to_frame("dt").reset_index(drop=True)
    tmp["missing_bars"] = missing_full.values
    tmp["from_ts"] = ts.iloc[:-1].values
    tmp["to_ts"] = ts.iloc[1:].values
    tmp = (tmp[tmp["missing_bars"] > 0]
           .sort_values("missing_bars", ascending=False)
           .head(top_n)
           .reset_index(drop=True))
    records = [
        {
            "from_iso": _iso(int(r["from_ts"])),
            "to_iso": _iso(int(r["to_ts"])),
            "missing_bars": int(r["missing_bars"]),
        }
        for _, r in tmp.iterrows()
    ]
    return missing_total, max_gap, gap_count, records


def detect_series(
    spec: SeriesSpec,
    now_ms: Optional[int] = None,
    window_ms: int = 7 * 24 * 60 * 60_000,
    full_file_stride: int = FULL_FILE_SAMPLE_STRIDE,
) -> SeriesReport:
    """Run the missing-bar detector on a single :class:`SeriesSpec`.

    Returns a fully-populated :class:`SeriesReport`. Never raises on
    missing/empty/malformed files; instead ``report.missing`` /
    ``report.too_small`` / ``report.error`` carry the diagnostic and
    ``report.ok`` reflects whether the series passes the hard checks.
    """
    r = SeriesReport(
        symbol=spec.symbol,
        interval=spec.interval,
        bucket=spec.bucket,
        path=str(spec.path),
    )
    if now_ms is None:
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    r.window_end_ms = now_ms
    r.window_start_ms = now_ms - window_ms

    if spec.interval not in BAR_MS:
        r.error = f"unknown interval {spec.interval!r}"
        return r

    bar_ms = BAR_MS[spec.interval]
    expected_in_window = (window_ms // bar_ms)
    r.expected_rows_in_window = expected_in_window

    p = Path(spec.path)
    if not p.exists():
        r.missing = True
        return r
    try:
        size = p.stat().st_size
    except OSError as e:
        r.error = f"stat failed: {e}"
        return r
    r.size_bytes = size
    r.is_symlink = p.is_symlink()
    if size <= SIZE_FLOOR_BYTES:
        r.too_small = True
        return r

    try:
        if p.suffix.lower() == ".feather":
            df = pd.read_feather(p)
        else:
            df = pd.read_parquet(p)
    except Exception as e:  # noqa: BLE001 — surface the raw error to the report
        r.error = f"read failed: {type(e).__name__}: {e}"
        return r
    if df.empty:
        r.rows_total = 0
        r.schema_ok = False
        r.schema_missing = sorted(REQUIRED_COLS | {TIMESTAMP_COLS[0]})
        return r

    cols = set(df.columns.tolist())
    # Pick the timestamp column: prefer ``open_time`` (Binance), fall back to
    # ``date`` (alpaca / freqtrade-feather). Track which one was used so the
    # report shows what we actually audited against.
    ts_col: Optional[str] = None
    for cand in TIMESTAMP_COLS:
        if cand in cols:
            ts_col = cand
            break
    schema_missing = sorted((REQUIRED_COLS - cols) |
                            ({TIMESTAMP_COLS[0]} - cols if ts_col is None else set()))
    r.schema_missing = schema_missing
    r.schema_ok = not schema_missing
    r.rows_total = int(len(df))

    if "close" in cols:
        r.nan_close_total = int(df["close"].isna().sum())

    if ts_col is None:
        # Schema is bad — return early; downstream checks would KeyError.
        return r

    # Normalise the timestamp series to int64 ms. Datetime columns can come
    # in ns / us / ms resolution depending on the source — pandas picks the
    # tightest fit automatically, so we read the dtype's ``unit`` attr and
    # divide accordingly. Pure numeric columns are assumed already-ms.
    raw_ts = df[ts_col]
    if pd.api.types.is_datetime64_any_dtype(raw_ts):
        unit = pd.api.types.infer_dtype(raw_ts, skipna=False)
        # ``datetime64`` dtype exposes the unit via ``.unit`` attribute on
        # the dtype object itself.
        try:
            dt_unit = raw_ts.dtype.unit  # 'ns' | 'us' | 'ms' | 's'
        except AttributeError:
            dt_unit = "ns"
        ts_full = raw_ts.astype("int64")
        if dt_unit == "ns":
            ts_full = ts_full // 1_000_000
        elif dt_unit == "us":
            ts_full = ts_full // 1_000
        # 'ms' and 's' are already correct (s resolution is rare in finance).
        if dt_unit == "s":
            ts_full = ts_full * 1_000
    else:
        ts_full = raw_ts.astype("int64")
    if len(ts_full) > 1:
        diff = ts_full.diff().dropna()
        r.ts_monotonic = bool((diff >= 0).all())

    # Boundary alignment: open_time mod bar_ms should be 0.
    if r.schema_ok and len(ts_full):
        r.boundary_misalign_count = int((ts_full % bar_ms).ne(0).sum())

    r.first_open_time_ms = int(ts_full.iloc[0])
    r.last_open_time_ms = int(ts_full.iloc[-1])
    r.first_open_time_iso = _iso(r.first_open_time_ms)
    r.last_open_time_iso = _iso(r.last_open_time_ms)
    r.staleness_ms = int(now_ms - r.last_open_time_ms)
    trailing_short_bars = max(0, r.staleness_ms // bar_ms)
    r.trailing_short_bars = int(trailing_short_bars)
    r.trailing_edge_short = trailing_short_bars > 0

    # Window slice (sorted + de-duplicated). Use the normalised int64 ms
    # series we built earlier (ts_full) instead of re-reading the raw
    # column — that way alpaca `date` and Binance `open_time` both work.
    win_mask = ts_full >= r.window_start_ms
    win = ts_full.loc[win_mask].reset_index(drop=True)
    win = win.sort_values().drop_duplicates().reset_index(drop=True)
    r.rows_in_window = int(len(win))
    r.row_count_ok = r.rows_in_window >= int(expected_in_window * 0.98)
    miss_w, max_w, gc_w, top_w = _count_gaps(win, bar_ms, top_n=TOP_N_GAPS)
    r.missing_bars_in_window = miss_w
    r.max_gap_bars_in_window = max_w
    r.gap_count_in_window = gc_w
    r.largest_gaps_in_window = top_w

    # Full-file slice (sample if huge).
    full_sorted = ts_full.sort_values().drop_duplicates().reset_index(drop=True)
    if full_file_stride > 1 and len(full_sorted) > 10_000:
        full_audit = full_sorted.iloc[::full_file_stride].reset_index(drop=True)
        r.full_file_audit_sampled = True
    else:
        full_audit = full_sorted
    miss_f, max_f, _gc_f, top_f = _count_gaps(full_audit, bar_ms, top_n=TOP_N_GAPS)
    # Scale sampled missing counts back up so the report reflects the full
    # file (a stride of N captures 1/N of the bars but every adjacent pair
    # is preserved, so total missing scales linearly).
    if r.full_file_audit_sampled and full_file_stride > 1:
        miss_f *= full_file_stride
        if top_f:
            # We can only report the sampled largest gaps verbatim — note
            # in the report so callers know these are sampled views.
            for g in top_f:
                g["sampled"] = True
    r.full_file_missing_bars = miss_f
    r.full_file_max_gap_bars = max_f
    r.full_file_largest_gaps = top_f

    # Verdicts
    r.internal_gap = (r.missing_bars_in_window > 0)
    hard_fail = (
        not r.schema_ok
        or r.is_symlink
        or r.rows_in_window == 0
        or not r.ts_monotonic
        or r.internal_gap
        or (r.nan_close_total is not None and r.nan_close_total != 0)
    )
    r.ok = not hard_fail
    return r


def detect_path(
    path: Path,
    symbol: str,
    interval: str,
    bucket: str = "ad_hoc",
    **kwargs: Any,
) -> SeriesReport:
    """Convenience wrapper for ad-hoc paths (e.g. unit tests)."""
    spec = SeriesSpec(symbol=symbol, interval=interval,
                      path=Path(path), bucket=bucket)
    return detect_series(spec, **kwargs)


def run_grid(
    specs: Iterable[SeriesSpec],
    now_ms: Optional[int] = None,
    window_ms: int = 7 * 24 * 60 * 60_000,
    full_file_stride: int = FULL_FILE_SAMPLE_STRIDE,
) -> list[SeriesReport]:
    """Run :func:`detect_series` across an iterable of specs."""
    return [detect_series(s, now_ms=now_ms, window_ms=window_ms,
                          full_file_stride=full_file_stride) for s in specs]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_human(report: SeriesReport) -> str:
    status = "OK" if report.ok else "FAIL"
    flags: list[str] = []
    if report.missing:
        flags.append("missing")
    if report.is_symlink:
        flags.append("symlink")
    if report.too_small:
        flags.append("too_small")
    if not report.schema_ok:
        flags.append("schema_bad")
    if report.nan_close_total:
        flags.append("nan_close")
    if not report.ts_monotonic:
        flags.append("ts_not_monotonic")
    if report.boundary_misalign_count:
        flags.append("boundary_misalign")
    if report.internal_gap:
        flags.append("internal_gap")
    if report.trailing_edge_short:
        flags.append("trailing_stale")
    if report.rows_in_window == 0 and not report.missing:
        flags.append("empty_window")
    flag_str = " ".join(flags) if flags else "-"
    return (f"  {status:>4} [{report.bucket}] {report.symbol}_{report.interval}: "
            f"rows_win={report.rows_in_window}/{report.expected_rows_in_window} "
            f"last={report.last_open_time_iso or '-'} "
            f"stale={report.staleness_ms}ms "
            f"miss_win={report.missing_bars_in_window} "
            f"miss_full={report.full_file_missing_bars} "
            f"[{flag_str}]")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Missing-bar detector for quant-loop OHLCV parquet series.",
    )
    p.add_argument("--root", type=Path,
                   default=Path(os.environ.get("QUANT_LOOP_ROOT",
                                               "/home/smark/multica/quant-loop")),
                   help="quant-loop root directory (default: $QUANT_LOOP_ROOT or "
                        "/home/smark/multica/quant-loop).")
    p.add_argument("--window-days", type=int, default=7,
                   help="Sliding window size in days (default: 7).")
    p.add_argument("--buckets", default="shared_pool,freqtrade_user_data,strategy_local",
                   help="Comma-separated buckets to scan (default: all).")
    p.add_argument("--symbols", default="",
                   help="Comma-separated symbols to restrict to (default: all).")
    p.add_argument("--intervals", default="",
                   help="Comma-separated intervals to restrict to (default: all).")
    p.add_argument("--out", type=Path, default=None,
                   help="Output JSON report path (default: live_data/"
                        "missing_bar_report_<UTC-timestamp>.json).")
    p.add_argument("--full-file-stride", type=int, default=FULL_FILE_SAMPLE_STRIDE,
                   help="Sample every Nth timestamp for the full-file gap audit "
                        "(default: 1, no sampling).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-series stderr summary.")
    args = p.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: quant-loop root not found: {root}", file=sys.stderr)
        return 2

    specs = find_canonical_specs(root)
    allowed_buckets = set(args.buckets.split(",")) if args.buckets else None
    sym_filter = set(args.symbols.split(",")) if args.symbols else None
    tf_filter = set(args.intervals.split(",")) if args.intervals else None
    specs = [s for s in specs
             if (allowed_buckets is None or s.bucket in allowed_buckets)
             and (sym_filter is None or s.symbol in sym_filter)
             and (tf_filter is None or s.interval in tf_filter)]

    window_ms = args.window_days * 24 * 60 * 60_000
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    reports = run_grid(specs, now_ms=now_ms, window_ms=window_ms,
                       full_file_stride=args.full_file_stride)

    # Per-bucket / overall rollup.
    rollup: dict[str, dict[str, int]] = {}
    failed: list[str] = []
    missing: list[str] = []
    for rep, spec in zip(reports, specs):
        b = rep.bucket
        rollup.setdefault(b, {"files": 0, "ok": 0, "failed": 0,
                              "missing": 0, "internal_gaps": 0,
                              "missing_bars_window": 0,
                              "missing_bars_full": 0})
        rollup[b]["files"] += 1
        if rep.missing:
            rollup[b]["missing"] += 1
            missing.append(spec.key)
            continue
        if rep.ok:
            rollup[b]["ok"] += 1
        else:
            rollup[b]["failed"] += 1
            failed.append(spec.key)
        if rep.internal_gap:
            rollup[b]["internal_gaps"] += 1
        rollup[b]["missing_bars_window"] += rep.missing_bars_in_window
        rollup[b]["missing_bars_full"] += rep.full_file_missing_bars

    out_path = args.out
    if out_path is None:
        live_data_dir = root / "live_data"
        live_data_dir.mkdir(exist_ok=True)
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = live_data_dir / f"missing_bar_report_{stamp}.json"

    report_obj = {
        "wall_clock_iso": _iso(now_ms),
        "window_start_iso": _iso(now_ms - window_ms),
        "window_days": args.window_days,
        "full_file_stride": args.full_file_stride,
        "root": str(root),
        "rollup": rollup,
        "totals": {
            "files": len(reports),
            "failed": len(failed),
            "missing": len(missing),
            "all_ok": not failed and not missing,
        },
        "failed": failed,
        "missing": missing,
        "bar_ms": BAR_MS,
        "bars_per_day": BARS_PER_DAY,
        "freshness_slack_ms": FRESHNESS_SLACK_MS,
        "files": {spec.key: asdict(rep)
                  for spec, rep in zip(specs, reports)},
    }
    out_path.write_text(json.dumps(report_obj, indent=2, sort_keys=True))

    if not args.quiet:
        print(f"Missing-bar detector report", file=sys.stderr)
        print(f"  root:           {root}", file=sys.stderr)
        print(f"  window:         {args.window_days} day(s)", file=sys.stderr)
        print(f"  full-file stride: {args.full_file_stride}", file=sys.stderr)
        print(f"  files scanned:  {len(reports)}", file=sys.stderr)
        for b, agg in sorted(rollup.items()):
            print(f"  bucket[{b}]: files={agg['files']} ok={agg['ok']} "
                  f"failed={agg['failed']} missing={agg['missing']} "
                  f"internal_gaps={agg['internal_gaps']} "
                  f"miss_win={agg['missing_bars_window']} "
                  f"miss_full={agg['missing_bars_full']}",
                  file=sys.stderr)
        for rep in reports:
            print(_format_human(rep), file=sys.stderr)
        print(f"Report: {out_path}", file=sys.stderr)
    print(str(out_path))
    return 0 if (not failed and not missing) else 1


if __name__ == "__main__":
    raise SystemExit(main())