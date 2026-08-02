"""Portfolio state snapshots persisted to parquet (I19).

A :class:`PortfolioSnapshot` freezes the full book state at one instant:
equity, cash, signed positions, mark prices, and a free-form risk-metric
dict (e.g. outputs of ``_shared/market_making/portfolio_risk``). Snapshots
are appended to a directory as two parquet files:

  ``snapshots.parquet``  one row per snapshot: ts, equity, cash,
                         risk_metrics (JSON)
  ``positions.parquet``  long format: ts, symbol, qty, price

Reads support point-in-time recovery (:func:`snapshot_at` returns the
latest snapshot at or before a timestamp) and pairwise diffs
(:func:`diff_snapshots`) for position/equity/metric changes.

This is the only module in ``_shared/portfolio`` that does file I/O.

References:
  - López de Prado (2018), "Advances in Financial Machine Learning",
    Ch. 8 (feature/state persistence for reproducibility).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import pandas as pd

SNAPSHOTS_FILE = "snapshots.parquet"
POSITIONS_FILE = "positions.parquet"


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Immutable book state at one timestamp."""

    ts: pd.Timestamp
    equity: float
    cash: float
    positions: Dict[str, float]        # symbol -> signed qty
    prices: Dict[str, float]           # symbol -> mark price
    risk_metrics: Dict[str, float]     # name -> value (e.g. var, cvar, lev)


@dataclass(frozen=True)
class SnapshotDiff:
    """Difference between two snapshots (``b`` minus ``a``)."""

    ts_a: pd.Timestamp
    ts_b: pd.Timestamp
    equity_delta: float
    cash_delta: float
    positions_opened: Dict[str, float]    # in b, not in a
    positions_closed: Dict[str, float]    # in a, not in b (a's qty)
    positions_changed: Dict[str, tuple]   # symbol -> (qty_a, qty_b)
    metric_deltas: Dict[str, float]       # for metric names present in both


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def save_snapshot(snap: PortfolioSnapshot, dir_path: str | Path) -> Path:
    """Append ``snap`` to the snapshot store at ``dir_path`` (created if
    missing). Rewrites the two parquet files; snapshot volumes in this
    project are small enough that read-modify-write is the simple correct
    choice. Returns the directory path.
    """
    d = Path(dir_path)
    d.mkdir(parents=True, exist_ok=True)

    snaps = _read_snapshot_rows(d)
    snaps.append({
        "ts": snap.ts,
        "equity": snap.equity,
        "cash": snap.cash,
        "risk_metrics": json.dumps(snap.risk_metrics, sort_keys=True),
    })
    snap_df = pd.DataFrame(snaps).drop_duplicates(subset="ts", keep="last")
    snap_df = snap_df.sort_values("ts").reset_index(drop=True)
    snap_df.to_parquet(d / SNAPSHOTS_FILE, index=False)

    pos = _read_position_rows(d)
    ts_norm = pd.Timestamp(snap.ts)
    pos = [r for r in pos if pd.Timestamp(r["ts"]) != ts_norm]
    for sym, qty in snap.positions.items():
        pos.append({
            "ts": snap.ts,
            "symbol": sym,
            "qty": qty,
            "price": snap.prices.get(sym, 0.0),
        })
    pos_df = pd.DataFrame(
        pos, columns=["ts", "symbol", "qty", "price"]
    ).sort_values(["ts", "symbol"]).reset_index(drop=True)
    pos_df.to_parquet(d / POSITIONS_FILE, index=False)
    return d


def _read_snapshot_rows(d: Path) -> List[dict]:
    f = d / SNAPSHOTS_FILE
    if not f.exists():
        return []
    return pd.read_parquet(f).to_dict("records")


def _read_position_rows(d: Path) -> List[dict]:
    f = d / POSITIONS_FILE
    if not f.exists():
        return []
    return pd.read_parquet(f).to_dict("records")


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def load_snapshots(dir_path: str | Path) -> List[PortfolioSnapshot]:
    """Load all snapshots, ordered by ts."""
    d = Path(dir_path)
    snap_rows = _read_snapshot_rows(d)
    if not snap_rows:
        return []
    pos_df = pd.DataFrame(
        _read_position_rows(d), columns=["ts", "symbol", "qty", "price"]
    )
    out = []
    for row in sorted(snap_rows, key=lambda r: r["ts"]):
        ts = pd.Timestamp(row["ts"])
        sub = pos_df[pos_df["ts"] == ts] if not pos_df.empty else pos_df
        out.append(PortfolioSnapshot(
            ts=ts,
            equity=float(row["equity"]),
            cash=float(row["cash"]),
            positions={r["symbol"]: float(r["qty"]) for _, r in sub.iterrows()},
            prices={r["symbol"]: float(r["price"]) for _, r in sub.iterrows()},
            risk_metrics={k: float(v) for k, v in json.loads(row["risk_metrics"]).items()},
        ))
    return out


def snapshot_at(
    dir_path: str | Path, ts: pd.Timestamp
) -> Optional[PortfolioSnapshot]:
    """Point-in-time recovery: latest snapshot with ``ts_snap <= ts``."""
    ts = pd.Timestamp(ts)
    eligible = [s for s in load_snapshots(dir_path) if s.ts <= ts]
    return eligible[-1] if eligible else None


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def diff_snapshots(a: PortfolioSnapshot, b: PortfolioSnapshot) -> SnapshotDiff:
    """Position / equity / metric changes from ``a`` to ``b``."""
    keys_a, keys_b = set(a.positions), set(b.positions)
    opened = {k: b.positions[k] for k in keys_b - keys_a}
    closed = {k: a.positions[k] for k in keys_a - keys_b}
    changed = {
        k: (a.positions[k], b.positions[k])
        for k in keys_a & keys_b
        if a.positions[k] != b.positions[k]
    }
    common_metrics = set(a.risk_metrics) & set(b.risk_metrics)
    metric_deltas = {
        k: b.risk_metrics[k] - a.risk_metrics[k]
        for k in common_metrics
        if b.risk_metrics[k] != a.risk_metrics[k]
    }
    return SnapshotDiff(
        ts_a=a.ts, ts_b=b.ts,
        equity_delta=b.equity - a.equity,
        cash_delta=b.cash - a.cash,
        positions_opened=opened,
        positions_closed=closed,
        positions_changed=changed,
        metric_deltas=metric_deltas,
    )
