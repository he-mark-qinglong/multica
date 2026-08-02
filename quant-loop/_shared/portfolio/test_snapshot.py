"""Tests for portfolio/snapshot.py (I19)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pandas as pd
import pytest

from _shared.portfolio.snapshot import (
    PortfolioSnapshot, SnapshotDiff, diff_snapshots, load_snapshots,
    save_snapshot, snapshot_at,
)


def _snap(day, equity, positions, metrics=None, hour=0):
    return PortfolioSnapshot(
        ts=pd.Timestamp(f"2026-01-{day:02d} {hour:02d}:00"),
        equity=equity,
        cash=equity * 0.5,
        positions=positions,
        prices={s: 100.0 for s in positions},
        risk_metrics=metrics or {},
    )


def test_roundtrip(tmp_path):
    snaps = [
        _snap(1, 1000.0, {"BTC": 1.0}, {"var": 0.02}),
        _snap(2, 1010.0, {"BTC": 1.0, "ETH": 5.0}, {"var": 0.025}),
        _snap(3, 990.0, {"ETH": 5.0}, {"var": 0.03}),
    ]
    for s in snaps:
        save_snapshot(s, tmp_path)
    loaded = load_snapshots(tmp_path)
    assert len(loaded) == 3
    assert loaded[0].ts < loaded[1].ts < loaded[2].ts
    assert loaded[1].positions == {"BTC": 1.0, "ETH": 5.0}
    assert loaded[1].prices == {"BTC": 100.0, "ETH": 100.0}
    assert loaded[1].risk_metrics == {"var": pytest.approx(0.025)}
    assert loaded[2].equity == pytest.approx(990.0)


def test_save_same_ts_overwrites(tmp_path):
    save_snapshot(_snap(1, 1000.0, {"BTC": 1.0}), tmp_path)
    save_snapshot(_snap(1, 2000.0, {"BTC": 2.0}), tmp_path)
    loaded = load_snapshots(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].equity == pytest.approx(2000.0)
    assert loaded[0].positions == {"BTC": 2.0}


def test_snapshot_at_point_in_time(tmp_path):
    for day in (1, 3, 5):
        save_snapshot(_snap(day, 1000.0 + day, {"BTC": float(day)}), tmp_path)
    s = snapshot_at(tmp_path, pd.Timestamp("2026-01-04 12:00"))
    assert s is not None and s.ts == pd.Timestamp("2026-01-03 00:00")
    assert s.positions == {"BTC": 3.0}
    # Exact hit works too.
    s = snapshot_at(tmp_path, pd.Timestamp("2026-01-05 00:00"))
    assert s.positions == {"BTC": 5.0}
    # Before the first snapshot -> None.
    assert snapshot_at(tmp_path, pd.Timestamp("2025-12-31")) is None


def test_load_empty_dir(tmp_path):
    assert load_snapshots(tmp_path) == []
    assert snapshot_at(tmp_path, pd.Timestamp("2026-01-01")) is None


def test_diff_snapshots():
    a = _snap(1, 1000.0, {"BTC": 1.0, "ETH": 5.0}, {"var": 0.02, "lev": 1.0})
    b = _snap(2, 1100.0, {"BTC": 2.0, "SOL": 10.0}, {"var": 0.03, "lev": 1.0})
    d = diff_snapshots(a, b)
    assert isinstance(d, SnapshotDiff)
    assert d.equity_delta == pytest.approx(100.0)
    assert d.cash_delta == pytest.approx(50.0)
    assert d.positions_opened == {"SOL": 10.0}
    assert d.positions_closed == {"ETH": 5.0}
    assert d.positions_changed == {"BTC": (1.0, 2.0)}
    assert d.metric_deltas == {"var": pytest.approx(0.01)}


def test_diff_no_changes():
    a = _snap(1, 1000.0, {"BTC": 1.0}, {"var": 0.02})
    b = _snap(2, 1000.0, {"BTC": 1.0}, {"var": 0.02})
    d = diff_snapshots(a, b)
    assert d.equity_delta == 0.0
    assert d.positions_opened == {} and d.positions_closed == {}
    assert d.positions_changed == {} and d.metric_deltas == {}
