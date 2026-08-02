"""Tail risk metrics: VaR, CVaR, and stress scenarios.

Jane Street, "Probability & Markets Guide" — Confidence Intervals:
  "If we're making a trade, we might want to know the probability we
   lose a lot of money, or more generally the range of normal outcomes."
  "People tend to be overconfident."

References:
  - Rockafellar & Uryasev (2000), "Optimization of Conditional Value-at-Risk"
  - Cornish & Fisher (1938), expansion for non-normal distributions
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class TailRiskResult:
    """Comprehensive tail-risk snapshot."""

    # Value at Risk (loss, positive number = magnitude of loss)
    var_95_bp: float                # 95th percentile loss
    var_99_bp: float                # 99th percentile loss

    # Conditional VaR (Expected Shortfall) — average loss beyond VaR
    cvar_95_bp: float
    cvar_99_bp: float

    # Distribution shape
    mean_bp: float
    std_bp: float
    skewness: float
    excess_kurtosis: float

    # Cornish-Fisher VaR (adjusts for fat tails)
    cf_var_95_bp: float
    cf_var_99_bp: float

    # Stress scenarios
    worst_case_bp: float            # worst observed outcome
    max_consecutive_losses: int     # longest losing streak

    n_samples: int


def historical_var(
    pnl_bp: Sequence[float],
    confidence: float = 0.95,
) -> float:
    """Historical VaR — the loss at the given confidence level.

    Returns a positive number representing the loss magnitude in bp.
    E.g. var=50bp means "with 95% confidence, we won't lose more than 50bp."
    """
    arr = np.array(pnl_bp, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return 0.0
    percentile = (1.0 - confidence) * 100  # 95% → 5th percentile
    var_threshold = float(np.percentile(arr, percentile))
    # VaR is a *loss* magnitude: if the tail percentile is still
    # positive (all trades profitable), there is no loss at this
    # confidence level → return 0.
    return max(0.0, -var_threshold)


def historical_cvar(
    pnl_bp: Sequence[float],
    confidence: float = 0.95,
) -> float:
    """Historical CVaR (Expected Shortfall).

    Average loss in the worst (1-confidence) tail of the distribution.
    """
    arr = np.array(pnl_bp, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return 0.0
    percentile = (1.0 - confidence) * 100
    var_threshold = np.percentile(arr, percentile)
    tail = arr[arr <= var_threshold]
    if len(tail) == 0:
        return abs(float(var_threshold))
    return abs(float(np.mean(tail)))


def cornish_fisher_var(
    pnl_bp: Sequence[float],
    confidence: float = 0.95,
) -> float:
    """Cornish-Fisher VaR — adjusts parametric VaR for skew & kurtosis.

    The CF expansion modifies the z-score to account for non-normality,
    which is critical for fat-tailed trading PnL distributions.
    """
    arr = np.array(pnl_bp, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 4:
        return historical_var(arr, confidence)

    mu = float(np.mean(arr))
    sigma = float(np.std(arr, ddof=1))
    if sigma <= 0:
        return 0.0

    # Skewness and excess kurtosis
    skew = float(np.mean(((arr - mu) / sigma) ** 3))
    kurt = float(np.mean(((arr - mu) / sigma) ** 4)) - 3.0  # excess

    # z-score for confidence level
    from scipy.stats import norm
    z = norm.ppf(1.0 - confidence)  # negative for left tail

    # Cornish-Fisher expansion
    z_cf = (z
            + (z**2 - 1) * skew / 6
            + (z**3 - 3*z) * kurt / 24
            - (2*z**3 - 5*z) * skew**2 / 36)

    cf_var = -(mu + z_cf * sigma)
    return max(0.0, cf_var)


def max_consecutive_losses(pnl_bp: Sequence[float]) -> int:
    """Longest run of consecutive negative trades."""
    arr = np.array(pnl_bp, dtype=float)
    arr = arr[np.isfinite(arr)]
    max_streak = 0
    current = 0
    for p in arr:
        if p < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def compute_tail_risk(
    pnl_bp: Sequence[float],
) -> TailRiskResult:
    """Full tail-risk analysis from a PnL history.

    Use this after a simulation run to assess downside risk.
    """
    arr = np.array(pnl_bp, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)

    if n < 2:
        return TailRiskResult(
            var_95_bp=0.0, var_99_bp=0.0,
            cvar_95_bp=0.0, cvar_99_bp=0.0,
            mean_bp=0.0, std_bp=0.0,
            skewness=0.0, excess_kurtosis=0.0,
            cf_var_95_bp=0.0, cf_var_99_bp=0.0,
            worst_case_bp=0.0, max_consecutive_losses=0,
            n_samples=n,
        )

    mu = float(np.mean(arr))
    sigma = float(np.std(arr, ddof=1))

    skew = float(np.mean(((arr - mu) / sigma) ** 3)) if sigma > 0 else 0.0
    kurt = (float(np.mean(((arr - mu) / sigma) ** 4)) - 3.0) if sigma > 0 else 0.0

    v95 = historical_var(arr, 0.95)
    v99 = historical_var(arr, 0.99)
    cv95 = historical_cvar(arr, 0.95)
    cv99 = historical_cvar(arr, 0.99)

    try:
        cf95 = cornish_fisher_var(arr, 0.95)
        cf99 = cornish_fisher_var(arr, 0.99)
    except Exception:
        cf95, cf99 = v95, v99

    worst = abs(float(np.min(arr)))

    return TailRiskResult(
        var_95_bp=v95,
        var_99_bp=v99,
        cvar_95_bp=cv95,
        cvar_99_bp=cv99,
        mean_bp=mu,
        std_bp=sigma,
        skewness=skew,
        excess_kurtosis=kurt,
        cf_var_95_bp=cf95,
        cf_var_99_bp=cf99,
        worst_case_bp=worst,
        max_consecutive_losses=max_consecutive_losses(arr),
        n_samples=n,
    )
