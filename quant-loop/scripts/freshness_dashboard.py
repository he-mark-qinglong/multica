#!/usr/bin/env python3
"""Data freshness dashboard for ``quant-loop``.

Implements ``AGENTS.md`` §1 (audit-by-replication) for the *freshness*
question — i.e. for every data file the workspace can prove exists, is
its latest bar still inside the per-timeframe staleness budget?

Distinct from :mod:`scripts.missing_bar_detector`, which answers the
*structural completeness* question (gaps, schema, monotonicity,
symlinks-as-bug). This module is concerned with the operational
question **"is the latest bar new enough to drive a live strategy?"**

Design notes
------------
* The matrix of (symbol × timeframe) cells is built from a live
  ``find`` enumeration, never from a hard-coded directory list. This is
  the §1 rule: hard-coding misses files and produces false negatives.
* Per-cell status is one of:

    =========== ==================================================
    ``fresh``   file present, last bar inside the per-TF budget
    ``stale``   file present, last bar older than the budget
    ``missing`` spec says a file should be here, nothing on disk
    ``symlink`` file is a symlink; ``readlink`` reported, cell
                flagged so the BTCUSDT_4h -> BTCUSD_4h class of bug
                (SMA-34855) cannot recur silently
    ``unknown`` file present but schema unknown; reported, not
                counted as missing
    =========== ==================================================

* Staleness budgets match ``scripts.missing_bar_detector``:

    =========  =============
    1m         5 min
    5m         10 min
    15m        20 min
    30m        35 min
    1h         65 min
    2h         2 h 5 min
    4h         5 h
    1d         26 h
    funding    9 h (8 h cadence + 1 h slack)
    =========  =============

* The script writes a machine-readable JSON snapshot **and** an HTML
  dashboard. The HTML is a single self-contained file (no JS deps,
  inline CSS) so it can be opened directly from ``file://`` or piped
  over HTTP without a build step.
* Exit code mirrors ``missing_bar_detector``'s convention: ``0`` when
  no present file is past the staleness budget and no expected file is
  missing; otherwise ``1``.

Library API
-----------
The CLI is a thin wrapper around three callables:

* :func:`enumerate_data_files` — runs the §1 ``find`` and returns a
  list of :class:`DataFile`.
* :func:`audit_freshness` — turns each :class:`DataFile` into a
  :class:`FreshnessReport`.
* :func:`render_html` — renders the dashboard.

This lets tests stub the filesystem (write synthetic parquet files
under ``tmp_path``) without depending on the real workspace layout.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# ``pandas`` is optional at import time — the discovery and
# filesystem-level audit paths (file_present, is_symlink, mtime) work
# without it. We import lazily inside the row-reading helpers so the
# CLI can still emit a report when pandas is missing (degraded mode
# reports ``unknown`` schema for every file).
try:  # pragma: no cover - exercised by manual ``python -c`` runs
    import pandas as pd  # type: ignore
    _HAS_PANDAS = True
except ImportError:  # pragma: no cover
    pd = None  # type: ignore
    _HAS_PANDAS = False


# ---------------------------------------------------------------------------
# Spec: what the workspace SHOULD have
# ---------------------------------------------------------------------------


# Canonical (symbol, interval, expected-relative-path, schema) tuples.
# The "expected path" is the *primary* canonical location per
# AGENTS.md §2; if a file is found at any other path the row is still
# audited but bucketed as ``strategy_local`` or ``freqtrade_user_data``.
EXPECTED_OHLCV: list[tuple[str, str, str]] = [
    # live_data bucket (shared pool)
    ("BTCUSDT", "15m", "live_data/BTCUSDT_15m.parquet"),
    ("ETHUSDT", "15m", "live_data/ETHUSDT_15m.parquet"),
    ("SOLUSDT", "15m", "live_data/SOLUSDT_15m.parquet"),
    ("BTCUSDT", "1h",  "live_data/BTCUSDT_1h.parquet"),
    ("ETHUSDT", "1h",  "live_data/ETHUSDT_1h.parquet"),
    ("SOLUSDT", "1h",  "live_data/SOLUSDT_1h.parquet"),
    ("BTCUSDT", "4h",  "live_data/BTCUSDT_4h.parquet"),
    ("ETHUSDT", "4h",  "live_data/ETHUSDT_4h.parquet"),
    ("SOLUSDT", "4h",  "live_data/SOLUSDT_4h.parquet"),
    # perp_1m bucket
    ("BTCUSDT", "1m",  "data/perp_1m/BTCUSDT_1m.parquet"),
    ("ETHUSDT", "1m",  "data/perp_1m/ETHUSDT_1m.parquet"),
    # perp_2h bucket (SMA-35009 resampled)
    ("BTCUSDT", "2h",  "data/perp_2h/BTCUSDT_2h.parquet"),
    ("ETHUSDT", "2h",  "data/perp_2h/ETHUSDT_2h.parquet"),
    ("SOLUSDT", "2h",  "data/perp_2h/SOLUSDT_2h.parquet"),
    # perp_30m bucket
    ("BTCUSDT", "30m", "data/perp_30m/BTCUSDT_30m.parquet"),
    ("ETHUSDT", "30m", "data/perp_30m/ETHUSDT_30m.parquet"),
    ("SOLUSDT", "30m", "data/perp_30m/SOLUSDT_30m.parquet"),
    ("AVAXUSDT", "30m", "data/perp_30m/AVAXUSDT_30m.parquet"),
    ("BNBUSDT",  "30m", "data/perp_30m/BNBUSDT_30m.parquet"),
    ("DOGEUSDT", "30m", "data/perp_30m/DOGEUSDT_30m.parquet"),
    ("LINKUSDT", "30m", "data/perp_30m/LINKUSDT_30m.parquet"),
]

# Funding has its own cadence (8 h) and a different schema
# (``fundingTime`` / ``fundingRate``).
EXPECTED_FUNDING: list[tuple[str, str, str]] = [
    (sym, "funding", f"data/funding/{sym}.parquet")
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT",
                "AVAXUSDT", "BNBUSDT", "DOGEUSDT", "LINKUSDT")
]


# ---------------------------------------------------------------------------
# Staleness budgets
# ---------------------------------------------------------------------------


# Per-interval staleness budget in milliseconds. Matches
# ``scripts/missing_bar_detector.FRESHNESS_SLACK_MS``.
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
STALENESS_BUDGET_MS: dict[str, int] = {
    "1m":      5 * 60_000,
    "5m":      10 * 60_000,
    "15m":     20 * 60_000,
    "30m":     35 * 60_000,
    "1h":      65 * 60_000,
    "2h":      2 * 60 * 60_000 + 5 * 60_000,
    "4h":      5 * 60 * 60_000,
    "1d":      26 * 60 * 60_000,
    "funding": 9 * 60 * 60_000,  # 8 h cadence + 1 h slack
}

# Below this size the file is treated as a stray stub, not a real
# series. Matches ``missing_bar_detector.SIZE_FLOOR_BYTES``.
SIZE_FLOOR_BYTES = 1024


# ---------------------------------------------------------------------------
# Filename -> (symbol, interval, bucket) classifier
# ---------------------------------------------------------------------------


# Matches ``BTCUSDT_15m`` (shared pool / freqtrade parquet).
_SHARED_POOL_RE = re.compile(r"^(?P<sym>[A-Z0-9]+)_(?P<tf>1m|5m|15m|30m|1h|2h|4h|1d)$")
# Matches ``BTCUSDT__1m`` (strategy-local snapshot).
_STRATEGY_LOCAL_RE = re.compile(r"^(?P<sym>[A-Z0-9]+)__(?P<tf>1m|5m|15m|30m|1h|2h|4h|1d)$")
# Matches ``fapi_BTCUSDT__1m`` (binance fapi export).
_FAPI_RE = re.compile(r"^fapi_(?P<sym>[A-Z0-9]+)__(?P<tf>1m|5m|15m|30m|1h|2h|4h|1d)$")
# Matches ``BTCUSDT-30m`` (freqtrade feather).
_FREQTRADE_FEATHER_RE = re.compile(r"^(?P<sym>[A-Z0-9]+)-(?P<tf>1m|5m|15m|30m|1h|2h|4h|1d)$")
# Funding files: bare ``BTCUSDT`` / ``AVAXUSDT`` under data/funding/.
_FUNDING_RE = re.compile(r"^(?P<sym>(?:BTC|ETH|SOL|AVAX|BNB|DOGE|LINK)USDT)$")


def classify(path: Path, workspace: Path) -> tuple[Optional[str], Optional[str], str]:
    """Return (symbol, interval, bucket) for a data file.

    ``bucket`` is one of: ``shared_pool``, ``strategy_local``,
    ``freqtrade_user_data``, ``funding``, ``unknown``.

    The classification is purely path-based. A row that resolves to
    ``unknown`` will still be reported in the dashboard (so we never
    silently drop a file) but its freshness status will be ``unknown``.
    """
    rel = path.relative_to(workspace) if path.is_absolute() else path
    parts = rel.parts
    stem = path.stem

    # Funding bucket — ``data/funding/{SYMBOL}.parquet`` (or .csv).
    if len(parts) >= 2 and parts[0] == "data" and parts[1] == "funding":
        m = _FUNDING_RE.match(stem)
        if m:
            return m.group("sym"), "funding", "funding"
        return None, None, "unknown"

    # Strategy-local copies — anything under strategies/*/data/.
    if len(parts) >= 3 and parts[0] == "strategies" and parts[2] == "data":
        for pat in (_FAPI_RE, _STRATEGY_LOCAL_RE):
            m = pat.match(stem)
            if m:
                return m.group("sym"), m.group("tf"), "strategy_local"
        return None, None, "unknown"

    # Freqtrade user data — anything under ``*freqtrade*/user_data/data/``.
    if any("freqtrade" in p for p in parts) \
            and any("user_data" in p for p in parts) \
            and any(p == "data" for p in parts):
        for pat in (_FREQTRADE_FEATHER_RE, _SHARED_POOL_RE):
            m = pat.match(stem)
            if m:
                return m.group("sym"), m.group("tf"), "freqtrade_user_data"
        return None, None, "unknown"

    # Shared pool — live_data/ or data/perp_{tf}/.
    m = _SHARED_POOL_RE.match(stem)
    if m:
        return m.group("sym"), m.group("tf"), "shared_pool"
    return None, None, "unknown"


# ---------------------------------------------------------------------------
# §1 audit-by-replication: the canonical ``find`` command
# ---------------------------------------------------------------------------


# The exact ``find`` invocation per AGENTS.md §1, parameterised by the
# workspace root so callers can point it at any tree. We re-emit this
# command verbatim in the JSON output so future audits can replay it.
# ``.html`` is excluded on top of the AGENTS.md baseline so the
# dashboard's own output never re-enters the audit (otherwise a fresh
# ``--html-out`` would inflate ``files_seen`` by 1).
DEFAULT_FIND_ARGS: list[str] = [
    "-path", "*data*",
    "-type", "f",
    "-not", "-path", "*/__pycache__/*",
    "-not", "-path", "*/.pytest_cache/*",
    "-not", "-name", "*.pyc",
    "-not", "-name", "*.py",
    "-not", "-name", "*.json",
    "-not", "-name", "*.md",
    "-not", "-name", "*.sha256",
    "-not", "-name", "*.ipynb",
    "-not", "-name", "*.sh",
    "-not", "-name", "*.yaml",
    "-not", "-name", "*.yml",
    "-not", "-name", "*.html",
]


def enumerate_data_files(workspace: Path) -> list[Path]:
    """Run the §1 ``find`` and return matching paths under ``workspace``.

    Returns an empty list (not raises) when ``find`` is not on PATH —
    in that case the caller should fall back to :func:`os.walk` if it
    wants *anything*. We prefer surfacing the missing-tool condition
    explicitly so the JSON report makes it visible.
    """
    find_bin = shutil.which("find")
    if find_bin is None:
        return []
    cmd = [find_bin, str(workspace), *DEFAULT_FIND_ARGS]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    paths: list[Path] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        p = Path(line)
        if p.exists():
            paths.append(p)
    paths.sort()
    return paths


def enumerate_data_files_walk(workspace: Path) -> list[Path]:
    """Pure-Python fallback for environments without ``find``.

    Mirrors the §1 filter set so the two paths produce comparable
    results.
    """
    excluded_substr = ("/__pycache__/", "/.pytest_cache/")
    excluded_suffixes = (
        ".pyc", ".py", ".json", ".md", ".sha256", ".ipynb",
        ".sh", ".yaml", ".yml", ".html",
    )
    out: list[Path] = []
    for root, dirs, files in os.walk(workspace):
        # prune noisy cache dirs
        dirs[:] = [d for d in dirs if f"/{d}/" not in excluded_substr]
        for f in files:
            if f.endswith(excluded_suffixes):
                continue
            p = Path(root) / f
            if "data" not in str(p):
                continue
            out.append(p)
    out.sort()
    return out


# ---------------------------------------------------------------------------
# Row readers — extract the latest-bar timestamp from a parquet file
# ---------------------------------------------------------------------------


def _ms(dt: datetime) -> int:
    """Convert an aware datetime to Unix epoch milliseconds."""
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return int(dt.timestamp() * 1000)


def read_latest_open_time_ms(path: Path) -> Optional[int]:
    """Return the latest ``open_time`` (ms) in an OHLCV parquet, or None.

    Reads only the ``open_time`` column to keep cost bounded on large
    files. Returns None when the schema is wrong (caller will report
    ``unknown`` status) or the file is unreadable.
    """
    if not _HAS_PANDAS:
        return None
    try:
        df = pd.read_parquet(path, columns=["open_time"])
    except (KeyError, ValueError):
        try:
            df = pd.read_parquet(path, columns=["date"])
        except (KeyError, ValueError):
            return None
        col = "date"
    else:
        col = "open_time"
    if df.empty:
        return None
    last = df[col].iloc[-1]
    try:
        return int(last)
    except (TypeError, ValueError):
        return None


def read_latest_funding_time_ms(path: Path) -> Optional[int]:
    """Return the latest ``fundingTime`` (ms) in a funding parquet."""
    if not _HAS_PANDAS:
        return None
    for col in ("fundingTime", "funding_time", "time", "open_time"):
        try:
            df = pd.read_parquet(path, columns=[col])
        except (KeyError, ValueError):
            continue
        if df.empty:
            return None
        last = df[col].iloc[-1]
        try:
            return int(last)
        except (TypeError, ValueError):
            continue
    return None


def read_latest_bar_ms(path: Path, interval: str) -> Optional[int]:
    """Dispatch to the right reader based on interval/cadence."""
    if interval == "funding":
        return read_latest_funding_time_ms(path)
    return read_latest_open_time_ms(path)


# ---------------------------------------------------------------------------
# Dataclasses — what the audit returns
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataFile:
    """A single file the §1 ``find`` enumerated."""
    path: Path
    size_bytes: int
    mtime_ms: int
    is_symlink: bool


@dataclass
class FreshnessReport:
    """Audit outcome for one (symbol × interval) cell."""
    symbol: str
    interval: str
    bucket: str
    path: str
    size_bytes: int
    mtime_ms: int
    mtime_iso: str
    is_symlink: bool
    symlink_target: Optional[str]
    status: str  # fresh | stale | missing | symlink | unknown
    last_bar_ms: Optional[int]
    last_bar_iso: Optional[str]
    age_ms: Optional[int]
    budget_ms: Optional[int]
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Audit — turn DataFiles into FreshnessReports
# ---------------------------------------------------------------------------


def _status_from_age(age_ms: int, budget_ms: int) -> str:
    return "fresh" if age_ms <= budget_ms else "stale"


def audit_path(
    path: Path,
    *,
    symbol_hint: Optional[str] = None,
    interval_hint: Optional[str] = None,
    bucket_hint: Optional[str] = None,
    workspace: Path,
    now_ms: Optional[int] = None,
) -> FreshnessReport:
    """Audit a single path. Hints are used for files that aren't on the
    expected list (e.g. strategy-local copies discovered via ``find``).
    """
    now = now_ms if now_ms is not None else _ms(datetime.now(timezone.utc))
    sym, interval, bucket = classify(path, workspace)
    if sym is None:
        sym = symbol_hint or path.stem
    if interval is None:
        interval = interval_hint or "unknown"
    if bucket == "unknown" and bucket_hint:
        bucket = bucket_hint

    # Filesystem-level facts (no parquet read needed).
    try:
        st = path.stat()
    except FileNotFoundError:
        st = None
    is_symlink = path.is_symlink()
    symlink_target: Optional[str] = None
    if is_symlink:
        try:
            symlink_target = os.readlink(path)
        except OSError:
            symlink_target = None

    if st is None:
        return FreshnessReport(
            symbol=sym, interval=interval, bucket=bucket,
            path=str(path), size_bytes=0, mtime_ms=0, mtime_iso="",
            is_symlink=is_symlink, symlink_target=symlink_target,
            status="missing",
            last_bar_ms=None, last_bar_iso=None,
            age_ms=None,
            budget_ms=STALENESS_BUDGET_MS.get(interval),
            note="path does not exist",
        )

    size_bytes = int(st.st_size)
    mtime_ms = int(st.st_mtime_ns // 1_000_000)
    mtime_iso = datetime.fromtimestamp(mtime_ms / 1000, tz=timezone.utc).isoformat()

    # Symlink: report but don't hard-fail (the SMA-34855 BTCUSDT_4h
    # -> BTCUSD_4h bug must stay visible, not crash the run).
    if is_symlink:
        return FreshnessReport(
            symbol=sym, interval=interval, bucket=bucket,
            path=str(path), size_bytes=size_bytes,
            mtime_ms=mtime_ms, mtime_iso=mtime_iso,
            is_symlink=True, symlink_target=symlink_target,
            status="symlink",
            last_bar_ms=None, last_bar_iso=None,
            age_ms=None,
            budget_ms=STALENESS_BUDGET_MS.get(interval),
            note="symlink — verify target before trusting this cell",
        )

    # Too-small stub.
    if size_bytes < SIZE_FLOOR_BYTES:
        return FreshnessReport(
            symbol=sym, interval=interval, bucket=bucket,
            path=str(path), size_bytes=size_bytes,
            mtime_ms=mtime_ms, mtime_iso=mtime_iso,
            is_symlink=False, symlink_target=None,
            status="missing",
            last_bar_ms=None, last_bar_iso=None,
            age_ms=None,
            budget_ms=STALENESS_BUDGET_MS.get(interval),
            note=f"file < {SIZE_FLOOR_BYTES} bytes — stub, not a series",
        )

    # Schema unknown.
    if interval == "unknown":
        return FreshnessReport(
            symbol=sym, interval=interval, bucket=bucket,
            path=str(path), size_bytes=size_bytes,
            mtime_ms=mtime_ms, mtime_iso=mtime_iso,
            is_symlink=False, symlink_target=None,
            status="unknown",
            last_bar_ms=None, last_bar_iso=None,
            age_ms=None,
            budget_ms=None,
            note="could not parse (symbol, interval) from filename",
        )

    budget = STALENESS_BUDGET_MS.get(interval)
    last_bar_ms = read_latest_bar_ms(path, interval)
    if last_bar_ms is None:
        return FreshnessReport(
            symbol=sym, interval=interval, bucket=bucket,
            path=str(path), size_bytes=size_bytes,
            mtime_ms=mtime_ms, mtime_iso=mtime_iso,
            is_symlink=False, symlink_target=None,
            status="unknown",
            last_bar_ms=None, last_bar_iso=None,
            age_ms=None, budget_ms=budget,
            note="file present but open_time/fundingTime column unreadable",
        )

    last_bar_iso = datetime.fromtimestamp(last_bar_ms / 1000, tz=timezone.utc).isoformat()
    age_ms = now - last_bar_ms
    status = _status_from_age(age_ms, budget)
    note = ""
    if status == "stale":
        note = f"age={age_ms // 60_000} min > budget={budget // 60_000} min"
    return FreshnessReport(
        symbol=sym, interval=interval, bucket=bucket,
        path=str(path), size_bytes=size_bytes,
        mtime_ms=mtime_ms, mtime_iso=mtime_iso,
        is_symlink=False, symlink_target=None,
        status=status,
        last_bar_ms=last_bar_ms, last_bar_iso=last_bar_iso,
        age_ms=age_ms, budget_ms=budget, note=note,
    )


def audit_freshness(
    workspace: Path,
    *,
    now_ms: Optional[int] = None,
    files: Optional[Iterable[Path]] = None,
) -> list[FreshnessReport]:
    """Run the audit over the workspace and return per-cell reports.

    The report list contains one entry per *expected* (symbol ×
    interval) plus one entry per *found* file that did not match the
    expected set (so a newly-added strategy-local copy is surfaced
    rather than silently ignored).
    """
    workspace = workspace.resolve()
    now = now_ms if now_ms is not None else _ms(datetime.now(timezone.utc))
    if files is None:
        files = enumerate_data_files(workspace)

    # ---- pass 1: every expected cell ----
    expected: list[tuple[Path, str, str, str]] = []
    for sym, interval, rel in EXPECTED_OHLCV + EXPECTED_FUNDING:
        expected.append((workspace / rel, sym, interval,
                         "shared_pool" if interval != "funding" else "funding"))

    reports: list[FreshnessReport] = []
    seen_paths: set[Path] = set()
    for path, sym, interval, bucket in expected:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        reports.append(audit_path(
            path,
            symbol_hint=sym, interval_hint=interval, bucket_hint=bucket,
            workspace=workspace, now_ms=now,
        ))

    # ---- pass 2: anything the ``find`` saw that we didn't expect ----
    for path in files:
        path = path.resolve()
        if path in seen_paths:
            continue
        seen_paths.add(path)
        r = audit_path(path, workspace=workspace, now_ms=now)
        if r.status == "unknown" and r.note.startswith("could not parse"):
            # Drop files that aren't even classifiable — they don't
            # belong on a freshness dashboard, and emitting them would
            # dilute the matrix.
            continue
        reports.append(r)

    reports.sort(key=lambda r: (r.symbol, _interval_sort_key(r.interval), r.bucket, r.path))
    return reports


def _interval_sort_key(interval: str) -> tuple[int, str]:
    """Stable sort key: numeric timeframes first by ms, then 'funding', then 'unknown'."""
    if interval in BAR_MS:
        return (0, f"{BAR_MS[interval]:016d}")
    if interval == "funding":
        return (1, interval)
    return (2, interval)


# ---------------------------------------------------------------------------
# Rollup: per-(symbol × interval) verdict
# ---------------------------------------------------------------------------


def rollup(reports: list[FreshnessReport]) -> dict:
    """Aggregate counts and per-status breakdowns."""
    by_status: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    by_interval: dict[str, dict[str, int]] = {}
    for r in reports:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        by_bucket[r.bucket] = by_bucket.get(r.bucket, 0) + 1
        slot = by_interval.setdefault(r.interval, {})
        slot[r.status] = slot.get(r.status, 0) + 1
    return {
        "total": len(reports),
        "by_status": by_status,
        "by_bucket": by_bucket,
        "by_interval": by_interval,
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


_CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
       margin: 24px; line-height: 1.45; }
h1 { margin-bottom: 4px; }
h2 { margin-top: 28px; }
table { border-collapse: collapse; margin: 8px 0 24px 0; }
th, td { padding: 6px 10px; border: 1px solid #ccc; text-align: left;
         font-size: 14px; }
th { background: #f3f4f6; }
.status-fresh   { background: #d1fae5; }
.status-stale   { background: #fee2e2; font-weight: 600; }
.status-missing { background: #fde68a; font-weight: 600; }
.status-symlink { background: #fef3c7; }
.status-unknown { background: #e5e7eb; }
.summary { display: flex; gap: 12px; flex-wrap: wrap; }
.chip { padding: 6px 12px; border-radius: 999px; font-weight: 600;
        border: 1px solid #ccc; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 12px; }
.footer { color: #6b7280; font-size: 12px; margin-top: 32px; }
"""


