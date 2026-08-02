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


# ---- Regression: Bug 1 — negative mean correlation must NOT trigger crisis ----

def test_dynamic_erc_negative_corr_not_crisis():
    """Mean correlation -0.8 is excellent diversification, not a crisis.

    Regression for _detect_crisis using abs(mean_corr) > threshold,
    which misclassified strong negative correlation as a crisis.
    """
    params = DynamicERCParams(
        min_lookback=50, lookback=150, rebalance_freq=1,
        crisis_corr_threshold=0.3,
    )
    derc = DynamicERC(params)
    # Two assets with strong negative correlation (~-0.8)
    np.random.seed(99)
    base = np.random.normal(0, 0.02, 200)
    noise = np.random.normal(0, 0.005, 200)
    rets = pd.DataFrame({"A": base + noise, "B": -base + noise})
    result = derc.update(rets)
    assert result is not None
    assert result.mean_correlation < 0  # diversified
    assert not result.is_crisis  # negative corr → no crisis


# ---- Regression: Bug 3 — correlation_override must amplify VaR ----

def test_corr_breakdown_amplifies_var():
    """corr_breakdown scenario must produce higher VaR with n_strategies > 1.

    Regression: correlation_override was a dead parameter that
    run_stress_test never read.
    """
    pnl = [1, -1, 2, -2] * 20

    # n_strategies=1: correlation_override has no diversification to remove
    result_single = run_stress_test(
        scenario=SCENARIOS["corr_breakdown"],
        capital_usd=10000.0,
        net_inventory_usd=0.0,  # no inventory, isolate VaR channel
        historical_pnl_bp=pnl,
        n_strategies=1,
    )

    # n_strategies=5: diversification collapses, VaR amplified
    result_multi = run_stress_test(
        scenario=SCENARIOS["corr_breakdown"],
        capital_usd=10000.0,
        net_inventory_usd=0.0,
        historical_pnl_bp=pnl,
        n_strategies=5,
    )

    # With 5 strategies at correlation_override=0.9:
    # vol_amplification = sqrt(1 + 4*0.9) = sqrt(4.6) ≈ 2.145
    assert result_multi.var_after_bp > result_single.var_after_bp
    expected_ratio = (result_multi.var_after_bp / result_single.var_after_bp)
    assert 2.0 < expected_ratio < 2.3  # ≈ √4.6


# ---- Ledoit-Wolf covariance shrinkage (SMA-36941) ----

from _shared.market_making.dynamic_erc import ledoit_wolf_cov
from _shared.market_making.portfolio_risk import erc_weights


def test_lw_delta_bounds_and_symmetry():
    rng = np.random.default_rng(1)
    window = pd.DataFrame(rng.normal(0, 0.01, (50, 4)), columns=list("ABCD"))
    cov, delta = ledoit_wolf_cov(window)
    assert 0.0 <= delta <= 1.0
    assert np.allclose(cov.values, cov.values.T)


def test_lw_pd_when_sample_cov_singular():
    """T < p → sample cov singular; shrunk cov must be positive definite."""
    rng = np.random.default_rng(2)
    p, t_obs = 6, 4
    window = pd.DataFrame(
        rng.normal(0, 0.01, (t_obs, p)), columns=[f"s{i}" for i in range(p)])
    raw = window.cov().values
    assert np.min(np.linalg.eigvalsh(raw)) < 1e-10  # singular sample cov
    cov, delta = ledoit_wolf_cov(window)
    assert delta > 0.0
    assert np.min(np.linalg.eigvalsh(cov.values)) > 0.0  # PD after shrinkage


def test_lw_closer_to_identity_than_raw():
    """Identity true cov, small window: shrunk cov closer to m·I than raw."""
    rng = np.random.default_rng(3)
    p = 8
    window = pd.DataFrame(
        rng.normal(0, 1.0, (30, p)), columns=[f"s{i}" for i in range(p)])
    cov, delta = ledoit_wolf_cov(window)
    m = np.trace(cov.values) / p
    target = m * np.eye(p)
    raw = window.values - window.values.mean(axis=0)
    s = raw.T @ raw / len(raw)
    err_shrunk = np.linalg.norm(cov.values - target)
    err_raw = np.linalg.norm(s - target)
    assert err_shrunk < err_raw


