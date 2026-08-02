"""Tests for portfolio/component_var.py (I16)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.portfolio.component_var import ComponentVaRResult, compute_component_var


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_returns(n_obs=2000, seed=42):
    """Generate returns with two assets of different volatility."""
    rng = np.random.default_rng(seed)
    cov = np.array([[0.04, 0.012], [0.012, 0.01]])
    mean = np.array([0.001, 0.0005])
    data = rng.multivariate_normal(mean, cov, size=n_obs)
    return pd.DataFrame(data, columns=["alpha", "beta"])


# ---------------------------------------------------------------------------
# Basic result type & fields
# ---------------------------------------------------------------------------

def test_result_type():
    returns = _make_returns()
    result = compute_component_var(returns, {"alpha": 0.6, "beta": 0.4})
    assert isinstance(result, ComponentVaRResult)


def test_result_fields():
    returns = _make_returns()
    result = compute_component_var(returns, {"alpha": 0.6, "beta": 0.4})
    assert hasattr(result, "var")
    assert hasattr(result, "cvar")
    assert hasattr(result, "component_var")
    assert hasattr(result, "component_cvar")
    assert hasattr(result, "marginal_var")
    assert hasattr(result, "confidence")
    assert result.confidence == 0.95


def test_var_positive():
    returns = _make_returns()
    result = compute_component_var(returns, {"alpha": 0.5, "beta": 0.5})
    assert result.var > 0
    assert result.cvar >= result.var  # CVaR ≥ VaR (tail mean is worse)


# ---------------------------------------------------------------------------
# Component decomposition
# ---------------------------------------------------------------------------

def test_component_var_keys_match_assets():
    returns = _make_returns()
    w = {"alpha": 0.6, "beta": 0.4}
    result = compute_component_var(returns, w)
    assert set(result.component_var.keys()) == {"alpha", "beta"}
    assert set(result.component_cvar.keys()) == {"alpha", "beta"}
    assert set(result.marginal_var.keys()) == {"alpha", "beta"}


def test_component_var_is_marginal_times_weight():
    """Component VaR = marginal VaR × weight."""
    returns = _make_returns()
    w = {"alpha": 0.6, "beta": 0.4}
    result = compute_component_var(returns, w)
    for asset in w:
        expected = result.marginal_var[asset] * w[asset]
        assert result.component_var[asset] == pytest.approx(expected, rel=1e-6)


def test_higher_vol_asset_higher_component_var():
    """The higher-volatility asset should contribute more to VaR."""
    rng = np.random.default_rng(10)
    returns = pd.DataFrame({
        "low_vol": rng.normal(0, 0.01, 2000),
        "high_vol": rng.normal(0, 0.05, 2000),
    })
    w = {"low_vol": 0.5, "high_vol": 0.5}
    result = compute_component_var(returns, w)
    assert abs(result.component_var["high_vol"]) > abs(result.component_var["low_vol"])


def test_confidence_level_affects_var():
    """Higher confidence → higher VaR."""
    returns = _make_returns()
    r95 = compute_component_var(returns, {"alpha": 0.5, "beta": 0.5}, confidence=0.95)
    r99 = compute_component_var(returns, {"alpha": 0.5, "beta": 0.5}, confidence=0.99)
    assert r99.var > r95.var
    assert r99.cvar > r95.cvar


# ---------------------------------------------------------------------------
# Weight normalization
# ---------------------------------------------------------------------------

def test_weights_normalized():
    """Weights not summing to 1 should be normalized internally."""
    returns = _make_returns()
    r1 = compute_component_var(returns, {"alpha": 60, "beta": 40})
    r2 = compute_component_var(returns, {"alpha": 0.6, "beta": 0.4})
    assert r1.var == pytest.approx(r2.var, rel=1e-10)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_empty_returns_raises():
    with pytest.raises(ValueError):
        compute_component_var(pd.DataFrame(), {"A": 1.0})


def test_no_matching_assets_raises():
    returns = _make_returns()
    with pytest.raises(ValueError):
        compute_component_var(returns, {"nonexistent": 1.0})


def test_invalid_confidence_raises():
    returns = _make_returns()
    with pytest.raises(ValueError, match="confidence"):
        compute_component_var(returns, {"alpha": 0.5, "beta": 0.5}, confidence=0.3)


def test_unsupported_method_raises():
    returns = _make_returns()
    with pytest.raises(ValueError, match="method"):
        compute_component_var(returns, {"alpha": 0.5, "beta": 0.5}, method="parametric")


def test_too_few_observations_raises():
    returns = pd.DataFrame({"A": [0.01] * 5, "B": [0.02] * 5})
    with pytest.raises(ValueError, match="[Ii]nsufficient|observations"):
        compute_component_var(returns, {"A": 0.5, "B": 0.5})


def test_zero_total_weight_raises():
    returns = _make_returns()
    with pytest.raises(ValueError, match="weight"):
        compute_component_var(returns, {"alpha": 0.0, "beta": 0.0})


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_deterministic():
    returns = _make_returns()
    r1 = compute_component_var(returns, {"alpha": 0.6, "beta": 0.4})
    r2 = compute_component_var(returns, {"alpha": 0.6, "beta": 0.4})
    assert r1.var == r2.var
    assert r1.component_var == r2.component_var
