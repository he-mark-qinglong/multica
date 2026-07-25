"""Atomic, idempotent, per-date ledger writer for paper-trading results.

T8 (w5-s3, infra-sprint 2026-07-25). Replaces the buggy ``open("a")`` writer
in the graveyard ``paper_runner.py`` which suffered from two classes of
defects:

  1. **Header/data collision** — header was written by ``_init_ledger_headers``
     via ``write_text`` while rows were appended by ``_append_daily_metrics``
     via ``open("a")``.  On the first run after the header existed on disk
     the append path skipped the header and the *next* append then glued a
     new header in front of the data row, producing files like
     ``kill_reason,notes2026-07-20,13,...``.

  2. **Same-date duplicates** — pure append with no date key meant a retry
     of the same trading day produced two rows for one calendar date.

The replacement is a single read-modify-replace cycle that keeps a tidy,
sorted, deduped ledger even under crashes (the ``.csv.tmp`` neighbour is
the only artifact left on disk if ``os.replace`` is interrupted).

Public API
----------

* :data:`DAILY_FIELDS` — canonical column order, byte-for-byte equal to
  the header string in the legacy ``paper_runner.py:117-124``.
* :func:`append_daily_row` — upsert one row by ``date`` key.
* :func:`rebuild_daily_metrics` — rebuild daily rows from a
  ``trades.jsonl`` stream (used by T10's graveyard repair tool).
* :func:`write_daily_csv` — full overwrite writer used by the repair tool.

Invariants
----------

* Line 1 is the header line — exactly ``",".join(fieldnames)``.
* The file ends with exactly one ``"\\n"`` — every row including the last
  one has a trailing newline.
* Every ``date`` appears at most once.
* Files are written via ``tmp + os.replace`` on the same filesystem
  directory, so a crash leaves a ``.csv.tmp`` orphan rather than a
  half-written target.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

__all__ = [
    "DAILY_FIELDS",
    "append_daily_row",
    "rebuild_daily_metrics",
    "write_daily_csv",
]


# Canonical column order — keep in lockstep with paper_runner.py:117-124.
DAILY_FIELDS: list[str] = [
    "date",
    "total_trades",
    "winning_trades",
    "losing_trades",
    "win_rate",
    "gross_pnl_usd",
    "net_pnl_usd",
    "fees_usd",
    "slippage_usd",
    "equity_usd",
    "daily_return_pct",
    "rolling_20d_sharpe",
    "rolling_20d_pf",
    "max_drawdown_pct",
    "max_drawdown_pct_vs_backtest",
    "profit_factor_lifetime",
    "bootstrap_ci_lo",
    "action",
    "kill_triggered",
    "kill_reason",
    "notes",
]


def _read_existing_rows(
    path: Path, fieldnames: Sequence[str]
) -> list[dict[str, str]]:
    """Read every data row from a writer-produced daily-metrics CSV.

    The header line is consumed and validated separately so that:

    * malformed headers (e.g. the graveyard ``kill_reason,notes2026-07-20,
      ...`` glued-line bug) raise ``ValueError`` instead of being silently
      treated as a data row;
    * the same-date upsert path never confuses a header row for a data
      row whose ``date`` happens to equal ``"date"``.

    Raises ``ValueError`` rather than silently skipping malformed lines so
    callers see real corruption instead of mysteriously missing rows.
    """
    expected_header = ",".join(fieldnames)
    with path.open("r", newline="") as fh:
        header_line = fh.readline()
        if not header_line:
            return []
        # Strip the trailing newline so the comparison tolerates both
        # \\n and \\r\\n line endings on disk.
        header_stripped = header_line.rstrip("\r\n")
        if header_stripped != expected_header:
            raise ValueError(
                f"{path}: header mismatch — got {header_stripped!r}, "
                f"expected {expected_header!r}"
            )
        reader = csv.DictReader(fh, fieldnames=list(fieldnames))
        rows: list[dict[str, str]] = []
        for line_no, raw in enumerate(reader, start=2):
            if raw is None:
                continue
            if all((v is None or v == "") for v in raw.values()):
                continue
            if any(v is None for v in raw.values()):
                raise ValueError(
                    f"{path}:{line_no} missing columns — "
                    f"got {raw!r}"
                )
            rows.append(dict(raw))
    return rows


def _write_rows_atomic(
    path: Path, rows: Iterable[Mapping[str, object]], fieldnames: Sequence[str]
) -> None:
    """Atomically write ``rows`` to ``path`` via ``tmp + os.replace``.

    The temporary file lives in the same directory as the target so the
    final rename stays on a single filesystem (atomic on POSIX/NTFS).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # csv.DictWriter writes the header once followed by every row.  The
    # default lineterminator is "\\r\\n" on every platform; we override it
    # to "\\n" so the file matches the single-newline contract documented
    # in this module's docstring and expected by ``test_ledger_writer``.
    with tmp.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    os.replace(tmp, path)