def render_html(reports: list[FreshnessReport], rollup_dict: dict,
                *, generated_at: datetime) -> str:
    """Render a self-contained HTML dashboard. No JS dependencies."""
    rows_html = []
    for r in reports:
        age = "—"
        if r.age_ms is not None:
            age = _fmt_age(r.age_ms)
        budget = "—"
        if r.budget_ms is not None:
            budget = _fmt_age(r.budget_ms)
        symlink_target = html.escape(r.symlink_target or "")
        rows_html.append(
            f'<tr class="status-{r.status}">'
            f'<td class="mono">{html.escape(r.symbol)}</td>'
            f'<td class="mono">{html.escape(r.interval)}</td>'
            f'<td>{html.escape(r.bucket)}</td>'
            f'<td class="mono" title="{html.escape(r.path)}">{html.escape(_short_path(r.path))}</td>'
            f'<td>{html.escape(r.status)}</td>'
            f'<td class="mono">{html.escape(r.last_bar_iso or "—")}</td>'
            f'<td class="mono">{age}</td>'
            f'<td class="mono">{budget}</td>'
            f'<td class="mono">{html.escape(symlink_target)}</td>'
            f'<td>{html.escape(r.note)}</td>'
            '</tr>'
        )

    chips = []
    for status, count in sorted(rollup_dict["by_status"].items()):
        chips.append(f'<span class="chip status-{status}">{status}: {count}</span>')
    chips_html = "".join(chips)

    bucket_chips = " ".join(
        f'<span class="chip">{b}: {c}</span>'
        for b, c in sorted(rollup_dict["by_bucket"].items())
    )

    find_cmd = "find " + " ".join(DEFAULT_FIND_ARGS)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>quant-loop freshness dashboard</title>
