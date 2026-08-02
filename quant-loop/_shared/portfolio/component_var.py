"""Portfolio component VaR / CVaR decomposition (I22).

Decomposes total portfolio Value-at-Risk (VaR) and Conditional Value-at-Risk
(CVaR) into per-asset contributions. This answers the question: *which asset
is driving my tail risk?*

Key concepts:

- **VaR** — the α-quantile of the portfolio return distribution (e.g. the 5th
  percentile for 95% VaR). It is the maximum loss not exceeded with
  probability α.
- **CVaR (Expected Shortfall)** — the expected loss conditional on exceeding
  VaR. Coherent risk measure (Rockafellar & Uryasev 2000).
- **Component VaR** — the contribution of asset *i* to total portfolio VaR.
  Computed as: ``Component_VaR_i = Marginal_VaR_i × w_i``.
- **Marginal VaR** — sensitivity of portfolio VaR to a small change in
  asset *i*'s weight. Estimated numerically by finite differences.

For the **historical** method, VaR and CVaR are computed from empirical
quantiles of the realised portfolio return series.

References:
  - Rockafellar & Uryasev (2000), "Optimization of Conditional Value-at-Risk",
    Journal of Risk 2(3), pp. 21–41.
  - Epperlein & Smillie (2006), "Bringing Portfolio Risk Analysis to Life",
    Risk Magazine — component VaR decomposition via marginal contributions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ComponentVaRResult:
    """Result of the component VaR / CVaR decomposition.

    Attributes
    ----------
    var:
        Total portfolio VaR (positive number = loss magnitude).
    cvar:
        Total portfolio CVaR (Expected Shortfall).
    component_var:
        Per-asset contribution to total VaR.
    component_cvar:
        Per-asset contribution to total CVaR.
    marginal_var:
        Sensitivity of portfolio VaR to a 1-unit change in each asset weight.
    confidence:
        Confidence level used (e.g. 0.95).
    """

    var: float
    cvar: float
    component_var: Dict[str, float]
    component_cvar: Dict[str, float]
    marginal_var: Dict[str, float]
    confidence: float


def compute_component_var(
    returns: pd.DataFrame,
    weights: dict,
    confidence: float = 0.95,
    method: str = "historical",
) -> ComponentVaRResult:
    """Compute portfolio VaR, CVaR, and their component decomposition.

    Parameters
    ----------
    returns:
        Asset return DataFrame (columns = assets, rows = periods).
    weights:
        ``{asset: weight}`` dict. Keys must match columns of *returns*.
    confidence:
        Confidence level (e.g. 0.95 for 95% VaR). VaR is the (1 - confidence)
        quantile of returns.
    method:
        ``"historical"`` (default) — use empirical quantiles.

    Returns
    -------
    ComponentVaRResult
    """
    if not 0.5 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0.5, 1.0), got {confidence}")
    if method != "historical":
        raise ValueError(f"method must be 'historical', got {method!r}")

    # Align weights with returns columns.
    assets = [a for a in returns.columns if a in weights]
    if len(assets) < 1:
        raise ValueError("No matching assets between returns and weights")

    w = np.array([float(weights[a]) for a in assets])
    total_w = w.sum()
    if total_w <= 0:
        raise ValueError("Total weight must be positive")
    w = w / total_w  # normalise to sum-1 for correct Euler decomposition
    R = returns[assets].to_numpy(dtype=float)

    # Drop rows with NaN.
    valid_mask = ~np.isnan(R).any(axis=1)
    R = R[valid_mask]
    n = len(R)
    if n < 10:
        raise ValueError("Insufficient non-NaN observations for VaR computation")

    # Portfolio returns.
    portfolio_returns = R @ w

    # VaR: the (1 - confidence) quantile (e.g. 5th percentile for 95% VaR).
    # We report VaR as a positive number (loss magnitude).
    var_quantile = 1.0 - confidence
    var_threshold = float(np.percentile(portfolio_returns, var_quantile * 100))
    var = abs(var_threshold)

    # CVaR: mean of returns at or below the VaR threshold.
    tail = portfolio_returns[portfolio_returns <= var_threshold]
    cvar_threshold = float(tail.mean()) if len(tail) > 0 else var_threshold
    cvar = abs(cvar_threshold)

    # Marginal VaR: finite-difference sensitivity of VaR to weight changes.
    delta = 1e-4  # small perturbation
    marginal_var = {}
    for i, asset in enumerate(assets):
        w_up = w.copy()
        w_up[i] += delta
        w_down = w.copy()
        w_down[i] -= delta
        pr_up = R @ w_up
        pr_down = R @ w_down
        var_up = abs(float(np.percentile(pr_up, var_quantile * 100)))
        var_down = abs(float(np.percentile(pr_down, var_quantile * 100)))
        marginal_var[asset] = (var_up - var_down) / (2 * delta)

    # Component VaR: marginal VaR × weight.
    component_var = {asset: marginal_var[asset] * weights[asset] for asset in assets}

    # Component CVaR: same decomposition for CVaR.
    marginal_cvar = {}
    for i, asset in enumerate(assets):
        w_up = w.copy()
        w_up[i] += delta
        w_down = w.copy()
        w_down[i] -= delta
        pr_up = R @ w_up
        pr_down = R @ w_down
        t_up = pr_up[pr_up <= np.percentile(pr_up, var_quantile * 100)]
        t_down = pr_down[pr_down <= np.percentile(pr_down, var_quantile * 100)]
        cvar_up = abs(float(t_up.mean())) if len(t_up) > 0 else 0.0
        cvar_down = abs(float(t_down.mean())) if len(t_down) > 0 else 0.0
        marginal_cvar[asset] = (cvar_up - cvar_down) / (2 * delta)

    component_cvar = {asset: marginal_cvar[asset] * weights[asset] for asset in assets}

    return ComponentVaRResult(
        var=var,
        cvar=cvar,
        component_var=component_var,
        component_cvar=component_cvar,
        marginal_var=marginal_var,
        confidence=confidence,
    )


__all__ = ["ComponentVaRResult", "compute_component_var"]