def append_daily_row(
    ledger_dir: Path,
    row: Mapping[str, object],
    fieldnames: Sequence[str] = DAILY_FIELDS,
) -> Path:
    """Upsert a single daily row, keyed on ``row["date"]``.

    Reads any existing ledger, drops rows whose ``date`` matches the
    incoming row, appends the incoming row, sorts by ``date`` ascending,
    and atomically writes the whole ledger back.

    Returns the path to the daily-metrics CSV.

    Invariants enforced here (and asserted by tests):

    * Line 1 = ``",".join(fieldnames)``.
    * File ends with exactly one ``"\\n"`` — last data row + trailing
      newline, no glued-together header/body lines.
    * Each ``date`` appears at most once (idempotent upsert).
    """
    if "date" not in row:
        raise ValueError("row must contain a 'date' key")
    path = Path(ledger_dir) / "daily_metrics.csv"

    existing: list[dict[str, str]] = []
    if path.exists() and path.stat().st_size > 0:
        existing = _read_existing_rows(path, fieldnames)

    new_date = str(row["date"])
    merged = [r for r in existing if r.get("date") != new_date]
    merged.append({k: row.get(k, "") for k in fieldnames})
    merged.sort(key=lambda r: r["date"])

    _write_rows_atomic(path, merged, fieldnames)
    return path


def write_daily_csv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    fieldnames: Sequence[str] = DAILY_FIELDS,
) -> None:
    """Full overwrite of a daily-metrics CSV (used by the repair tool).

    Rows are sorted by ``date`` ascending before write so callers don't
    need to pre-sort.  ``rows`` may be empty — in that case we still write
    the header so downstream readers see a well-formed file.
    """
    path = Path(path)
    sorted_rows = sorted(rows, key=lambda r: r["date"]) if rows else []
    _write_rows_atomic(path, sorted_rows, fieldnames)


def _fill_date(ts: str) -> str:
    """Extract the UTC calendar date from a fill ``ts`` field."""
    # ts is an ISO-8601 string, usually "+00:00" suffix.  Tolerate "Z".
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d")


