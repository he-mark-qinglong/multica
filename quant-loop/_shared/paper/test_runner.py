"""W5/T9 — tests for the offline paper runner skeleton.

Spec source: ``docs/plans/infra-sprint-2026-07-25/round2/w5-s3-paper-harness.md``.

Conventions (mirrors ``_shared/test_run_backtest.py:44``):
    sys.path is augmented with the quant-loop repo root so we can do
    ``from _shared.paper.runner import ...``. Here, ``__file__`` is
    ``.../_shared/paper/test_runner.py`` → ``parents[2]`` is the
    ``quant-loop`` root.

All data is synthetic — no parquet reads, no network, no live data dir.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make the quant-loop repo importable.
_QUANT_LOOP_ROOT = Path(__file__).resolve().parents[2]
if str(_QUANT_LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(_QUANT_LOOP_ROOT))

from _shared.paper.runner import (  # noqa: E402
    ConfigError,
    evaluate_kill,
    load_config,
    load_state,
    run,
    save_state,
)
from _shared.paper.ledger_writer import DAILY_FIELDS  # noqa: E402


# --- Synthetic data factories ------------------------------------------------
def _make_synthetic_inputs(tmp_path: Path, *, n_bars_day1: int = 20,
                            n_bars_day2: int = 10,
                            bar_freq_min: int = 30):
    """Build a 2-day fixture: 30 30-minute bars + 4 trades (2 wins, 2 losses).

    Day 1 = 2026-07-20 (20 bars at 30m), Day 2 = 2026-07-21 (10 bars at 30m).
    Bars are placed with a UTC date boundary so the runner's `groupby(date)`
    sees two distinct dates — otherwise all bars land on the same UTC day
    and the ledger collapses to a single row.
    """
    rng = np.random.default_rng(seed=20260725)
    n_bars = n_bars_day1 + n_bars_day2
    # Deterministic random-walk close.
    rets = rng.normal(loc=0.0005, scale=0.005, size=n_bars)
    close = 100.0 * np.cumprod(1.0 + rets)

    bar_delta = pd.Timedelta(minutes=bar_freq_min)
    day1_start = pd.Timestamp("2026-07-20T00:00:00Z")
    # Day 2 starts at a fresh UTC date (not day1_start + 20*30min, which would
    # still land on 2026-07-20).
    day2_start = pd.Timestamp("2026-07-21T00:00:00Z")
    ts_index = (
        [day1_start + i * bar_delta for i in range(n_bars_day1)]
        + [day2_start + i * bar_delta for i in range(n_bars_day2)]
    )

    bars = pd.DataFrame({"ts": ts_index, "close": close})
    bars_csv = tmp_path / "bars.csv"
    bars.to_csv(bars_csv, index=False)

    # 4 trades. Hand-picked entries/exits so 2 win (long) and 2 lose (short).
    # Use the actual close prices to make sign predictable.
    def _ts_at(i: int) -> pd.Timestamp:
        return ts_index[i]

    trades = pd.DataFrame(
        {
            "entry_ts": [_ts_at(1), _ts_at(5), _ts_at(15), _ts_at(22)],
            "exit_ts":  [_ts_at(8), _ts_at(12), _ts_at(18), _ts_at(26)],
            "direction": ["long", "short", "long", "short"],
            "size_fraction": [1.0, 1.0, 1.0, 1.0],
        }
    )
    trades_csv = tmp_path / "trades.csv"
    trades.to_csv(trades_csv, index=False)

    return bars_csv, trades_csv, ts_index


def _base_config(path: Path, *, rolling_sharpe_floor: float = 0.0) -> Path:
    """Minimal valid config; can be tweaked per-test."""
    cfg = {
        "strategy_id": "test_runner_skeleton",
        "timeframe": "30m",
        "starting_capital_usd": 100_000.0,
        "cost_bps_rt": 0.0,           # zero cost for deterministic tests
        "freq_per_year": 365 * 24 * 2,  # 30m bars
        "backtest_expectations": {
            "backtest_max_dd_pct": 5.0,
        },
        "kill_criteria": {
            "min_trades_before_kill_check": 100,   # high → rule 1 dormant in test
            "min_live_profit_factor": 1.0,
            "max_drawdown_multiple_vs_backtest": 1.5,
            "rolling_20d_sharpe_floor": rolling_sharpe_floor,
        },
    }
    cfg_path = path / "config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))
    return cfg_path


# --- Tests -------------------------------------------------------------------
def test_first_run_writes_ledger_and_state(tmp_path: Path) -> None:
    """First run on a fresh run_dir: exit 0, 1 header + 2 data rows, state advanced."""
    bars_csv, trades_csv, ts_index = _make_synthetic_inputs(tmp_path)
    cfg_path = _base_config(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    rc = run(cfg_path, bars_csv, trades_csv, run_dir)
    assert rc == 0, f"first run should be clean, got exit={rc}"

    csv_path = run_dir / "results-ledger" / "daily_metrics.csv"
    assert csv_path.exists(), "ledger file must exist after first run"

    df = pd.read_csv(csv_path)
    # Header + 2 data rows = 2 lines of data + 1 header.
    n_data_rows = len(df)
    assert n_data_rows == 2, (
        f"first run should write exactly 2 data rows (day1+day2); got {n_data_rows}"
    )
    # Two distinct dates.
    assert set(df["date"].astype(str)) == {"2026-07-20", "2026-07-21"}
    # All DAILY_FIELDS columns present (header not glued to first data row).
    raw_text = csv_path.read_text()
    first_newline = raw_text.index("\n")
    header_line = raw_text[:first_newline]
    assert header_line.count("date,") == 1, (
        f"header should contain exactly one 'date,' field; got: {header_line!r}"
    )

    state = json.loads((run_dir / "state.json").read_text())
    assert state["last_date"] == "2026-07-21", state
    assert state["killed"] is False, state
    assert state["kill_reason"] == "", state

    # DAILY_FIELDS contract: every column appears in the CSV header.
    header_cols = [c.strip() for c in header_line.split(",")]
    assert header_cols == DAILY_FIELDS, (
        f"ledger header mismatch.\n  got:      {header_cols}\n  expected: {DAILY_FIELDS}"
    )

    _ = ts_index  # silence unused


def test_resume_is_idempotent(tmp_path: Path) -> None:
    """Re-running with same inputs must NOT duplicate rows."""
    bars_csv, trades_csv, _ = _make_synthetic_inputs(tmp_path)
    cfg_path = _base_config(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    rc1 = run(cfg_path, bars_csv, trades_csv, run_dir)
    assert rc1 == 0
    state1 = json.loads((run_dir / "state.json").read_text())
    csv_path = run_dir / "results-ledger" / "daily_metrics.csv"
    rows1 = pd.read_csv(csv_path)
    n1 = len(rows1)
    assert n1 == 2, f"first run rows={n1}, expected 2"

    rc2 = run(cfg_path, bars_csv, trades_csv, run_dir)
    assert rc2 == 0, f"resume should be a no-op (clean exit); got exit={rc2}"
    rows2 = pd.read_csv(csv_path)
    n2 = len(rows2)
    assert n2 == n1, (
        f"resume must not duplicate rows; first={n1} second={n2}"
    )
    state2 = json.loads((run_dir / "state.json").read_text())
    assert state1 == state2, (
        f"state must be unchanged after a no-op resume.\n"
        f"  before: {state1}\n  after:  {state2}"
    )


def test_kill_latches(tmp_path: Path) -> None:
    """rolling_20d_sharpe_floor=999 triggers rule 3; re-run still exit 2, no new rows."""
    bars_csv, trades_csv, _ = _make_synthetic_inputs(tmp_path)
    cfg_path = _base_config(tmp_path, rolling_sharpe_floor=999.0)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    rc1 = run(cfg_path, bars_csv, trades_csv, run_dir)
    assert rc1 == 2, f"kill rule 3 must trip; got exit={rc1}"

    csv_path = run_dir / "results-ledger" / "daily_metrics.csv"
    df = pd.read_csv(csv_path)
    # day1 always fails the rolling-sharpe floor (rolling is 0.0 < 999.0).
    halted = df[df["kill_triggered"].astype(bool)]
    assert len(halted) >= 1, "at least one row must show kill_triggered=True"
    assert set(halted["action"].astype(str)) == {"HALT"}, (
        f"halted rows must have action=HALT; got {halted['action'].tolist()}"
    )
    # Every halted row has a non-empty kill_reason.
    for reason in halted["kill_reason"].astype(str):
        assert "rolling_20d_sharpe" in reason, (
            f"kill_reason should name the rule; got: {reason!r}"
        )

    rows_after_first = len(df)

    # Resume onto a killed run must also exit 2 with no new rows.
    rc2 = run(cfg_path, bars_csv, trades_csv, run_dir)
    assert rc2 == 2, f"latched kill must keep returning 2; got exit={rc2}"
    rows_after_resume = len(pd.read_csv(csv_path))
    assert rows_after_resume == rows_after_first, (
        f"latched kill must not append new rows; "
        f"first={rows_after_first} resume={rows_after_resume}"
    )
    state = json.loads((run_dir / "state.json").read_text())
    assert state["killed"] is True
    assert state["kill_reason"] != ""


def test_missing_config_key_raises(tmp_path: Path) -> None:
    """Removing a required kill_criteria key must raise KeyError naming the key."""
    cfg = {
        "strategy_id": "missing_key_test",
        "timeframe": "30m",
        "starting_capital_usd": 100_000.0,
        "cost_bps_rt": 0.0,
        "freq_per_year": 365 * 24 * 2,
        "backtest_expectations": {"backtest_max_dd_pct": 5.0},
        "kill_criteria": {
            # min_live_profit_factor deliberately omitted.
            "min_trades_before_kill_check": 100,
            "max_drawdown_multiple_vs_backtest": 1.5,
            "rolling_20d_sharpe_floor": 0.0,
        },
    }
    cfg_path = tmp_path / "bad_config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))

    with pytest.raises(KeyError) as ei:
        load_config(cfg_path)
    # Message must contain the missing dotted key path.
    assert "min_live_profit_factor" in str(ei.value), (
        f"KeyError message must name the missing key; got: {ei.value!r}"
    )
    # ConfigError subclasses KeyError — should be the same type for callers.
    assert isinstance(ei.value, ConfigError)