"""Tests for the strategy contract v2 template + generic runner.

Phase D acceptance: the example strategy passes the contract check and
``run_strategy`` produces the 9-key metrics schema from
``_shared/validation/compute_metrics.py``.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # quant-loop/

from _shared.run_backtest import Trade  # noqa: E402
from _shared.templates import example_strategy  # noqa: E402
from _shared.templates.run_strategy import (  # noqa: E402
    infer_freq_per_year,
    run_strategy,
)
from _shared.templates.strategy_contract_v2 import (  # noqa: E402
    ContractError,
    check_contract,
    make_synthetic_bars,
    validate_module_signature,
    validate_trades,
)

NINE_KEYS = {
    "sharpe_daily",
    "annualized_return",
    "max_drawdown_pct",
    "profit_factor",
    "n_trades",
    "n_bars",
    "win_rate",
    "calmar",
    "sortino",
}


# ---------------------------------------------------------------------------
# Contract: example strategy passes.
# ---------------------------------------------------------------------------
def test_example_strategy_passes_contract_check():
    report = check_contract(example_strategy)
    assert report["ok"] is True
    assert report["n_trades"] is not None and report["n_trades"] > 0


def test_example_strategy_returns_valid_trades():
    bars = make_synthetic_bars(["SYNTH"], n_bars=500)
    trades = example_strategy.generate_signals(bars, dict(example_strategy.DEFAULT_CONFIG))
    validated = validate_trades(trades, bars["SYNTH"].index)
    assert len(validated) == len(trades)
    assert all(isinstance(t, Trade) for t in validated)


# ---------------------------------------------------------------------------
# Contract: violations are rejected.
# ---------------------------------------------------------------------------
def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def test_missing_generate_signals_fails():
    with pytest.raises(ContractError, match="generate_signals"):
        validate_module_signature(_module("m1"))


def test_wrong_param_names_fail():
    def generate_signals(data, cfg):
        return []

    with pytest.raises(ContractError, match="'bars'"):
        validate_module_signature(_module("m2", generate_signals=generate_signals))


def test_too_few_params_fail():
    def generate_signals(bars):
        return []

    with pytest.raises(ContractError, match="bars, config"):
        validate_module_signature(_module("m3", generate_signals=generate_signals))


def test_non_list_return_fails():
    def generate_signals(bars, config):
        return {"not": "a list"}

    mod = _module("m4", generate_signals=generate_signals,
                  DEFAULT_CONFIG={"symbol": "SYNTH"})
    with pytest.raises(ContractError, match="list of Trade"):
        check_contract(mod)


def test_invalid_direction_fails():
    def generate_signals(bars, config):
        idx = bars["SYNTH"].index
        return [Trade(entry_ts=idx[10], exit_ts=idx[20], direction="sideways")]

    mod = _module("m5", generate_signals=generate_signals,
                  DEFAULT_CONFIG={"symbol": "SYNTH"})
    with pytest.raises(ContractError, match="direction"):
        check_contract(mod)


def test_exit_before_entry_fails():
    def generate_signals(bars, config):
        idx = bars["SYNTH"].index
        return [Trade(entry_ts=idx[20], exit_ts=idx[10], direction="long")]

    mod = _module("m6", generate_signals=generate_signals,
                  DEFAULT_CONFIG={"symbol": "SYNTH"})
    with pytest.raises(ContractError, match="after entry_ts"):
        check_contract(mod)


def test_off_bar_timestamp_fails():
    def generate_signals(bars, config):
        idx = bars["SYNTH"].index
        off = idx[10] + pd.Timedelta(seconds=1)
        return [Trade(entry_ts=off, exit_ts=idx[20], direction="long")]

    mod = _module("m7", generate_signals=generate_signals,
                  DEFAULT_CONFIG={"symbol": "SYNTH"})
    with pytest.raises(ContractError, match="not on a bar"):
        check_contract(mod)


def test_bad_size_fraction_fails():
    def generate_signals(bars, config):
        idx = bars["SYNTH"].index
        return [Trade(entry_ts=idx[10], exit_ts=idx[20], direction="long",
                      size_fraction=1.5)]

    mod = _module("m8", generate_signals=generate_signals,
                  DEFAULT_CONFIG={"symbol": "SYNTH"})
    with pytest.raises(ContractError, match="size_fraction"):
        check_contract(mod)


def test_structural_only_check_does_not_run():
    called = {"n": 0}

    def generate_signals(bars, config):
        called["n"] += 1
        return []

    mod = _module("m9", generate_signals=generate_signals)
    report = check_contract(mod, run_synthetic=False)
    assert report["ok"] is True
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# run_strategy: end-to-end on synthetic bars -> 9-key metrics.
# ---------------------------------------------------------------------------
def test_run_strategy_produces_nine_key_metrics():
    bars = make_synthetic_bars(["SYNTH"], n_bars=1000)
    strategy_path = Path(example_strategy.__file__)
    out = run_strategy(
        strategy_path,
        {"symbol": "SYNTH"},
        bars=bars,
        cost_bps_rt=24.0,
        freq_per_year=365 * 24,
    )
    assert set(out["metrics"].keys()) == NINE_KEYS
    assert out["n_trades"] > 0
    assert out["n_skipped"] == 0
    assert isinstance(out["equity"], pd.Series)
    assert len(out["equity"]) == len(bars["SYNTH"])
    # equity walk starts at initial capital and stays finite/positive
    assert out["equity"].iloc[0] == pytest.approx(100_000.0)
    assert (out["equity"] > 0).all()


def test_run_strategy_respects_config_overrides():
    bars = make_synthetic_bars(["SYNTH"], n_bars=1000)
    strategy_path = Path(example_strategy.__file__)
    tight = run_strategy(strategy_path, {"symbol": "SYNTH", "entry_lookback": 5},
                         bars=bars)
    loose = run_strategy(strategy_path, {"symbol": "SYNTH", "entry_lookback": 100},
                         bars=bars)
    # shorter lookback -> more breakouts -> more trades
    assert tight["n_trades"] > loose["n_trades"]


def test_infer_freq_per_year():
    idx = pd.date_range("2026-01-01", periods=100, freq="1h")
    assert infer_freq_per_year(idx) == 365 * 24
    idx_1m = pd.date_range("2026-01-01", periods=100, freq="1min")
    assert infer_freq_per_year(idx_1m) == 365 * 24 * 60
