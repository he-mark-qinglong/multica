"""P&L dashboard — portfolio-wide profit-and-loss aggregator across every strategy.

Public surface (used by ``run.py`` and ``tests/``):
    TradeRecord            — dataclass for a single parsed trade row.
    Snapshot                — dataclass returned by :func:`build_snapshot`.
    parse_trades_csv(...)   — streaming CSV reader that pulls pnl/timestamp/symbol.
    parse_summary_json(...) — read ``results/summary.json`` and project fields.
    build_snapshot(...)     — walk a strategies root and produce a Snapshot.
    write_snapshot(...)     — convenience wrapper: build + serialise to JSON.
    iter_strategy_dirs(...) — yield ``(strategy_name, results_path)`` pairs.

Design notes
------------
- Stdlib-only. No duckdb, no fastapi, no pandas, no network calls.
- Pure functions: parsers and aggregators never mutate the input rows.
- Streaming-friendly: CSV parsing is line-by-line so we never hold more than
  one trade row in memory at a time.
- Resilient: a malformed CSV row, missing column, or unreadable file is
  recorded in ``missing_sources`` and counted under ``skipped_rows`` /
  ``skipped_files``. The dashboard still ships a partial-but-useful
  snapshot rather than blowing up.
- Mirrors the display-engine's column-fallback convention
  (``pnl_pct`` → ``pnl`` → ``ret_pct`` and ``exit_ts`` → ``ts`` → ``time``),
  so the dashboard can read every trade CSV the backend can.
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


# Column-fallback lookup tables — kept aligned with strategy-display-engine's
# backend/data.py so the two views never disagree on which value to read.
PNL_COL_CANDIDATES = ("pnl_pct", "pnl", "ret_pct")
EXIT_TS_COL_CANDIDATES = ("exit_ts", "exit_time", "ts", "time", "exit_date")
ENTRY_TS_COL_CANDIDATES = ("entry_ts", "entry_time", "entry_date", "ts", "time")
SYMBOL_COL_CANDIDATES = ("symbol", "pair")
DIRECTION_COL_CANDIDATES = ("direction", "side")
REASON_COL_CANDIDATES = ("exit_reason", "reason", "exit_type")

# Equity curve cap so a multi-year backtest doesn't blow up the JSON.
DEFAULT_EQUITY_MAX_POINTS = 5000
# Top-N winners / losers retained verbatim.
DEFAULT_TOP_N = 10


@dataclass(frozen=True)
class TradeRecord:
    """One parsed trade row from a strategy ``trades_*.csv``.

    All fields except ``pnl`` and the timestamps are optional because CSV
    schemas differ across strategies (pairs, single-symbol, futures-only).
    The aggregator only ever requires ``pnl`` and ``exit_ts``; everything
    else is bucketed when present.
    """

    strategy: str
    source_file: str
    symbol: Optional[str]
    direction: Optional[str]
    pnl: float
    exit_ts: Optional[str]
    entry_ts: Optional[str]
    exit_reason: Optional[str]
    line_no: int

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "source_file": self.source_file,
            "symbol": self.symbol,
            "direction": self.direction,
            "pnl": self.pnl,
            "exit_ts": self.exit_ts,
            "entry_ts": self.entry_ts,
            "exit_reason": self.exit_reason,
            "line_no": self.line_no,
        }


@dataclass
class Snapshot:
    """Portfolio-wide P&L snapshot.

    ``by_strategy`` is sorted by ``sum_pnl_pct`` descending so the best
    performers float to the top of the JSON. ``equity_curve`` keeps the
    ``DEFAULT_EQUITY_MAX_POINTS`` cap; if there are more trades the curve
    is downsampled (every Nth point) so the JSON stays bounded.
    """

    generated_at: str
    root_scanned: str
    version: str
    n_strategies_scanned: int = 0
    n_strategies_with_trades: int = 0
    n_strategies_profitable: int = 0
    n_trades: int = 0
    skipped_rows: int = 0
    skipped_files: int = 0
    sum_pnl_pct: float = 0.0
    mean_pnl_pct: float = 0.0
    median_pnl_pct: float = 0.0
    std_pnl_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    best_trade_pnl_pct: float = 0.0
    worst_trade_pnl_pct: float = 0.0
    unique_days: int = 0
    span_start: Optional[str] = None
    span_end: Optional[str] = None
    drawdown_max_pct: float = 0.0
    drawdown_peak_ts: Optional[str] = None
    drawdown_trough_ts: Optional[str] = None
    by_strategy: list = field(default_factory=list)
    by_symbol: list = field(default_factory=list)
    by_day: list = field(default_factory=list)
    top_winners: list = field(default_factory=list)
    top_losers: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    equity_curve_trade_count: int = 0
    equity_curve_downsampled: bool = False
    missing_sources: list = field(default_factory=list)
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "version": self.version,
            "root_scanned": self.root_scanned,
            "totals": {
                "n_strategies_scanned": self.n_strategies_scanned,
                "n_strategies_with_trades": self.n_strategies_with_trades,
                "n_strategies_profitable": self.n_strategies_profitable,
                "n_trades": self.n_trades,
                "skipped_rows": self.skipped_rows,
                "skipped_files": self.skipped_files,
                "sum_pnl_pct": self.sum_pnl_pct,
                "mean_pnl_pct": self.mean_pnl_pct,
                "median_pnl_pct": self.median_pnl_pct,
                "std_pnl_pct": self.std_pnl_pct,
                "win_rate": self.win_rate,
                "profit_factor": self.profit_factor,
                "best_trade_pnl_pct": self.best_trade_pnl_pct,
                "worst_trade_pnl_pct": self.worst_trade_pnl_pct,
                "unique_days": self.unique_days,
                "span_start": self.span_start,
                "span_end": self.span_end,
            },
            "drawdown": {
                "max_drawdown_pct": self.drawdown_max_pct,
                "peak_ts": self.drawdown_peak_ts,
                "trough_ts": self.drawdown_trough_ts,
            },
            "by_strategy": list(self.by_strategy),
            "by_symbol": list(self.by_symbol),
            "by_day": list(self.by_day),
            "top_winners": list(self.top_winners),
            "top_losers": list(self.top_losers),
            "equity_curve": {
                "trade_count": self.equity_curve_trade_count,
                "downsampled": self.equity_curve_downsampled,
                "points": list(self.equity_curve),
            },
            "missing_sources": list(self.missing_sources),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------


def iter_strategy_dirs(root: str | os.PathLike) -> Iterator[tuple[str, Path]]:
    """Yield ``(strategy_name, results_path)`` for every strategy under ``root``.

    A "strategy directory" is any direct subdirectory of ``root`` that
    contains a ``results/`` folder with at least one ``trades_*.csv``
    file or a ``summary.json``. Strategies that lack both are skipped
    (they're usually docs / tests / sandbox scaffolds).
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return
    for child in sorted(root_path.iterdir()):
        if not child.is_dir():
            continue
        results = child / "results"
        if not results.is_dir():
            continue
        has_trades = any(results.glob("trades_*.csv"))
        has_summary = (results / "summary.json").is_file()
        if not has_trades and not has_summary:
            continue
        yield child.name, results


def iter_trades_csv(results_path: Path) -> Iterator[Path]:
    """Yield every ``trades_*.csv`` under a strategy ``results/`` directory."""
    if not results_path.is_dir():
        return
    for csv_path in sorted(results_path.glob("trades_*.csv")):
        if csv_path.is_file():
            yield csv_path


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def _first_existing(header: list[str], candidates: Iterable[str]) -> Optional[str]:
    """Return the first column name in ``candidates`` that's present in ``header``."""
    hset = set(header)
    for c in candidates:
        if c in hset:
            return c
    return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def parse_trades_csv(
    strategy: str,
    csv_path: Path,
) -> tuple[list[TradeRecord], int]:
    """Parse a single trades CSV into a list of ``TradeRecord``.

    Returns ``(records, skipped_rows)``. Malformed rows are counted under
    ``skipped_rows`` and skipped silently so a single bad row never aborts
    the whole dashboard. An empty CSV (header only) yields ``[]``.
    """
    records: list[TradeRecord] = []
    skipped = 0
    try:
        with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                return records, 0
            header = [h.strip() for h in header]

            pnl_col = _first_existing(header, PNL_COL_CANDIDATES)
            exit_ts_col = _first_existing(header, EXIT_TS_COL_CANDIDATES)
            entry_ts_col = _first_existing(header, ENTRY_TS_COL_CANDIDATES)
            symbol_col = _first_existing(header, SYMBOL_COL_CANDIDATES)
            direction_col = _first_existing(header, DIRECTION_COL_CANDIDATES)
            reason_col = _first_existing(header, REASON_COL_CANDIDATES)

            if pnl_col is None or exit_ts_col is None:
                # Without pnl or exit_ts we cannot bucket this file; count
                # all data rows as skipped for the file-level tally.
                for _ in reader:
                    skipped += 1
                return records, skipped

            idx = {name: header.index(name) for name in header}
            source_name = csv_path.name
            for line_no, row in enumerate(reader, start=2):
                if not row:
                    skipped += 1
                    continue
                pnl = _coerce_float(row[idx[pnl_col]])
                if pnl is None:
                    skipped += 1
                    continue
                exit_ts = (row[idx[exit_ts_col]] or "").strip() or None
                entry_ts = (
                    (row[idx[entry_ts_col]] or "").strip()
                    if entry_ts_col
                    else None
                ) or None
                symbol = (
                    ((row[idx[symbol_col]] or "").strip() or None)
                    if symbol_col
                    else None
                )
                direction = (
                    ((row[idx[direction_col]] or "").strip() or None)
                    if direction_col
                    else None
                )
                reason = (
                    ((row[idx[reason_col]] or "").strip() or None)
                    if reason_col
                    else None
                )
                records.append(
                    TradeRecord(
                        strategy=strategy,
                        source_file=source_name,
                        symbol=symbol,
                        direction=direction,
                        pnl=pnl,
                        exit_ts=exit_ts,
                        entry_ts=entry_ts,
                        exit_reason=reason,
                        line_no=line_no,
                    )
                )
    except OSError:
        return records, skipped
    return records, skipped


# ---------------------------------------------------------------------------
# summary.json projection
# ---------------------------------------------------------------------------


def parse_summary_json(results_path: Path) -> dict:
    """Read ``results/summary.json`` and project a flat dict of headline numbers.

    Returns ``{}`` if the file is missing, unreadable, or not a JSON object
    (some legacy summaries serialise a list rather than a dict). Keys we
    surface:

    - ``iteration``         int
    - ``tag``               str  ("PROFITABLE" / "UNPROFITABLE" / "REVIEW" / …)
    - ``sharpe``            float (prefers portfolio_sharpe_daily_resampled)
    - ``annualized_return`` float (portfolio_annualized_return_daily)
    - ``max_drawdown_pct``  float (max pair, falls back to portfolio)
    - ``win_rate``          float (averaged across per_pair, falls back to portfolio)
    - ``profit_factor``     float (averaged across per_pair, falls back to portfolio)
    - ``n_trades``          int  (summed across per_pair, falls back to n_trades_total)
    - ``campaign``          str
    - ``hypothesis``        str
    """
    path = results_path / "summary.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        # Legacy / non-standard summary shapes serialise a list; we
        # cannot project headline numbers from a list, so bail.
        return {}

    out: dict = {}
    out["iteration"] = raw.get("iteration")
    out["tag"] = raw.get("tag")
    out["campaign"] = raw.get("campaign")
    out["hypothesis"] = raw.get("hypothesis")

    sharpe = raw.get("portfolio_sharpe_daily_resampled") or raw.get(
        "avg_pair_sharpe_daily_resampled"
    )
    if sharpe is not None:
        out["sharpe"] = sharpe

    aret = raw.get("portfolio_annualized_return_daily") or raw.get(
        "avg_pair_annualized_return_daily"
    )
    if aret is not None:
        out["annualized_return"] = aret

    pf_avg = raw.get("profit_factor_avg")
    if pf_avg is not None:
        out["profit_factor"] = pf_avg
    elif raw.get("profit_factor") is not None:
        out["profit_factor"] = raw["profit_factor"]

    max_dd = raw.get("avg_pair_max_drawdown_pct") or raw.get(
        "portfolio_max_drawdown_pct"
    ) or raw.get("max_drawdown_pct")
    if max_dd is not None:
        out["max_drawdown_pct"] = max_dd

    per_pair = raw.get("per_pair") or {}
    n_trades_total: Optional[int] = None
    win_rates: list[float] = []
    if isinstance(per_pair, dict) and per_pair:
        n_sum = 0
        for _, v in per_pair.items():
            if not isinstance(v, dict):
                continue
            n = v.get("n_trades")
            if isinstance(n, int):
                n_sum += n
            wr = v.get("win_rate")
            if isinstance(wr, (int, float)):
                win_rates.append(float(wr))
        if n_sum > 0:
            n_trades_total = n_sum
        if win_rates:
            out["win_rate"] = sum(win_rates) / len(win_rates)
    if n_trades_total is not None:
        out["n_trades"] = n_trades_total
    elif isinstance(raw.get("n_trades_total"), int):
        out["n_trades"] = raw["n_trades_total"]

    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _profit_factor(pnls: list[float]) -> float:
    """Return the profit factor: sum(wins) / |sum(losses)|, or 0 if no losses."""
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss == 0:
        return float(gross_win > 0)
    return gross_win / gross_loss


def _bucket_metrics(pnls: list[float]) -> dict:
    if not pnls:
        return {
            "n_trades": 0,
            "sum_pnl_pct": 0.0,
            "mean_pnl_pct": 0.0,
            "median_pnl_pct": 0.0,
            "std_pnl_pct": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "best_pnl_pct": 0.0,
            "worst_pnl_pct": 0.0,
        }
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    mean = sum(pnls) / n
    var = sum((p - mean) ** 2 for p in pnls) / max(n - 1, 1)
    std = math.sqrt(var) if var > 0 else 0.0
    return {
        "n_trades": n,
        "sum_pnl_pct": sum(pnls),
        "mean_pnl_pct": mean,
        "median_pnl_pct": statistics.median(pnls),
        "std_pnl_pct": std,
        "win_rate": wins / n,
        "profit_factor": _profit_factor(pnls),
        "best_pnl_pct": max(pnls),
        "worst_pnl_pct": min(pnls),
    }


def _exit_day(ts: Optional[str]) -> Optional[str]:
    """Extract YYYY-MM-DD from a possibly-fragmented ISO-ish timestamp."""
    if not ts:
        return None
    s = ts.strip()
    if not s:
        return None
    # ISO datetime: "2024-09-06T10:30:00+00:00"
    if "T" in s:
        return s.split("T", 1)[0]
    # Date-only: "2024-09-06"
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    return None


def _compare_trade_ts(ts: Optional[str]) -> str:
    """Return a string suitable for lexicographic ordering by time."""
    if not ts:
        return ""
    return ts.strip()


def _downsample_curve(points: list[dict], max_points: int) -> tuple[list[dict], bool]:
    """Return ``(points, downsampled)`` — stride the curve if it's too long."""
    if len(points) <= max_points:
        return points, False
    stride = math.ceil(len(points) / max_points)
    sampled = points[::stride]
    # Always keep the last point so the chart's right edge is accurate.
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    return sampled, True


def build_snapshot(
    root: str | os.PathLike,
    *,
    equity_max_points: int = DEFAULT_EQUITY_MAX_POINTS,
    top_n: int = DEFAULT_TOP_N,
) -> Snapshot:
    """Walk ``<root>/strategies`` and produce a :class:`Snapshot`.

    The function is the dashboard's single entry point. It is pure
    (no writes, no network, no daemon side-effects) — callers control
    where the JSON output goes.
    """
    t0 = time.perf_counter()
    snap = Snapshot(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        root_scanned=str(root),
        version="0.1.0",
    )

    strategy_records: list[TradeRecord] = []
    by_strategy_pnl: dict[str, list[float]] = defaultdict(list)
    by_symbol_pnl: dict[str, list[float]] = defaultdict(list)
    by_day_pnl: dict[str, list[float]] = defaultdict(list)
    summary_by_strategy: dict[str, dict] = {}

    for strategy_name, results in iter_strategy_dirs(root):
        snap.n_strategies_scanned += 1
        file_count = 0
        file_skipped = 0
        for csv_path in iter_trades_csv(results):
            file_count += 1
            records, skipped = parse_trades_csv(strategy_name, csv_path)
            strategy_records.extend(records)
            by_strategy_pnl[strategy_name].extend(r.pnl for r in records)
            for r in records:
                if r.symbol:
                    by_symbol_pnl[r.symbol].append(r.pnl)
                day = _exit_day(r.exit_ts)
                if day:
                    by_day_pnl[day].append(r.pnl)
            if skipped:
                file_skipped += 1
            if not records and skipped:
                snap.missing_sources.append(
                    {
                        "strategy": strategy_name,
                        "file": str(csv_path.relative_to(results.parent)),
                        "reason": "no_parsable_pnl_rows",
                    }
                )
        summary = parse_summary_json(results)
        if summary:
            summary_by_strategy[strategy_name] = summary
        if file_count == 0 and not summary:
            # Strategy with no usable artefacts — count as skipped file so
            # the totals stay honest.
            snap.skipped_files += 1
            snap.missing_sources.append(
                {
                    "strategy": strategy_name,
                    "file": str((results / "trades_*.csv").relative_to(results.parent)),
                    "reason": "no_trades_csv_or_summary",
                }
            )
        elif file_skipped == file_count and file_count > 0:
            snap.skipped_files += 1

    # Portfolio-level metrics.
    all_pnl = [r.pnl for r in strategy_records]
    snap.n_trades = len(all_pnl)
    if all_pnl:
        snap.n_strategies_with_trades = sum(
            1 for v in by_strategy_pnl.values() if v
        )
        totals = _bucket_metrics(all_pnl)
        snap.sum_pnl_pct = totals["sum_pnl_pct"]
        snap.mean_pnl_pct = totals["mean_pnl_pct"]
        snap.median_pnl_pct = totals["median_pnl_pct"]
        snap.std_pnl_pct = totals["std_pnl_pct"]
        snap.win_rate = totals["win_rate"]
        snap.profit_factor = totals["profit_factor"]
        snap.best_trade_pnl_pct = totals["best_pnl_pct"]
        snap.worst_trade_pnl_pct = totals["worst_pnl_pct"]

        # Span & unique days.
        days = sorted({d for d in by_day_pnl if d})
        snap.unique_days = len(days)
        if days:
            snap.span_start = days[0]
            snap.span_end = days[-1]

        # Per-strategy rows (sorted by sum_pnl_pct desc).
        rows: list[dict] = []
        for strategy_name, pnls in by_strategy_pnl.items():
            if not pnls:
                continue
            m = _bucket_metrics(pnls)
            summary = summary_by_strategy.get(strategy_name, {})
            profitable = (m["sum_pnl_pct"] > 0) and (
                summary.get("tag") == "PROFITABLE" or not summary.get("tag")
            )
            if profitable:
                snap.n_strategies_profitable += 1
            rows.append(
                {
                    "strategy": strategy_name,
                    "n_trades": m["n_trades"],
                    "sum_pnl_pct": m["sum_pnl_pct"],
                    "mean_pnl_pct": m["mean_pnl_pct"],
                    "win_rate": m["win_rate"],
                    "profit_factor": m["profit_factor"],
                    "best_pnl_pct": m["best_pnl_pct"],
                    "worst_pnl_pct": m["worst_pnl_pct"],
                    "summary_sharpe": summary.get("sharpe"),
                    "summary_max_drawdown_pct": summary.get("max_drawdown_pct"),
                    "summary_tag": summary.get("tag"),
                    "summary_iteration": summary.get("iteration"),
                }
            )
        rows.sort(key=lambda r: r["sum_pnl_pct"], reverse=True)
        snap.by_strategy = rows

        # Per-symbol rows.
        srows: list[dict] = []
        for sym, pnls in by_symbol_pnl.items():
            if not pnls:
                continue
            m = _bucket_metrics(pnls)
            srows.append(
                {
                    "symbol": sym,
                    "n_trades": m["n_trades"],
                    "sum_pnl_pct": m["sum_pnl_pct"],
                    "mean_pnl_pct": m["mean_pnl_pct"],
                    "win_rate": m["win_rate"],
                    "profit_factor": m["profit_factor"],
                    "best_pnl_pct": m["best_pnl_pct"],
                    "worst_pnl_pct": m["worst_pnl_pct"],
                }
            )
        srows.sort(key=lambda r: r["sum_pnl_pct"], reverse=True)
        snap.by_symbol = srows

        # Daily rows.
        drows: list[dict] = []
        for day, pnls in by_day_pnl.items():
            if not pnls:
                continue
            m = _bucket_metrics(pnls)
            drows.append(
                {
                    "date": day,
                    "n_trades": m["n_trades"],
                    "sum_pnl_pct": m["sum_pnl_pct"],
                    "mean_pnl_pct": m["mean_pnl_pct"],
                    "win_rate": m["win_rate"],
                }
            )
        drows.sort(key=lambda r: r["date"])
        snap.by_day = drows

        # Top winners / losers (verbatim trade rows).
        sorted_records = sorted(strategy_records, key=lambda r: r.pnl)
        snap.top_losers = [r.as_dict() for r in sorted_records[:top_n]]
        snap.top_winners = [
            r.as_dict() for r in sorted_records[-top_n:][::-1]
        ]

        # Equity curve: sort by exit_ts, cumulative pnl, running drawdown.
        ts_records = sorted(
            strategy_records, key=lambda r: _compare_trade_ts(r.exit_ts)
        )
        cum = 0.0
        peak = 0.0
        peak_ts: Optional[str] = None
        trough_ts: Optional[str] = None
        max_dd = 0.0
        raw_curve: list[dict] = []
        for r in ts_records:
            cum += r.pnl
            if cum > peak:
                peak = cum
                peak_ts = r.exit_ts
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
                trough_ts = r.exit_ts
            raw_curve.append(
                {
                    "ts": r.exit_ts,
                    "cum_pnl_pct": cum,
                    "drawdown_pct": dd,
                }
            )
        snap.drawdown_max_pct = max_dd
        snap.drawdown_peak_ts = peak_ts
        snap.drawdown_trough_ts = trough_ts
        snap.equity_curve_trade_count = len(raw_curve)
        downsampled_pts, downsampled = _downsample_curve(
            raw_curve, equity_max_points
        )
        snap.equity_curve = downsampled_pts
        snap.equity_curve_downsampled = downsampled

    snap.elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return snap


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def write_snapshot(snap: Snapshot, path: str | os.PathLike) -> Path:
    """Serialise ``snap`` to JSON at ``path`` and return the resolved path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap.as_dict(), indent=2, sort_keys=False))
    return out