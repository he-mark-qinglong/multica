"""Smoke tests for backtest outputs of vpvr_volume_edge_3tf_v1."""
from pathlib import Path

import pytest

RESULTS_DIR = Path(__file__).parent.parent / "results"


def test_summary_json_exists():
    assert (RESULTS_DIR / "summary.json").exists()


def test_metrics_json_exists():
    assert (RESULTS_DIR / "metrics.json").exists()


def test_trades_csv_exists():
    assert list(RESULTS_DIR.glob("trades_*.csv"))


def test_metrics_have_real_numbers():
    import json
    payload = json.loads((RESULTS_DIR / "metrics.json").read_text())
    assert "sharpe" in payload and isinstance(payload["sharpe"], (int, float))
    assert "max_drawdown" in payload and isinstance(payload["max_drawdown"], (int, float))
    assert "n_trades_total" in payload
    assert payload["n_trades_total"] >= 0
