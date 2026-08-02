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
from scipy.optimize import minimize


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
    diversification_ratio: float   # wᵀσ / √(wᵀΣw), higher = better


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

    # Diversification ratio: textbook definition wᵀσ / √(wᵀΣw)
    # with equal weights.  Equals √n for uncorrelated assets, →1 as
    # correlations → 1.
    cov = returns.cov()
    n_assets = len(cov)
    if n_assets > 1:
        w_eq = np.full(n_assets, 1.0 / n_assets)
        asset_vols = np.sqrt(np.maximum(np.diag(cov.values), 0.0))
        weighted_avg_vol = float(w_eq @ asset_vols)
        port_vol = float(np.sqrt(max(w_eq @ cov.values @ w_eq, 0.0)))
        div_ratio = weighted_avg_vol / port_vol if port_vol > 1e-20 else 1.0
    else:
        div_ratio = 1.0

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


def _normalise_budget(
    budget: dict[str, float] | Sequence[float] | None,
    assets: list[str],
) -> np.ndarray:
    """Validate and normalise a risk-budget vector to sum 1.

    None -> equal budget 1/n (Equal Risk Contribution).
    Raises ValueError on wrong length, unknown keys, negative entries,
    or a zero-sum budget.
    """
    n = len(assets)
    if budget is None:
        return np.full(n, 1.0 / n)
    if isinstance(budget, dict):
        missing = [a for a in assets if a not in budget]
        if missing:
            raise ValueError(f"budget missing assets {missing}")
        b = np.array([float(budget[a]) for a in assets])
    else:
        b = np.asarray(list(budget), dtype=float)
        if b.shape != (n,):
            raise ValueError(f"budget length {b.size} != n_assets {n}")
    if np.any(b < 0) or not np.all(np.isfinite(b)):
        raise ValueError("budget entries must be finite and non-negative")
    total = float(b.sum())
    if total <= 0:
        raise ValueError("budget must have positive total mass")
    return b / total


def _solve_risk_budget(cov: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Exact risk-budgeting solution via SLSQP (Roncalli formulation).

    Minimises  Σ_i (RC_i(w)/Σ_j RC_j(w) − b_i)²  with  RC_i = w_i·(Σw)_i
    subject to 1ᵀw = 1, w ≥ 0.  Seeded from inverse-volatility weights.

    With a PSD covariance and b > 0 the objective's unique global
    minimiser on the simplex is the risk-budgeting portfolio (same point
    as the normalised solution of the convex log-barrier problem
    min ½ xᵀΣx − Σ_i b_i ln x_i); SLSQP with an analytic gradient and
    tight ftol recovers it to machine precision.
    """
    n = cov.shape[0]

    def objective(w: np.ndarray) -> float:
        mrc = cov @ w
        total = w @ mrc
        if total <= 0.0:
            return 1e6
        frac = (w * mrc) / total
        diff = frac - b
        return float(diff @ diff)

    def gradient(w: np.ndarray) -> np.ndarray:
        # f = Σ_i e_i²,  e_i = rc_i/T − b_i,  rc = w ⊙ (Σw),  T = wᵀΣw
        # ∂rc_i/∂w_j = δ_ij (Σw)_i + w_i Σ_ij,   ∂T/∂w_j = 2 (Σw)_j
        mrc = cov @ w
        total = w @ mrc
        rc = w * mrc
        e = rc / total - b
        return 2.0 * (
            e * mrc / total
            + cov @ (e * w) / total
            - 2.0 * mrc * (e @ rc) / total**2
        )

    def _slsqp(w0: np.ndarray) -> tuple[np.ndarray, float]:
        res = minimize(
            objective,
            w0,
            jac=gradient,
            method="SLSQP",
            bounds=[(1e-12, 1.0)] * n,
            constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}],
            options={"ftol": 1e-15, "maxiter": 500},
        )
        return res.x, objective(res.x)

    # Seed: inverse-volatility weights
    vols = np.sqrt(np.maximum(np.diag(cov), 1e-20))
    w_invvol = (1.0 / vols) / np.sum(1.0 / vols)

    # The squared-mismatch objective is not convex — SLSQP can stick on a
    # spurious boundary KKT point from a single seed.  Multi-start from a
    # small deterministic seed set and keep the best objective value.
    seeds = [w_invvol, np.full(n, 1.0 / n), np.maximum(b, 1e-12) / b.sum()]
    best_w, best_obj = None, np.inf
    for w0 in seeds:
        w_try, obj_try = _slsqp(w0)
        if obj_try < best_obj:
            best_obj, best_w = obj_try, w_try

    # Rare stubborn cases: recover via the equivalent convex log-barrier
    # problem (min ½ xᵀΣx − Σ_i b_i ln x_i, unique minimiser for PD Σ and
    # b > 0; its normalised solution IS the risk-budgeting portfolio) and
    # polish with SLSQP.  Keeps SLSQP on the Roncalli objective as the
    # primary and final solver while guaranteeing exactness.
    if best_obj > 1e-10:
        def lb_obj(x: np.ndarray) -> float:
            return 0.5 * x @ cov @ x - float(b @ np.log(x))

        def lb_grad(x: np.ndarray) -> np.ndarray:
            return cov @ x - b / x

        res = minimize(
            lb_obj, np.full(n, 1.0 / n), jac=lb_grad, method="L-BFGS-B",
            bounds=[(1e-10, None)] * n,
            options={"ftol": 1e-18, "gtol": 1e-14, "maxiter": 1000},
        )
        w0 = res.x / res.x.sum()
        w_try, obj_try = _slsqp(w0)
        if obj_try < best_obj:
            best_obj, best_w = obj_try, w_try

    w = np.maximum(best_w, 0.0)
    return w / np.sum(w)


def erc_weights(
    cov_matrix: pd.DataFrame,
    max_iter: int = 1000,
    tol: float = 1e-8,
    budget: dict[str, float] | Sequence[float] | None = None,
) -> ERCResult:
    """Compute Equal Risk Contribution (or general risk-budgeting) weights.

    Exact SLSQP solution of Roncalli's risk-budgeting objective:

        min_w  Σ_i (RC_i(w)/Σ_j RC_j(w) − b_i)²
        s.t.   1ᵀw = 1,  w ≥ 0,   RC_i = w_i·(Σw)_i

    Parameters
    ----------
    cov_matrix : pd.DataFrame
        Symmetric covariance matrix of asset returns.
    max_iter, tol : kept for backward compatibility (the old damped
        fixed-point iteration used them); accepted but unused — the
        SLSQP solve has its own tight tolerances.
    budget : dict or sequence, optional
        Target risk-budget fractions b_i (normalised to sum 1).
        None (default) -> equal budget 1/n, i.e. classic ERC.

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

    b = _normalise_budget(budget, assets)
    cov = cov_matrix.values.astype(float)

    w = _solve_risk_budget(cov, b)

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
