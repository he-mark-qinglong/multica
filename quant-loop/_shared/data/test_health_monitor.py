"""Tests for data health monitor."""
import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from _shared.data.health_monitor import DataHealthMonitor, HealthReport, AssetHealth


class TestHealthReport:
    def test_stale_and_missing(self):
        report = HealthReport(checked_at="2026-01-01T00:00:00Z")
        report.assets = [
            AssetHealth(name="a", path="/a", exists=True, last_update=None,
                       age_hours=None, row_count=100, expected_freq="15m", is_stale=False),
            AssetHealth(name="b", path="/b", exists=True, last_update=None,
                       age_hours=48, row_count=50, expected_freq="15m", is_stale=True),
            AssetHealth(name="c", path="/c", exists=False, last_update=None,
                       age_hours=None, row_count=0, expected_freq="", is_stale=True),
        ]
        assert len(report.stale_assets) == 2
        assert len(report.missing_assets) == 1
        assert report.healthy_count == 1

    def test_summary(self):
        report = HealthReport(checked_at="2026-01-01")
        report.assets = [
            AssetHealth(name="a", path="/a", exists=True, last_update=None,
                       age_hours=None, row_count=100, expected_freq="15m", is_stale=False),
        ]
        s = report.summary()
        assert "1 healthy" in s


class TestDataHealthMonitor:
    def test_finds_existing_parquet(self, tmp_path):
        # Create a fake parquet
        import pandas as pd
        df = pd.DataFrame({"open_time": [1700000000000, 1700000060000], "close": [100, 101]})
        data_dir = tmp_path / "data" / "perp_15m"
        data_dir.mkdir(parents=True)
        df.to_parquet(data_dir / "BTCUSDT_15m.parquet")

        monitor = DataHealthMonitor(
            data_root=tmp_path,
            assets=[{"name": "BTCUSDT_15m", "pattern": "data/perp_15m/BTCUSDT_15m.parquet", "max_age_hours": 876000}],
        )
        report = monitor.check_all()
        assert len(report.assets) == 1
        assert report.assets[0].exists
        assert report.assets[0].row_count == 2

    def test_reports_missing_file(self, tmp_path):
        monitor = DataHealthMonitor(
            data_root=tmp_path,
            assets=[{"name": "missing", "pattern": "nonexistent.parquet", "max_age_hours": 24}],
        )
        report = monitor.check_all()
        assert len(report.missing_assets) == 1
        assert not report.missing_assets[0].exists

    def test_detects_stale_jsonl(self, tmp_path):
        # Create stale JSONL (old timestamp)
        old_ts = int((datetime.now(timezone.utc) - timedelta(hours=48)).timestamp() * 1000)
        data_dir = tmp_path / "data" / "liquidations"
        data_dir.mkdir(parents=True)
        with open(data_dir / "BTCUSDT.jsonl", "w") as f:
            f.write(json.dumps({"ts": old_ts, "side": "BUY", "qty": 0.1}) + "\n")

        monitor = DataHealthMonitor(
            data_root=tmp_path,
            assets=[{"name": "liq_BTC", "pattern": "data/liquidations/BTCUSDT.jsonl", "max_age_hours": 1}],
        )
        report = monitor.check_all()
        assert len(report.stale_assets) == 1
        assert report.stale_assets[0].age_hours > 1

    def test_fresh_data_not_stale(self, tmp_path):
        # Create fresh JSONL
        fresh_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        data_dir = tmp_path / "data" / "liquidations"
        data_dir.mkdir(parents=True)
        with open(data_dir / "BTCUSDT.jsonl", "w") as f:
            f.write(json.dumps({"ts": fresh_ts, "side": "BUY", "qty": 0.1}) + "\n")

        monitor = DataHealthMonitor(
            data_root=tmp_path,
            assets=[{"name": "liq_BTC", "pattern": "data/liquidations/BTCUSDT.jsonl", "max_age_hours": 1}],
        )
        report = monitor.check_all()
        assert len(report.stale_assets) == 0
        assert report.assets[0].row_count == 1
