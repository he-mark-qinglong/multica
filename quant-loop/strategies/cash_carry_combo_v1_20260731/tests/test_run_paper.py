"""Tests for run_paper.py — the paper-runner adapter for cash_carry_combo_v1."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")
sys.path.insert(0, "/Users/mark/multica/quant-loop/strategies/cash_carry_combo_v1_20260731")

import json

import pandas as pd
import pytest

from _shared.paper.runner import REQUIRED_CONFIG_KEYS, load_config, run as paper_run
from run_paper import (
    DEFAULT_KILL_CRITERIA,
    bars_from_equity,
    build_paper_config,
    combo_equity_bp,
    trades_from_index,
)
from strategy import CarryConfig


def _frame(fund_bp, basis_bp, start="2026-07-19", freq="8h"):
    """Synthetic load_symbol_data-shaped frame (ts, fund_bp, basis_bp)."""
    idx = pd.date_range(start, periods=len(fund_bp), freq=freq, tz="UTC")
    return pd.DataFrame({"ts": idx, "fund_bp": fund_bp, "basis_bp": basis_bp})


def test_combo_equity_equal_weight_mean():
    """Combo = mean of per-symbol curves; no filter symbol accumulates funding."""
    cfg = CarryConfig(symbols=("AAA", "BBB"), leverage=1.0, filter_symbols=())
    frames = {
        "AAA": _frame([1.0] * 6, [0.0] * 6),
        "BBB": _frame([3.0] * 6, [0.0] * 6),
    }
    eq = combo_equity_bp(frames, cfg)
    # Costs are zeroed via CarryConfig defaults? entry_cost_bp = (5+10)*1 = 15bp
    # per side → 30bp round trip subtracted. Mean of cumsum(1) & cumsum(3)
    # minus 30bp: at t=0 mean(1,3)-30 = -28.
    assert eq.iloc[0] == pytest.approx(2.0 - 30.0)
    assert eq.iloc[-1] == pytest.approx(2.0 * 6 - 30.0)
    assert isinstance(eq.index, pd.DatetimeIndex)


def test_combo_equity_filter_flattens_negative_funding():
    """Regime-filtered symbol goes flat when trailing funding sum <= 0."""
    cfg = CarryConfig(symbols=("FLT",), leverage=1.0, filter_symbols=("FLT",),
                      filter_window_events=3, perp_fee_bp=0.0, spot_fee_bp=0.0)
    frames = {"FLT": _frame([-1.0] * 8, [0.0] * 8)}
    eq = combo_equity_bp(frames, cfg)
    # Funding always negative → trailing sum <= 0 → inactive → zero income,
    # and basis is flat → equity stays at 0 (no costs configured).
    assert (eq == 0.0).all()


def test_bars_from_equity_ratio():
    idx = pd.date_range("2026-07-20", periods=3, freq="8h", tz="UTC")
    eq = pd.Series([0.0, 100.0, -50.0], index=idx)
    bars = bars_from_equity(eq)
    assert list(bars.columns) == ["ts", "close"]
    assert bars["close"].tolist() == pytest.approx([1.0, 1.01, 0.995])


def test_trades_tile_funding_cycles():
    idx = pd.date_range("2026-07-20", periods=5, freq="8h", tz="UTC")
    trades = trades_from_index(idx)
    assert len(trades) == 4
    assert (trades["direction"] == "long").all()
    assert (trades["size_fraction"] == 1.0).all()
    # exit of cycle i == entry of cycle i+1 (contiguous tiling)
    assert list(trades["exit_ts"][:-1]) == list(trades["entry_ts"][1:])
    # all entries/exits are on the bar grid
    assert set(trades["entry_ts"]) | set(trades["exit_ts"]) <= set(idx)


def test_build_paper_config_has_all_required_keys(tmp_path):
    cfg = build_paper_config(-0.2072)
    # Validate against the runner's own contract, not a copy of it.
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(cfg))
    loaded = load_config(cfg_path)  # raises ConfigError on any missing key
    assert loaded["kill_criteria"] == DEFAULT_KILL_CRITERIA
    assert loaded["backtest_expectations"]["backtest_max_dd_pct"] == pytest.approx(0.2072)
    for dotted in REQUIRED_CONFIG_KEYS:
        section, _, key = dotted.partition(".")
        assert section in loaded
        if key:
            assert key in loaded[section]


def test_end_to_end_runner_idempotent(tmp_path):
    """Synthetic 2-symbol book across 3 UTC days → runner writes daily rows;
    resume adds none."""
    cfg = CarryConfig(symbols=("AAA", "BBB"), leverage=1.0, filter_symbols=(),
                      perp_fee_bp=0.0, spot_fee_bp=0.0)
    # 9 events at 8h = exactly 3 UTC days (2026-07-20/21/22).
    frames = {
        "AAA": _frame([1.0] * 9, [0.0] * 9, start="2026-07-20"),
        "BBB": _frame([2.0] * 9, [0.0] * 9, start="2026-07-20"),
    }
    eq = combo_equity_bp(frames, cfg)
    bars = bars_from_equity(eq)
    trades = trades_from_index(eq.index)
    paper_cfg = build_paper_config(-0.05)

    bars_csv = tmp_path / "bars.csv"
    trades_csv = tmp_path / "trades.csv"
    cfg_path = tmp_path / "cfg.json"
    bars.to_csv(bars_csv, index=False)
    trades.to_csv(trades_csv, index=False)
    cfg_path.write_text(json.dumps(paper_cfg))

    run_dir = tmp_path / "run"
    rc = paper_run(cfg_path, bars_csv, trades_csv, run_dir)
    assert rc == 0
    df = pd.read_csv(run_dir / "results-ledger" / "daily_metrics.csv")
    assert len(df) == 3  # one row per UTC date
    # Equity must rise monotonically: funding income only, no costs.
    assert df["equity_usd"].is_monotonic_increasing
    state = json.loads((run_dir / "state.json").read_text())
    assert state["killed"] is False
    assert state["last_date"] == "2026-07-22"

    # Idempotent resume: same inputs, no new rows.
    rc2 = paper_run(cfg_path, bars_csv, trades_csv, run_dir)
    assert rc2 == 0
    assert len(pd.read_csv(run_dir / "results-ledger" / "daily_metrics.csv")) == 3
