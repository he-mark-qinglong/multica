"""Tests for portfolio_risk.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.portfolio_risk import (
    CorrelationResult, ERCResult,
    compute_correlation, erc_weights,
    portfolio_var, portfolio_cvar,
)


def _make_returns(n=200, assets=("A", "B", "C"), seed=42):
    np.random.seed(seed)
    data = {}
    for a in assets:
        data[a] = np.random.normal(0.001, 0.02, n)
    return pd.DataFrame(data)


# ---- correlation ----

def test_correlation_uncorrelated():
    rets = _make_returns()
    result = compute_correlation(rets)
    assert isinstance(result, CorrelationResult)
    assert abs(result.mean_correlation) < 0.3  # roughly uncorrelated
    assert result.diversification_ratio > 0.7

def test_correlation_perfectly_correlated():
    rets = pd.DataFrame({"A": [1, 2, 3, 4], "B": [2, 4, 6, 8]})
    result = compute_correlation(rets)
    assert result.mean_correlation > 0.99

def test_correlation_single_asset():
    rets = pd.DataFrame({"A": [1, 2, 3]})
    result = compute_correlation(rets)
    assert result.mean_correlation == 0.0


# ---- ERC ----

def test_erc_equal_vol():
    # Equal vol assets → equal weights
    cov = pd.DataFrame(np.eye(3) * 0.04, index=["A", "B", "C"], columns=["A", "B", "C"])
    result = erc_weights(cov)
    for w in result.weights.values():
        assert w == pytest.approx(1.0 / 3, abs=0.01)

def test_erc_unequal_vol():
    # Asset with higher vol → lower weight
    cov = pd.DataFrame(
        [[0.04, 0, 0], [0, 0.01, 0], [0, 0, 0.01]],
        index=["A", "B", "C"], columns=["A", "B", "C"],
    )
    result = erc_weights(cov)
    assert result.weights["A"] < result.weights["B"]  # higher vol → less weight

def test_erc_risk_contributions_equal():
    np.random.seed(42)
    rets = _make_returns()
    cov = rets.cov()
    result = erc_weights(cov)
    # All risk contributions should be ~1/n
    n = result.n_assets
    for rc in result.risk_contributions.values():
        assert rc == pytest.approx(1.0 / n, abs=0.05)

def test_erc_single_asset():
    cov = pd.DataFrame([[0.04]], index=["A"], columns=["A"])
    result = erc_weights(cov)
    assert result.weights["A"] == pytest.approx(1.0)


# ---- Portfolio VaR / CVaR ----

def test_portfolio_var_positive():
    rets = _make_returns()
    weights = {"A": 0.4, "B": 0.3, "C": 0.3}
    var = portfolio_var(rets, weights)
    assert var > 0

def test_portfolio_cvar_exceeds_var():
    rets = _make_returns()
    weights = {"A": 0.4, "B": 0.3, "C": 0.3}
    var = portfolio_var(rets, weights)
    cvar = portfolio_cvar(rets, weights)
    assert cvar >= var

def test_portfolio_var_empty():
    assert portfolio_var(pd.DataFrame(), {}) == 0.0
