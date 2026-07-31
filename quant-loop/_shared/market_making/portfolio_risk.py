"""Portfolio-level risk: correlation matrix and ERC allocation.

Extends risk management from single-strategy to multi-strategy /
multi-asset. Answers: "if all my strategies run simultaneously, what's
my aggregate risk?"

Jane Street, "Probability & Markets Guide":
  "It is bad to lose a lot of money, because that means in the future
   when there are great opportunities to trade, you won't have as much
   capital."

References:
  - Maillard, Roncalli & Teïletche (2010), "The Properties of Equally
    Weighted Risk Contribution Portfolios"
  - López de Prado (2018), "Advances in Financial Machine Learning", Ch.16
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Correlation matrix
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CorrelationResult:
    """Pairwise correlation analysis across strategy/asset return series."""

    correlation_matrix: pd.DataFrame
    mean_correlation: float
    max_correlation: float
    max_pair: tuple[str, str]
    diversification_ratio: float   # 1 / (1 + mean_corr), higher = better


def compute_correlation(
    returns: pd.DataFrame,
) -> CorrelationResult:
    """Compute pairwise correlation and diversification metrics.

    Parameters
    ----------
    returns : pd.DataFrame
        Columns are strategy/asset names, rows are time-indexed returns.

    Returns
    -------
    CorrelationResult
    """
    corr = returns.corr()

    # Mean off-diagonal correlation
    n = len(corr)
    if n < 2:
        return CorrelationResult(
            correlation_matrix=corr,
            mean_correlation=0.0,
            max_correlation=0.0,
            max_pair=("", ""),
            diversification_ratio=1.0,
        )

    # Extract upper triangle (off-diagonal)
    mask = np.triu(np.ones((n, n), dtype=bool), k=1)
    upper_vals = corr.values[mask]
    upper_vals = upper_vals[np.isfinite(upper_vals)]

    mean_corr = float(np.mean(upper_vals)) if len(upper_vals) > 0 else 0.0
    max_idx = int(np.nanargmax(np.abs(upper_vals))) if len(upper_vals) > 0 else 0

    # Find the pair with max correlation
    pairs = [(corr.columns[i], corr.columns[j])
             for i in range(n) for j in range(i + 1, n)]
    max_pair = pairs[max_idx] if pairs else ("", "")
    max_corr = float(upper_vals[max_idx]) if len(upper_vals) > 0 else 0.0

    # Diversification ratio: simple metric
    # 1.0 = perfectly uncorrelated, lower = more correlated
    div_ratio = 1.0 / (1.0 + abs(mean_corr))

    return CorrelationResult(
        correlation_matrix=corr,
        mean_correlation=mean_corr,
        max_correlation=max_corr,
        max_pair=max_pair,
        diversification_ratio=div_ratio,
    )


# ---------------------------------------------------------------------------
# ERC (Equal Risk Contribution) allocation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ERCResult:
    """Equal Risk Contribution allocation output."""

    weights: dict[str, float]
    risk_contributions: dict[str, float]   # fraction of total portfolio vol
    portfolio_vol: float
    n_assets: int


def erc_weights(
    cov_matrix: pd.DataFrame,
    max_iter: int = 1000,
    tol: float = 1e-8,
) -> ERCResult:
    """Compute Equal Risk Contribution weights.

    Iteratively adjusts weights so that each asset contributes equally
    to total portfolio volatility.

    Uses a simple fixed-point iteration on the inverse-volatility seed,
    then refines via Newton steps on the risk-contribution mismatch.

    Parameters
    ----------
    cov_matrix : pd.DataFrame
        Symmetric covariance matrix of asset returns.
    max_iter : int
        Maximum iterations.
    tol : float
        Convergence tolerance on weight change.

    Returns
    -------
    ERCResult
    """
    assets = list(cov_matrix.index)
    n = len(assets)
    if n == 0:
        return ERCResult({}, {}, 0.0, 0)

    if n == 1:
        return ERCResult({assets[0]: 1.0}, {assets[0]: 1.0}, 0.0, 1)

    cov = cov_matrix.values.astype(float)

    # Seed: inverse-volatility weights
    vols = np.sqrt(np.diag(cov))
    vols = np.maximum(vols, 1e-10)
    w = (1.0 / vols) / np.sum(1.0 / vols)

    for _ in range(max_iter):
        # Marginal risk contributions: MRC_i = (Σw)_i
        mrc = cov @ w
        port_vol = np.sqrt(max(w @ mrc, 1e-20))

        # Risk contributions: RC_i = w_i * MRC_i
        rc = w * mrc
        rc_frac = rc / np.sum(rc)

        # Target: all equal = 1/n
        # Gradient: adjust w proportional to (target - rc_frac)
        gradient = (1.0 / n) - rc_frac
        step = 0.5 * gradient  # damping factor

        w_new = w + step
        w_new = np.maximum(w_new, 1e-10)
        w_new = w_new / np.sum(w_new)  # renormalise to sum=1

        if np.max(np.abs(w_new - w)) < tol:
            w = w_new
            break
        w = w_new

    # Final risk contributions
    mrc = cov @ w
    port_vol = float(np.sqrt(max(w @ mrc, 0.0)))
    rc = w * mrc
    rc_frac = rc / np.sum(rc)

    return ERCResult(
        weights={assets[i]: float(w[i]) for i in range(n)},
        risk_contributions={assets[i]: float(rc_frac[i]) for i in range(n)},
        portfolio_vol=port_vol,
        n_assets=n,
    )


# ---------------------------------------------------------------------------
# Portfolio VaR
# ---------------------------------------------------------------------------

def portfolio_var(
    strategy_returns: pd.DataFrame,
    weights: dict[str, float],
    confidence: float = 0.95,
) -> float:
    """Aggregate VaR for a weighted portfolio of strategies.

    Combines individual strategy returns into a portfolio return series,
    then computes historical VaR.
    """
    if strategy_returns.empty:
        return 0.0

    # Align weights with columns
    cols = [c for c in strategy_returns.columns if c in weights]
    if not cols:
        return 0.0

    w = np.array([weights[c] for c in cols])
    w = w / w.sum() if w.sum() > 0 else w

    portfolio_ret = (strategy_returns[cols] * w).sum(axis=1)
    percentile = (1.0 - confidence) * 100
    return abs(float(np.percentile(portfolio_ret.dropna(), percentile)))


def portfolio_cvar(
    strategy_returns: pd.DataFrame,
    weights: dict[str, float],
    confidence: float = 0.95,
) -> float:
    """Aggregate CVaR for a weighted portfolio."""
    if strategy_returns.empty:
        return 0.0

    cols = [c for c in strategy_returns.columns if c in weights]
    if not cols:
        return 0.0

    w = np.array([weights[c] for c in cols])
    w = w / w.sum() if w.sum() > 0 else w

    portfolio_ret = (strategy_returns[cols] * w).sum(axis=1).dropna()
    percentile = (1.0 - confidence) * 100
    var_threshold = np.percentile(portfolio_ret, percentile)
    tail = portfolio_ret[portfolio_ret <= var_threshold]
    if len(tail) == 0:
        return abs(float(var_threshold))
    return abs(float(tail.mean()))