def test_lw_delta_shrinks_with_more_obs():
    """δ must decrease as the observation count grows (true Σ ≠ m·I).

    Uses heterogeneous true variances: d² → ‖Σ − mI‖²_F > 0 while
    b̄² = O(1/T), so δ → 0 with more data.  (For a true scaled-identity
    Σ the optimal δ is 1 at any T, so that case does not apply here.)
    """
    rng = np.random.default_rng(4)
    p = 5
    scales = np.array([1.0, 1.5, 2.0, 3.0, 4.0])
    deltas = []
    for t_obs in (20, 200, 2000):
        window = pd.DataFrame(
            rng.normal(0, 1.0, (t_obs, p)) * scales,
            columns=[f"s{i}" for i in range(p)])
        _, delta = ledoit_wolf_cov(window)
        deltas.append(delta)
    assert deltas[0] > deltas[1] > deltas[2]
    assert deltas[2] < 0.1  # large T → little shrinkage


def test_lw_constant_data_zero_delta():
    window = pd.DataFrame(np.ones((30, 3)) * 0.01, columns=list("ABC"))
    cov, delta = ledoit_wolf_cov(window)
    assert delta == 0.0
    assert np.allclose(cov.values, 0.0)


def test_lw_reduces_erc_weight_noise():
    """Acceptance: ERC weights from LW-shrunk cov are less dispersed than
    from raw cov across many small rolling windows drawn from one true cov."""
    rng = np.random.default_rng(5)
    p, t_obs, n_draws = 5, 30, 60
    # fixed true covariance
    a = rng.normal(size=(p, p))
    true_cov = a @ a.T + np.eye(p)
    chol = np.linalg.cholesky(true_cov)

    raw_weights, lw_weights = [], []
    for _ in range(n_draws):
        draws = rng.normal(size=(t_obs, p)) @ chol.T
        window = pd.DataFrame(draws, columns=[f"s{i}" for i in range(p)])
        w_raw = erc_weights(window.cov()).weights
        cov_lw, _ = ledoit_wolf_cov(window)
        w_lw = erc_weights(cov_lw).weights
        raw_weights.append([w_raw[c] for c in window.columns])
        lw_weights.append([w_lw[c] for c in window.columns])

    # dispersion = mean over assets of the std of weights across draws
    disp_raw = float(np.std(np.array(raw_weights), axis=0).mean())
    disp_lw = float(np.std(np.array(lw_weights), axis=0).mean())
    print(f"\nweight dispersion raw={disp_raw:.5f} lw={disp_lw:.5f} "
          f"ratio={disp_lw / disp_raw:.3f}")
    assert disp_lw < disp_raw


# ---- DynamicERC wiring ----

def test_dynamic_erc_none_mode_matches_raw_cov():
    """cov_shrinkage='none' reproduces the old raw-covariance behaviour."""
    params = DynamicERCParams(
        min_lookback=50, lookback=150, rebalance_freq=1, cov_shrinkage="none")
    derc = DynamicERC(params)
    rets = _make_returns(100)
    result = derc.update(rets)
    expected = erc_weights(rets.tail(150).cov()).weights
    for k in result.raw_weights:
        assert result.raw_weights[k] == pytest.approx(expected[k], abs=1e-12)


def test_dynamic_erc_auto_uses_lw_on_small_windows():
    """auto: window 100 obs < 10*3 assets? No (100 >= 30) → raw.
    Force small window: min_lookback=25, lookback=25, 3 assets → 25 < 30 → LW."""
    rets = _make_returns(60)
    # small window → LW active (25 < 30)
    params = DynamicERCParams(
        min_lookback=25, lookback=25, rebalance_freq=1, cov_shrinkage="auto")
    derc = DynamicERC(params)
    result = derc.update(rets)
    cov_lw, delta = ledoit_wolf_cov(rets.tail(25))
    expected = erc_weights(cov_lw).weights
    assert delta > 0.0
    for k in result.raw_weights:
        assert result.raw_weights[k] == pytest.approx(expected[k], abs=1e-9)

    # large window → raw cov
    params2 = DynamicERCParams(
        min_lookback=50, lookback=150, rebalance_freq=1, cov_shrinkage="auto")
    derc2 = DynamicERC(params2)
    result2 = derc2.update(_make_returns(100))
    expected2 = erc_weights(_make_returns(100).tail(150).cov()).weights
    for k in result2.raw_weights:
        assert result2.raw_weights[k] == pytest.approx(expected2[k], abs=1e-9)


def test_dynamic_erc_invalid_shrinkage_mode_raises():
    with pytest.raises(ValueError, match="cov_shrinkage"):
        DynamicERCParams(cov_shrinkage="bogus")