<style>{_CSS}</style>
</head>
<body>
<h1>quant-loop freshness dashboard</h1>
<p class="footer">Generated {html.escape(generated_at.isoformat())} — {rollup_dict["total"]} cells audited</p>

<h2>Summary</h2>
<div class="summary">{chips_html}</div>
<p class="footer">By bucket: {bucket_chips}</p>

<h2>Per-interval breakdown</h2>
<table>
<thead><tr><th>interval</th><th>fresh</th><th>stale</th><th>missing</th><th>symlink</th><th>unknown</th></tr></thead>
<tbody>
{_render_interval_rows(rollup_dict["by_interval"])}
</tbody>
</table>

<h2>Per-cell freshness</h2>
<table>
<thead><tr>
<th>symbol</th><th>interval</th><th>bucket</th><th>path</th><th>status</th>
<th>last_bar (UTC)</th><th>age</th><th>budget</th><th>symlink target</th><th>note</th>
</tr></thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>

<h2>Audit-by-replication</h2>
<p class="footer">The cell matrix above is built from this <code>find</code> invocation per
AGENTS.md §1 (audit-by-replication rule):</p>
<pre class="mono">{html.escape(find_cmd)}</pre>
<p class="footer">Re-running the same command on a different day must produce the same answer.
Discrepancies in cell count mean data was added, removed, or the workspace moved —
stop and report before drawing conclusions.</p>
</body>
</html>
"""


def _render_interval_rows(by_interval: dict) -> str:
    out = []
    for interval in sorted(by_interval.keys(), key=_interval_sort_key):
        slot = by_interval[interval]
        out.append(
            f"<tr><td class='mono'>{html.escape(interval)}</td>"
            f"<td>{slot.get('fresh', 0)}</td>"
            f"<td>{slot.get('stale', 0)}</td>"
            f"<td>{slot.get('missing', 0)}</td>"
            f"<td>{slot.get('symlink', 0)}</td>"
            f"<td>{slot.get('unknown', 0)}</td></tr>"
        )
    return "\n".join(out)


def _fmt_age(ms: int) -> str:
    """Render an age in ms as a compact human string.

    Stable, locale-free format:

      * < 1 min        -> ``45s``
      * < 1 day        -> ``3m``, ``2h5m``
      * >= 1 day       -> ``1d2h`` (no trailing minute component — keeps
                          the field width bounded for 4-week staleness
                          reports)
    """
    seconds = ms // 1000
    if seconds < 0:
        return f"{seconds}s"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h{minutes % 60}m"
    days = hours // 24
    return f"{days}d{hours % 24}h"


def _short_path(path: str) -> str:
    """Show at most the trailing ``workspace/live_data/...`` portion."""
    p = Path(path)
    parts = p.parts
    if "live_data" in parts:
        i = parts.index("live_data")
        return ".../" + "/".join(parts[i:])
    if "data" in parts:
        i = parts.index("data")
        return ".../" + "/".join(parts[i:])
    return p.name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="freshness_dashboard",
        description="Quant-loop data freshness dashboard (audit-by-replication)",
    )
    p.add_argument(
        "--workspace", type=Path, default=Path.cwd(),
        help="Path to the quant-loop workspace root (default: cwd)",
    )
    p.add_argument(
        "--json-out", type=Path, default=None,
        help="Write the JSON snapshot here (default: stdout)",
    )
    p.add_argument(
        "--html-out", type=Path, default=None,
        help="Write the HTML dashboard here (default: not written)",
    )
    p.add_argument(
        "--now-ms", type=int, default=None,
        help="Override the 'now' reference time (Unix ms, for tests)",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress human-readable summary on stderr",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        print(f"workspace not found: {workspace}", file=sys.stderr)
        return 2

    files = enumerate_data_files(workspace)
    if not files:
        # ``find`` missing — fall back to the pure-Python walker so
        # the dashboard is still useful in minimal environments.
        files = enumerate_data_files_walk(workspace)

    reports = audit_freshness(workspace, now_ms=args.now_ms, files=files)
    summary = rollup(reports)
    generated_at = datetime.now(timezone.utc)

    snapshot = {
        "generated_at": generated_at.isoformat(),
        "workspace": str(workspace),
        "find_command": ["find", str(workspace), *DEFAULT_FIND_ARGS],
        "files_seen": len(files),
        "summary": summary,
        "reports": [r.to_dict() for r in reports],
    }

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        json.dump(snapshot, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")

    if args.html_out is not None:
        args.html_out.parent.mkdir(parents=True, exist_ok=True)
        args.html_out.write_text(render_html(reports, summary,
                                             generated_at=generated_at))

    if not args.quiet:
        s = summary
        print(
            f"\nfreshness_dashboard: total={s['total']} "
            f"fresh={s['by_status'].get('fresh', 0)} "
            f"stale={s['by_status'].get('stale', 0)} "
            f"missing={s['by_status'].get('missing', 0)} "
            f"symlink={s['by_status'].get('symlink', 0)} "
            f"unknown={s['by_status'].get('unknown', 0)}",
            file=sys.stderr,
        )

    # Exit non-zero iff any expected cell is past its budget (stale)
    # or absent (missing). Symlinks and unknown are *reported* but not
    # hard-failed — the BTCUSDT_4h symlink is a known case.
    hard_fail = (
        summary["by_status"].get("stale", 0) > 0
        or summary["by_status"].get("missing", 0) > 0
    )
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())