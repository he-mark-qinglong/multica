"""Tests for dynamic_erc.py and stress_test.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.dynamic_erc import (
    DynamicERC, DynamicERCParams, DynamicERCResult,
)
from _shared.market_making.stress_test import (
    StressScenario, StressResult, SCENARIOS,
    run_stress_test, run_all_stress_tests,
)


# ---- Dynamic ERC ----

def _make_returns(n=200, seed=42):
    np.random.seed(seed)
    return pd.DataFrame({
        "A": np.random.normal(0.001, 0.02, n),
        "B": np.random.normal(0.0005, 0.015, n),
        "C": np.random.normal(0.002, 0.025, n),
    })

def test_dynamic_erc_insufficient_data():
    derc = DynamicERC(DynamicERCParams(min_lookback=100))
    rets = _make_returns(50)
    result = derc.update(rets)
    assert result is None

def test_dynamic_erc_produces_weights():
    derc = DynamicERC(DynamicERCParams(min_lookback=50, lookback=150, rebalance_freq=1))
    rets = _make_returns(100)
    result = derc.update(rets)
    assert result is not None
    assert len(result.weights) == 3
    assert sum(result.weights.values()) == pytest.approx(1.0, abs=0.05)

def test_dynamic_erc_crisis_detection():
    params = DynamicERCParams(
        min_lookback=50, lookback=150, rebalance_freq=1,
        crisis_corr_threshold=0.3,
    )
    derc = DynamicERC(params)
    # Create correlated returns
    base = np.random.normal(0, 0.02, 200)
    rets = pd.DataFrame({"A": base, "B": base * 0.95, "C": base * 0.9})
    result = derc.update(rets)
    assert result is not None
    assert result.is_crisis  # high correlation → crisis

def test_dynamic_erc_crisis_shrinkage():
    params = DynamicERCParams(
        min_lookback=50, lookback=150, rebalance_freq=1,
        crisis_corr_threshold=0.3, crisis_shrinkage=0.5,
    )
    derc = DynamicERC(params)
    base = np.random.normal(0, 0.02, 200)
    rets = pd.DataFrame({"A": base, "B": base * 0.95})
    result = derc.update(rets)
    if result.is_crisis:
        total_weight = sum(result.weights.values())
        assert total_weight < 1.0  # shrunk


# ---- Stress testing ----

def test_flash_crash_impact():
    result = run_stress_test(
        scenario=SCENARIOS["flash_crash"],
        capital_usd=10000.0,
        net_inventory_usd=5000.0,
        historical_pnl_bp=[1, -1, 2, -2, 1.5, -1.5] * 20,
    )
    assert result.pnl_impact_pct < 0  # lost money
    assert result.scenario == "Flash Crash"

def test_stress_survival():
    # Small inventory → should survive
    result = run_stress_test(
        scenario=SCENARIOS["flash_crash"],
        capital_usd=10000.0,
        net_inventory_usd=1000.0,  # small
        historical_pnl_bp=[1, -1, 2, -2] * 20,
        survival_threshold_pct=-0.25,
    )
    assert result.survival

def test_stress_failure():
    # Huge inventory → should fail
    result = run_stress_test(
        scenario=SCENARIOS["liquidation_cascade"],
        capital_usd=10000.0,
        net_inventory_usd=50000.0,  # huge
        historical_pnl_bp=[1, -1, 2, -2] * 20,
        survival_threshold_pct=-0.25,
    )
    assert not result.survival

def test_run_all_scenarios():
    results = run_all_stress_tests(
        capital_usd=10000.0,
        net_inventory_usd=3000.0,
        historical_pnl_bp=[1, -1, 2, -2] * 20,
    )
    assert len(results) == 5  # all 5 scenarios
    for r in results:
        assert isinstance(r, StressResult)
        assert r.scenario != ""