def rebuild_daily_metrics(
    trades_path: Path,
    starting_capital: float,
) -> list[dict[str, object]]:
    """Rebuild daily rows from a ``trades.jsonl`` stream.

    The graveyard ``trades.jsonl`` contains alternating ``kind="fill"``
    lines (the only kind we care about here) and assorted non-fill
    bookkeeping (``kind="system"``, ``kind="warmup"`` …).  Non-fill lines
    are skipped silently — they have no per-trade schema we can aggregate.

    Pair-trade semantics
    --------------------

    Each pair trade consists of two fills (legs A and B) that share an
    identical ``tags`` dict — including ``tags.fees_usd`` and
    ``tags.slippage_usd`` — because the strategy tags both legs with the
    per-trade totals.  Summing ``tags.fees_usd`` across both fills would
    therefore double-count; we take the **first** fill's tags for the
    fees/slippage contribution of the trade.

    Win flag uses the sum of ``realized_pnl_after`` across both legs,
    which is the true economic P&L of the pair.

    Daily aggregation
    -----------------

    Fills are bucketed by the UTC calendar date of their ``ts`` field.
    Per-day metrics:

    * ``total_trades`` — number of pair trades whose fills both land on
      this UTC date.
    * ``winning_trades`` / ``losing_trades`` — counts of pair trades with
      ``realized_pnl > 0`` / ``<= 0``.
    * ``win_rate`` — ``winning_trades / total_trades`` rounded to 6dp.
    * ``fees_usd`` / ``slippage_usd`` — sum of per-trade fees across all
      trades that day (each counted once, not twice).
    * ``net_pnl_usd`` — last fill ``balance_after`` of the day minus the
      previous day's last ``balance_after`` (first day uses
      ``starting_capital``).
    * ``gross_pnl_usd`` — ``net_pnl_usd + fees_usd + slippage_usd``.
    * ``equity_usd`` — last fill ``balance_after`` of the day.
    * ``daily_return_pct`` — ``net_pnl_usd / previous_equity * 100``
      (first day uses ``starting_capital``).

    Rolling/kill metrics are not derivable from a single ``trades.jsonl``
    snapshot — they require the bootstrap/SHARPE state machine from
    ``paper_runner._evaluate_kill_criteria``.  We surface explicit
    ``0.0``/``False``/``""`` sentinels and ``action="REBUILT"`` so the
    downstream reconciler can tell a rebuilt row apart from a live one.

    Returns rows sorted ascending by ``date`` — no disk I/O.  Use
    :func:`write_daily_csv` to persist.
    """
    trades_path = Path(trades_path)
    rows_by_line: list[dict] = []
    skipped_non_fill = 0
    with trades_path.open("r") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                # Mirror the strict posture of ``append_daily_row``: never
                # silently drop corruption.  But a repair tool must keep
                # moving; raise a precise error pointing at the file.
                raise ValueError(
                    f"{trades_path}: malformed JSONL line — {raw[:80]!r}"
                )
            if obj.get("kind") != "fill":
                skipped_non_fill += 1
                continue
            rows_by_line.append(obj)

    # Group by entry_ts — each group = one pair trade.
    trades: dict[str, list[dict]] = {}
    for obj in rows_by_line:
        tags = obj.get("tags") or {}
        entry_ts = tags.get("entry_ts")
        if entry_ts is None:
            # Fill without an entry_ts tag — that violates the schema we
            # know.  Raise so a future test catches any drift.
            raise ValueError(
                f"fill at ts={obj.get('ts')!r} missing tags.entry_ts"
            )
        trades.setdefault(entry_ts, []).append(obj)

    # For each pair trade, fees/slippage come from the FIRST fill's tags,
    # pnl is sum of both legs' realized_pnl_after.
    per_trade: list[dict[str, object]] = []
    for entry_ts, fills in trades.items():
        fills_sorted = sorted(fills, key=lambda f: f.get("ts", ""))
        first = fills_sorted[0]
        tags = first.get("tags") or {}
        trade_pnl = sum(float(f.get("realized_pnl_after") or 0.0) for f in fills_sorted)
        per_trade.append(
            {
                "trade_date": _fill_date(first.get("ts", "")),
                "last_balance_after": float(fills_sorted[-1].get("balance_after") or 0.0),
                "fees_usd": float(tags.get("fees_usd") or 0.0),
                "slippage_usd": float(tags.get("slippage_usd") or 0.0),
                "win": trade_pnl > 0,
            }
        )

    per_trade.sort(key=lambda t: t["trade_date"])

    # Group by trade_date (every fill of the trade happens on the same
    # date because tags.entry_ts is earlier than every fill ts).
    by_day: dict[str, list[dict[str, object]]] = {}
    for t in per_trade:
        by_day.setdefault(t["trade_date"], []).append(t)

    daily_dates = sorted(by_day.keys())

    rows: list[dict[str, object]] = []
    prev_equity = float(starting_capital)
    for date in daily_dates:
        trades_today = by_day[date]
        total = len(trades_today)
        winning = sum(1 for t in trades_today if t["win"])
        losing = total - winning
        win_rate = round(winning / total, 6) if total else 0.0
        fees_day = round(sum(float(t["fees_usd"]) for t in trades_today), 6)
        slip_day = round(sum(float(t["slippage_usd"]) for t in trades_today), 6)
        last_balance = float(trades_today[-1]["last_balance_after"])
        net_pnl = round(last_balance - prev_equity, 6)
        gross_pnl = round(net_pnl + fees_day + slip_day, 6)
        if prev_equity > 0:
            daily_return = round(net_pnl / prev_equity * 100.0, 6)
        else:
            daily_return = 0.0
        rows.append(
            {
                "date": date,
                "total_trades": total,
                "winning_trades": winning,
                "losing_trades": losing,
                "win_rate": win_rate,
                "gross_pnl_usd": gross_pnl,
                "net_pnl_usd": net_pnl,
                "fees_usd": fees_day,
                "slippage_usd": slip_day,
                "equity_usd": round(last_balance, 6),
                "daily_return_pct": daily_return,
                "rolling_20d_sharpe": 0.0,
                "rolling_20d_pf": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_pct_vs_backtest": 0.0,
                "profit_factor_lifetime": 0.0,
                "bootstrap_ci_lo": "",
                "action": "REBUILT",
                "kill_triggered": False,
                "kill_reason": "",
                "notes": "rebuilt from trades.jsonl",
            }
        )
        prev_equity = last_balance

    return rows