"""Tail risk metrics — copula-based tail dependence + EVT/GPD VaR.

Bridges the gap between normal-distribution VaR (which underestimates
tail risk for crypto returns) and empirically-grounded tail modeling.

Implements:
  - Tail dependence coefficient (Gaussian + Student-t copula)
  - Generalized Pareto Distribution (GPD) fit for Peaks-Over-Threshold
  - EVT-based VaR and CVaR (fat-tailed, distribution-free)
  - Hill estimator for tail index (cross-check)

References:
  - McNeil, Frey, Embrechts (2015) "Quantitative Risk Management"
  - Nelsen (2006) "An Introduction to Copulas"
  - Embrechts, Klüppelberg, Mikosch (1997) "Modelling Extremal Events"
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from scipy.optimize import minimize


@dataclass(frozen=True)
class GPDFit:
    """Generalized Pareto Distribution fit result.

    The GPD CDF is: F(x) = 1 - (1 + ξ·x/β)^(-1/ξ)  for ξ ≠ 0
                          = 1 - exp(-x/β)            for ξ = 0

    Attributes:
        xi: shape parameter (tail index). ξ > 0 heavy tail, ξ = 0 exponential, ξ < 0 finite endpoint.
        beta: scale parameter (must be > 0).
        threshold: the level above which peaks were fit (u in POT).
        n_exceedances: number of observations above threshold.
        method: fitting method used.
    """
    xi: float
    beta: float
    threshold: float
    n_exceedances: int
    method: str = "MLE"


@dataclass(frozen=True)
class CopulaFit:
    """Copula fit result for bivariate tail dependence."""
    copula_type: str  # "gaussian" or "student_t"
    rho: float        # correlation parameter
    df: float | None  # degrees of freedom (Student-t only)
    tail_lower: float  # lower tail dependence coefficient λ⁻
    tail_upper: float  # upper tail dependence coefficient λ⁺


# ---------------------------------------------------------------------------
# EVT / GPD
# ---------------------------------------------------------------------------

def fit_gpd(
    data: pd.Series | np.ndarray,
    threshold_quantile: float = 0.95,
    method: Literal["MLE", "PWM"] = "MLE",
) -> GPDFit:
    """Fit Generalized Pareto Distribution via Peaks-Over-Threshold.

    Args:
        data: return series (or any continuous data).
        threshold_quantile: quantile to use as threshold u (default 95th percentile).
        method: "MLE" (maximum likelihood) or "PWM" (probability weighted moments).

    Returns:
        GPDFit with shape (ξ), scale (β), threshold, and exceedance count.
    """
    arr = np.asarray(data, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 50:
        raise ValueError(f"Insufficient data for GPD fit: {len(arr)} < 50")

    threshold = np.quantile(arr, threshold_quantile)
    exceedances = arr[arr > threshold] - threshold

    if len(exceedances) < 10:
        raise ValueError(
            f"Too few exceedances ({len(exceedances)}) above threshold "
            f"(q={threshold_quantile}). Lower threshold_quantile or get more data."
        )

    if method == "PWM":
        return _fit_gpd_pwm(exceedances, threshold)
    return _fit_gpd_mle(exceedances, threshold)


def _fit_gpd_mle(exceedances: np.ndarray, threshold: float) -> GPDFit:
    """Maximum likelihood estimation of GPD parameters."""
    n = len(exceedances)

    def neg_log_likelihood(params: np.ndarray) -> float:
        xi, beta = params
        if beta <= 0:
            return 1e10
        if abs(xi) < 1e-8:
            # Exponential case
            return -n * np.log(beta) + np.sum(exceedances) / beta
        scaled = xi * exceedances / beta
        if np.any(scaled <= -1):
            return 1e10  # invalid: CDF goes negative
        return (
            n * np.log(beta)
            + (1.0 / xi + 1.0) * np.sum(np.log1p(scaled))
        )

    # Initial guess via method of moments
    mu_ex = np.mean(exceedances)
    s_ex = np.std(exceedances, ddof=1)
    if s_ex > 0 and mu_ex > 0:
        xi0 = 0.5 * (1 - (mu_ex / s_ex) ** 2)
        beta0 = mu_ex * (1 - xi0) if (1 - xi0) > 0 else mu_ex
    else:
        xi0, beta0 = 0.1, mu_ex if mu_ex > 0 else 1.0

    result = minimize(
        neg_log_likelihood,
        x0=[xi0, max(beta0, 1e-6)],
        method="Nelder-Mead",
        options={"xatol": 1e-8, "maxiter": 5000},
    )
    xi, beta = result.x
    return GPDFit(xi=float(xi), beta=float(max(beta, 1e-10)),
                  threshold=float(threshold), n_exceedances=n, method="MLE")


def _fit_gpd_pwm(exceedances: np.ndarray, threshold: float) -> GPDFit:
    """Probability Weighted Moments estimation of GPD."""
    n = len(exceedances)
    sorted_ex = np.sort(exceedances)
    # PWM estimators
    b0 = np.mean(sorted_ex)
    weights = (np.arange(1, n + 1) - 1) / (n - 1) if n > 1 else np.zeros(n)
    b1 = np.mean(sorted_ex * weights)

    if b0 - 2 * b1 <= 0:
        xi = 0.1
        beta = max(b0, 1e-10)
    else:
        xi = b0 / (b0 - 2 * b1) - 2
        beta = 2 * b0 * b1 / (b0 - 2 * b1)

    return GPDFit(xi=float(xi), beta=float(max(beta, 1e-10)),
                  threshold=float(threshold), n_exceedances=n, method="PWM")


def evt_var(
    data: pd.Series | np.ndarray,
    confidence: float = 0.99,
    threshold_quantile: float = 0.95,
) -> float:
    """EVT-based Value at Risk using GPD (Peaks-Over-Threshold method).

    VaR_α = u + (β/ξ) · [((1-α)/(n/n_u))^(-ξ) - 1]

    This captures fat tails that normal-distribution VaR misses.

    Args:
        data: return series.
        confidence: VaR confidence level (e.g., 0.99 for 99% VaR).
        threshold_quantile: quantile for POT threshold.

    Returns:
        VaR as a positive number (loss magnitude).
    """
    arr = np.asarray(data, dtype=float)
    fit = fit_gpd(arr, threshold_quantile=threshold_quantile)
    n = len(arr)
    n_u = fit.n_exceedances

    # GPD quantile
    prob_tail = (1 - confidence) / (n_u / n)  # P(X > VaR | X > u)
    prob_tail = min(max(prob_tail, 1e-10), 1 - 1e-10)

    if abs(fit.xi) < 1e-8:
        var_excess = -fit.beta * np.log(prob_tail)
    else:
        var_excess = (fit.beta / fit.xi) * (prob_tail ** (-fit.xi) - 1)

    return float(fit.threshold + var_excess)


def evt_cvar(
    data: pd.Series | np.ndarray,
    confidence: float = 0.99,
    threshold_quantile: float = 0.95,
) -> float:
    """EVT-based Conditional VaR (Expected Shortfall).

    CVaR_α = VaR_α + (β + ξ·(VaR_α - u)) / (1 - ξ)

    Args:
        data: return series.
        confidence: CVaR confidence level.
        threshold_quantile: quantile for POT threshold.

    Returns:
        CVaR as a positive number (loss magnitude).
    """
    arr = np.asarray(data, dtype=float)
    fit = fit_gpd(arr, threshold_quantile=threshold_quantile)
    var = evt_var(arr, confidence, threshold_quantile)

    excess = var - fit.threshold
    if abs(fit.xi) < 1e-8:
        cvar_excess = fit.beta + excess
    else:
        cvar_excess = (fit.beta + fit.xi * excess) / (1 - fit.xi)

    return float(var + cvar_excess)


def hill_estimator(data: pd.Series | np.ndarray, k: int | None = None) -> float:
    """Hill tail index estimator.

    A cross-check on the GPD shape parameter. The Hill estimator is:

        ξ_hill = (1/k) · Σ ln(X_{n-i+1}) - ln(X_{n-k})

    Returns the tail index α = 1/ξ (higher = thinner tail).
    """
    arr = np.sort(np.abs(np.asarray(data, dtype=float)))
    arr = arr[arr > 0]
    n = len(arr)
    if k is None:
        k = max(int(0.1 * n), 10)
    k = min(k, n - 1)
    if k < 5:
        return float("inf")
    threshold = arr[n - k - 1]
    order_stats = arr[n - k:]
    xi_hill = np.mean(np.log(order_stats) - np.log(threshold))
    return float(1.0 / xi_hill) if xi_hill > 0 else float("inf")


# ---------------------------------------------------------------------------
# Copula / Tail Dependence
# ---------------------------------------------------------------------------

def fit_copula(
    x: pd.Series | np.ndarray,
    y: pd.Series | np.ndarray,
    copula_type: Literal["gaussian", "student_t"] = "student_t",
) -> CopulaFit:
    """Fit a copula and compute tail dependence coefficients.

    Tail dependence λ measures the probability of joint extreme events:
        λ⁻ = P(X < F_X⁻¹(α) | Y < F_Y⁻¹(α))  as α → 0
        λ⁺ = P(X > F_X⁻¹(1-α) | Y > F_Y⁻¹(1-α))  as α → 0

    Gaussian copula has zero tail dependence (λ = 0 for ρ < 1).
    Student-t copula has positive tail dependence that increases with
    correlation ρ and decreases with degrees of freedom df.

    Args:
        x, y: return series (aligned).
        copula_type: "gaussian" or "student_t".

    Returns:
        CopulaFit with ρ, df (if t), and tail dependence coefficients.
    """
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    n = min(len(x_arr), len(y_arr))
    x_arr, y_arr = x_arr[:n], y_arr[:n]

    if n < 50:
        raise ValueError(f"Insufficient data for copula fit: {n} < 50")

    # Transform to uniform via empirical CDF (rank-based)
    u = sp_stats.rankdata(x_arr) / (n + 1)
    v = sp_stats.rankdata(y_arr) / (n + 1)

    # Transform to standard normal
    z_x = sp_stats.norm.ppf(u)
    z_y = sp_stats.norm.ppf(v)

    # Correlation
    rho = float(np.corrcoef(z_x, z_y)[0, 1])

    if copula_type == "gaussian":
        # Gaussian copula: zero tail dependence for |ρ| < 1
        return CopulaFit(
            copula_type="gaussian",
            rho=rho, df=None,
            tail_lower=0.0, tail_upper=0.0,
        )

    # Student-t copula: fit df via MLE
    def neg_log_lik(df_val: float) -> float:
        if df_val < 1:
            return 1e10
        # Bivariate t log-likelihood
        c = np.column_stack([z_x, z_y])
        log_lik = np.sum(sp_stats.multivariate_t.logpdf(
            c, df=df_val, shape=[[1, rho], [rho, 1]]
        ))
        return -log_lik if np.isfinite(log_lik) else 1e10

    from scipy.optimize import minimize_scalar
    result = minimize_scalar(neg_log_lik, bounds=(1, 200), method="bounded")
    df = float(result.x)

    # Tail dependence for Student-t copula
    # λ = 2 * t_{ν+1}( -sqrt((ν+1)(1-ρ)/(1+ρ)) )
    if rho > -1:
        arg = -np.sqrt((df + 1) * (1 - rho) / (1 + rho))
        lam = float(2 * sp_stats.t.cdf(arg, df + 1))
    else:
        lam = 0.0

    return CopulaFit(
        copula_type="student_t",
        rho=rho, df=df,
        tail_lower=lam, tail_upper=lam,  # symmetric for t-copula
    )


def tail_dependence_matrix(
    returns: pd.DataFrame,
    copula_type: Literal["gaussian", "student_t"] = "student_t",
) -> pd.DataFrame:
    """Compute pairwise tail dependence matrix for all asset pairs.

    Args:
        returns: DataFrame of asset returns (columns = assets).
        copula_type: copula type for fitting.

    Returns:
        Symmetric DataFrame of lower tail dependence coefficients.
    """
    assets = returns.columns
    n = len(assets)
    mat = np.full((n, n), np.nan)
    np.fill_diagonal(mat, 1.0)

    for i in range(n):
        for j in range(i + 1, n):
            try:
                fit = fit_copula(
                    returns[assets[i]].dropna(),
                    returns[assets[j]].dropna(),
                    copula_type=copula_type,
                )
                mat[i, j] = fit.tail_lower
                mat[j, i] = fit.tail_lower
            except (ValueError, RuntimeError):
                mat[i, j] = mat[j, i] = 0.0

    return pd.DataFrame(mat, index=assets, columns=assets)
