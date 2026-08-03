"""Data health monitor — real-time freshness and completeness monitoring.

Checks all data assets for staleness, gaps, and quality issues.
Designed to run as a cron job and report to the alerting system.

Usage:
    monitor = DataHealthMonitor()
    report = monitor.check_all()
    if report.stale_assets:
        print(f"STALE: {report.stale_assets}")
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

import pandas as pd


@dataclass
class AssetHealth:
    """Health status of a single data asset."""
    name: str
    path: str
    exists: bool
    last_update: datetime | None
    age_hours: float | None
    row_count: int
    expected_freq: str  # "15m", "4h", "8h" etc.
    is_stale: bool
    staleness_reason: str = ""


@dataclass
class HealthReport:
    """Complete data health report."""
    checked_at: str
    assets: List[AssetHealth] = field(default_factory=list)

    @property
    def stale_assets(self) -> List[AssetHealth]:
        return [a for a in self.assets if a.is_stale]

    @property
    def missing_assets(self) -> List[AssetHealth]:
        return [a for a in self.assets if not a.exists]

    @property
    def healthy_count(self) -> int:
        return sum(1 for a in self.assets if a.exists and not a.is_stale)

    def summary(self) -> str:
        return (f"Data health: {self.healthy_count} healthy, "
                f"{len(self.stale_assets)} stale, "
                f"{len(self.missing_assets)} missing "
                f"(of {len(self.assets)} total)")


# Default data assets to monitor
DEFAULT_ASSETS = [
    {"name": "BTCUSDT_15m", "pattern": "data/perp_15m/BTCUSDT_15m.parquet", "max_age_hours": 24},
    {"name": "ETHUSDT_15m", "pattern": "data/perp_15m/ETHUSDT_15m.parquet", "max_age_hours": 24},
    {"name": "SOLUSDT_15m", "pattern": "data/perp_15m/SOLUSDT_15m.parquet", "max_age_hours": 24},
    {"name": "funding_BTCUSDT", "pattern": "data/funding/funding_BTCUSDT.json", "max_age_hours": 8},
    {"name": "liq_BTCUSDT", "pattern": "data/liquidations/BTCUSDT.jsonl", "max_age_hours": 1},
    {"name": "liq_ETHUSDT", "pattern": "data/liquidations/ETHUSDT.jsonl", "max_age_hours": 1},
    {"name": "liq_SOLUSDT", "pattern": "data/liquidations/SOLUSDT.jsonl", "max_age_hours": 1},
]


class DataHealthMonitor:
    """Monitors data asset freshness and completeness."""

    def __init__(self, data_root: str | Path = ".", assets: list | None = None):
        self.data_root = Path(data_root)
        self.assets_config = assets or DEFAULT_ASSETS

    def check_all(self) -> HealthReport:
        """Check health of all configured data assets."""
        report = HealthReport(
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

        for cfg in self.assets_config:
            asset = self._check_asset(cfg)
            report.assets.append(asset)

        return report

    def _check_asset(self, cfg: dict) -> AssetHealth:
        """Check health of a single data asset."""
        path = self.data_root / cfg["pattern"]
        max_age = cfg["max_age_hours"]

        if not path.exists():
            return AssetHealth(
                name=cfg["name"], path=str(path), exists=False,
                last_update=None, age_hours=None, row_count=0,
                expected_freq="", is_stale=True, staleness_reason="file not found",
            )

        # Determine last update and row count based on file type
        last_update = None
        row_count = 0

        try:
            if path.suffix == ".parquet":
                df = pd.read_parquet(path)
                row_count = len(df)
                # Check for timestamp column
                for ts_col in ["open_time", "ts", "timestamp", "ts_ns"]:
                    if ts_col in df.columns:
                        ts_series = df[ts_col]
                        if ts_col == "ts_ns":
                            last_val = ts_series.iloc[-1]
                            last_update = datetime.fromtimestamp(last_val / 1e9, tz=timezone.utc)
                        elif ts_series.max() > 1e12:  # milliseconds
                            last_update = datetime.fromtimestamp(ts_series.iloc[-1] / 1000, tz=timezone.utc)
                        elif ts_series.max() > 1e9:  # seconds
                            last_update = datetime.fromtimestamp(ts_series.iloc[-1], tz=timezone.utc)
                        break
            elif path.suffix == ".jsonl":
                lines = path.read_text().strip().split("\n")
                row_count = len(lines)
                if lines and lines[0].strip():
                    last_line = json.loads(lines[-1])
                    for ts_key in ["ts", "timestamp", "ts_ns"]:
                        if ts_key in last_line:
                            ts_val = last_line[ts_key]
                            if ts_val > 1e12:
                                last_update = datetime.fromtimestamp(ts_val / 1000, tz=timezone.utc)
                            elif ts_val > 1e9:
                                last_update = datetime.fromtimestamp(ts_val, tz=timezone.utc)
                            break
            elif path.suffix == ".json":
                data = json.loads(path.read_text())
                if isinstance(data, list) and data:
                    row_count = len(data)
                    last_item = data[-1]
                    for ts_key in ["fundingTime", "ts", "timestamp"]:
                        if ts_key in last_item:
                            ts_val = last_item[ts_key]
                            if ts_val > 1e12:
                                last_update = datetime.fromtimestamp(ts_val / 1000, tz=timezone.utc)
                            break
        except Exception as e:
            return AssetHealth(
                name=cfg["name"], path=str(path), exists=True,
                last_update=None, age_hours=None, row_count=0,
                expected_freq="", is_stale=True,
                staleness_reason=f"error reading: {e}",
            )

        # Check staleness
        now = datetime.now(timezone.utc)
        age_hours = (now - last_update).total_seconds() / 3600 if last_update else None
        is_stale = age_hours is not None and age_hours > max_age

        return AssetHealth(
            name=cfg["name"], path=str(path), exists=True,
            last_update=last_update, age_hours=age_hours,
            row_count=row_count, expected_freq=cfg.get("freq", ""),
            is_stale=is_stale,
            staleness_reason=f"age {age_hours:.1f}h > {max_age}h" if is_stale else "",
        )
