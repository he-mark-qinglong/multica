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


# ---- Regression: Bug 2 — diversification_ratio must use textbook formula ----

def test_diversification_ratio_uncorrelated():
    """For n uncorrelated assets with equal vol, DR = sqrt(n)."""
    np.random.seed(123)
    n = 4
    rets = pd.DataFrame({
        f"s{i}": np.random.normal(0, 0.02, 500) for i in range(n)
    })
    result = compute_correlation(rets)
    # DR should be close to sqrt(4) = 2 for uncorrelated equal-vol assets
    assert 1.7 < result.diversification_ratio < 2.3

def test_diversification_ratio_perfectly_correlated():
    """Perfectly correlated assets → DR = 1 (no diversification)."""
    rets = pd.DataFrame({"A": [1, 2, 3, 4, 5], "B": [2, 4, 6, 8, 10]})
    result = compute_correlation(rets)
    assert result.diversification_ratio == pytest.approx(1.0, abs=0.05)


# ---- Validation: exact SLSQP risk-budgeting solver (SMA-36941) ----

def _log_barrier_weights(cov: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Independent risk-budgeting formulation (Roncalli): the solution of

        min_x  ½ xᵀΣx − Σ_i b_i ln(x_i)

    normalised to sum 1 is the risk-budgeting portfolio.  The objective
    is strictly convex on the positive orthant (Hessian Σ + diag(b/x²) ≻ 0),
    so this is an independent cross-check of the SLSQP solver.
    """
    from scipy.optimize import minimize as _min

    n = cov.shape[0]

    def objective(x):
        return 0.5 * x @ cov @ x - float(b @ np.log(x))

    def gradient(x):
        return cov @ x - b / x

    res = _min(
        objective, np.full(n, 1.0 / n), jac=gradient, method="L-BFGS-B",
        bounds=[(1e-10, None)] * n,
        options={"ftol": 1e-18, "gtol": 1e-14, "maxiter": 1000},
    )
    x = res.x
    return x / x.sum()


def _random_psd_cov(n: int, rng: np.random.Generator) -> pd.DataFrame:
    a = rng.normal(size=(n, n))
    cov = a @ a.T + np.eye(n) * 0.5  # well-conditioned PD
    # random vol scaling so assets differ in risk
    d = np.diag(rng.uniform(0.5, 3.0, n))
    cov = d @ cov @ d
    names = [f"s{i}" for i in range(n)]
    return pd.DataFrame(cov, index=names, columns=names)


def test_erc_matches_log_barrier_formulation():
    """SLSQP weights must agree with the independent convex log-barrier
    solution to < 1e-6 in sup norm on seeded random PD covariances."""
    rng = np.random.default_rng(7)
    max_err = 0.0
    for trial in range(8):
        n = int(rng.integers(2, 7))
        cov_df = _random_psd_cov(n, rng)
        b = rng.uniform(0.2, 2.0, n)
        b = b / b.sum()
        result = erc_weights(cov_df, budget=b)
        w_slsqp = np.array([result.weights[c] for c in cov_df.columns])
        w_lb = _log_barrier_weights(cov_df.values, b)
        err = float(np.max(np.abs(w_slsqp - w_lb)))
        max_err = max(max_err, err)
        assert err < 1e-6, f"trial {trial}: |w_slsqp − w_logbarrier|_inf = {err}"
    print(f"\nmax |w_slsqp − w_logbarrier|_inf over 8 trials: {max_err:.3e}")


def test_erc_risk_contributions_match_equal_budget_tightly():
    """RC fractions must hit the equal budget to < 1e-8."""
    rng = np.random.default_rng(123)
    cov_df = _random_psd_cov(5, rng)
    result = erc_weights(cov_df)
    n = result.n_assets
    for rc in result.risk_contributions.values():
        assert abs(rc - 1.0 / n) < 1e-8


def test_erc_risk_contributions_match_custom_budget():
    """RC fractions must hit a custom budget b = (0.5, 0.3, 0.2) to < 1e-8."""
    rng = np.random.default_rng(99)
    cov_df = _random_psd_cov(3, rng)
    budget = {"s0": 0.5, "s1": 0.3, "s2": 0.2}
    result = erc_weights(cov_df, budget=budget)
    for name, target in budget.items():
        assert abs(result.risk_contributions[name] - target) < 1e-8


def test_erc_budget_dict_and_sequence_equivalent():
    rng = np.random.default_rng(5)
    cov_df = _random_psd_cov(4, rng)
    b = [0.4, 0.3, 0.2, 0.1]
    r_dict = erc_weights(cov_df, budget={f"s{i}": b[i] for i in range(4)})
    r_seq = erc_weights(cov_df, budget=b)
    for name in r_dict.weights:
        assert r_dict.weights[name] == pytest.approx(r_seq.weights[name], abs=1e-12)


def test_erc_budget_unnormalised_is_normalised():
    rng = np.random.default_rng(5)
    cov_df = _random_psd_cov(3, rng)
    r1 = erc_weights(cov_df, budget=[0.5, 0.3, 0.2])
    r2 = erc_weights(cov_df, budget=[5.0, 3.0, 2.0])
    for name in r1.weights:
        assert r1.weights[name] == pytest.approx(r2.weights[name], abs=1e-12)


def test_erc_invalid_budget_raises():
    cov_df = pd.DataFrame(np.eye(2), index=["A", "B"], columns=["A", "B"])
    with pytest.raises(ValueError):
        erc_weights(cov_df, budget=[0.5])          # wrong length
    with pytest.raises(ValueError):
        erc_weights(cov_df, budget=[1.5, -0.5])    # negative entry
    with pytest.raises(ValueError):
        erc_weights(cov_df, budget={"A": 1.0})     # missing key
    with pytest.raises(ValueError):
        erc_weights(cov_df, budget=[0.0, 0.0])     # zero total
