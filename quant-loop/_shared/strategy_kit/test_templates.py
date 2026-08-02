"""Contract + behaviour tests for the five strategy templates (A11)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from _shared.run_backtest import run_backtest
from _shared.templates.strategy_contract_v2 import (
    check_contract,
    make_synthetic_bars,
    validate_module_signature,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAMES = [
    "funding_carry_template",
    "momentum_template",
    "mean_reversion_template",
    "hedged_grid_template",
    "meta_label_template",
]


def _load(name: str):
    """Import a template's strategy.py by file path (templates are not a
    package — they are meant to be copied)."""
    path = TEMPLATES_DIR / name / "strategy.py"
    spec = importlib.util.spec_from_file_location(f"template_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_five_templates_present():
    for name in TEMPLATE_NAMES:
        d = TEMPLATES_DIR / name
        assert (d / "strategy.py").is_file(), name
        assert (d / "config.json").is_file(), name
        assert (d / "README.md").is_file(), name
        assert (d / "README.md").read_text().strip()  # one-liner, non-empty


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_contract_synthetic_smoke(name):
    module = _load(name)
    report = check_contract(module, run_synthetic=True, n_bars=400)
    assert report["ok"] is True


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_config_json_matches_default_config(name):
    module = _load(name)
    cfg = json.loads((TEMPLATES_DIR / name / "config.json").read_text())
    for key, value in module.DEFAULT_CONFIG.items():
        assert key in cfg, f"{name}: config.json missing '{key}'"
        assert cfg[key] == value, f"{name}: config.json['{key}'] diverged"


# ---------------------------------------------------------------------------
# Per-template behaviour
# ---------------------------------------------------------------------------
def _trending_bars(n: int = 400) -> dict:
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 + np.cumsum(np.full(n, 0.05) + np.random.default_rng(3)
                            .normal(0, 0.1, n))
    df = pd.DataFrame({"open": close, "high": close * 1.001,
                       "low": close * 0.999, "close": close,
                       "volume": np.full(n, 1000.0)}, index=idx)
    return {"SYNTH": df}


def test_momentum_template_trades_on_trend():
    module = _load("momentum_template")
    trades = module.generate_signals(_trending_bars(), dict(module.DEFAULT_CONFIG))
    assert len(trades) >= 1
    assert all(t.direction == "long" for t in trades)


def test_mean_reversion_template_trades_on_oscillation():
    n = 400
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 + 3 * np.sin(np.arange(n) / 5.0)
    df = pd.DataFrame({"open": close, "high": close + 0.2, "low": close - 0.2,
                       "close": close, "volume": np.full(n, 1000.0)},
                      index=idx)
    module = _load("mean_reversion_template")
    trades = module.generate_signals({"SYNTH": df}, dict(module.DEFAULT_CONFIG))
    assert len(trades) >= 4  # repeated fades on a clean sine wave


def test_funding_carry_shorts_positive_funding():
    bars = _trending_bars()
    bars["SYNTH"]["funding"] = 0.001  # persistently high positive
    module = _load("funding_carry_template")
    trades = module.generate_signals(bars, dict(module.DEFAULT_CONFIG))
    assert len(trades) >= 5
    assert all(t.direction == "short" for t in trades)


def test_funding_carry_longs_negative_funding_and_flat_without_column():
    module = _load("funding_carry_template")
    bars = _trending_bars()
    bars["SYNTH"]["funding"] = -0.001
    trades = module.generate_signals(bars, dict(module.DEFAULT_CONFIG))
    assert trades and all(t.direction == "long" for t in trades)
    # no funding column -> no signal at all
    assert module.generate_signals(_trending_bars(),
                                   dict(module.DEFAULT_CONFIG)) == []


def test_hedged_grid_trades_in_range_and_non_overlapping_per_leg():
    n = 400
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 + 5 * np.sin(np.arange(n) / 8.0)
    df = pd.DataFrame({"open": close, "high": close + 0.3, "low": close - 0.3,
                       "close": close, "volume": np.full(n, 1000.0)},
                      index=idx)
    module = _load("hedged_grid_template")
    trades = module.generate_signals({"SYNTH": df}, dict(module.DEFAULT_CONFIG))
    assert len(trades) >= 4
    for leg in ("long", "short"):
        leg_trades = sorted((t for t in trades if t.direction == leg),
                            key=lambda t: t.entry_ts)
        for a, b in zip(leg_trades, leg_trades[1:]):
            assert b.entry_ts >= a.exit_ts  # one open trade per leg


def test_meta_label_template_exits_within_vertical_barrier():
    module = _load("meta_label_template")
    bars = make_synthetic_bars(["SYNTH"], n_bars=400, seed=5)
    cfg = dict(module.DEFAULT_CONFIG)
    trades = module.generate_signals(bars, cfg)
    idx = bars["SYNTH"].index
    assert len(trades) >= 1
    for t in trades:
        assert idx.get_loc(t.exit_ts) - idx.get_loc(t.entry_ts) <= cfg["max_bars"]


# ---------------------------------------------------------------------------
# Equity-walk integration: every template runs through the shared engine
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_template_runs_through_shared_backtest_engine(name):
    module = _load(name)
    bars = make_synthetic_bars(["SYNTH"], n_bars=400, seed=9)
    if name == "funding_carry_template":
        bars["SYNTH"]["funding"] = 0.001
    trades = module.generate_signals(bars, dict(module.DEFAULT_CONFIG))
    result = run_backtest(bars["SYNTH"], trades, cost_mode="fill")
    assert "equity" in result and len(result["equity"]) == len(bars["SYNTH"])


def test_validate_module_signature_on_all():
    for name in TEMPLATE_NAMES:
        validate_module_signature(_load(name))
