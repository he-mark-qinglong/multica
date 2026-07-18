"""Smoke tests for B3 backtest artifacts of vol_breakout_vpvr_val_fade_1h_5m_20260714.

Verifies that results/v10/{summary.json, metrics.json, trades_BTCUSDT.csv,
equity_BTCUSDT.csv} exist and have the minimum contract shape required
to mark B3 backtest as done.

This test does NOT touch strategy source. It only inspects artifacts
written to results/v10/.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "v10"


def test_summary_json_exists_and_shape():
    p = RESULTS / "summary.json"
    assert p.exists(), f"missing {p}"
    data = json.loads(p.read_text())
    assert data["strategy_key"] == "vol_breakout_vpvr_val_fade_1h_5m_20260714"
    assert data["variant"] == "V10"
    assert data["timeframe"] == "5m"
    assert data["filter_timeframe"] == "1h"
    assert "BTCUSDT" in data["per_symbol"]
    per = data["per_symbol"]["BTCUSDT"]
    for k in ("sharpe", "annualised_pct", "profit_factor",
              "max_drawdown_pct", "n_trades", "win_rate", "total_return_pct"):
        assert k in per, f"missing per_symbol.BTCUSDT.{k}"
    assert "verdict" in data
    assert data["verdict"] in ("PROFITABLE", "NOT-PROFITABLE")


def test_metrics_json_exists_and_shape():
    p = RESULTS / "metrics.json"
    assert p.exists(), f"missing {p}"
    data = json.loads(p.read_text())
    assert data["iteration"] == 74
    assert "walk_forward" in data
    wf = data["walk_forward"]
    assert wf["n_folds"] == 5
    assert "oos_sharpe_mean" in wf
    assert "folds" in wf
    assert isinstance(wf["folds"], list)
    assert len(wf["folds"]) == 5


def test_trades_csv_exists_and_schema():
    p = RESULTS / "trades_BTCUSDT.csv"
    assert p.exists(), f"missing {p}"
    with p.open() as fh:
        rows = list(csv.DictReader(fh))
    # V10 produced 6 trades total.
    assert len(rows) >= 1
    required = {"variant", "symbol", "direction", "entry_fill_date",
                "entry_price", "exit_fill_date", "exit_price",
                "pnl_usd", "pnl_pct", "exit_reason"}
    got = set(rows[0].keys())
    missing = required - got
    assert not missing, f"missing trade columns: {sorted(missing)}"
    for r in rows:
        assert r["symbol"] == "BTCUSDT"
        assert r["direction"] == "long"
