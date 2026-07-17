"""Smoke tests for backtest outputs."""
from pathlib import Path

import pytest

RESULTS_DIR = Path(__file__).parent.parent / "results"


def test_summary_json_exists():
    assert (RESULTS_DIR / "summary.json").exists()


def test_metrics_json_exists():
    assert (RESULTS_DIR / "metrics.json").exists()


def test_trades_csv_exists():
    assert list(RESULTS_DIR.glob("trades_*.csv"))
