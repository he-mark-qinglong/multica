"""Drawdown risk metrics — CDaR, EDaR, and drawdown duration analysis.

These complement standard max drawdown with distributional drawdown
measures that capture the *typical* drawdown experience, not just the
worst case.

Implements:
  - CDaR (Conditional Drawdown at Risk): average of worst (1-α)% drawdowns
  - EDaR (Expected Drawdown): CVaR of the drawdown distribution
  - Maximum drawdown duration
  - Average drawdown
  - Drawdown deviation (Ulcer Index variant)

References:
  - Chekhlov, Uryasev, Zabarankin (2005) "Drawdown Measure in Portfolio Optimization"
  - Martin & McCann (1989) "The Ulcer Index" (Ulcer Index original)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DrawdownMetrics:
    """Complete drawdown analysis for a return/equity series."""
    max_drawdown: float           # worst peak-to-trough (negative number)
    max_dd_duration: int          # bars from peak to recovery (or end)
    avg_drawdown: float           # average drawdown across all bars
    dd_deviation: float           # std dev of drawdown (Ulcer-like)
    cdar_95: float                # CDaR at 95%: mean of worst 5% drawdowns
    cdar_99: float                # CDaR at 99%
    edar_95: float                # EDaR at 95%: CVaR of drawdown distribution
    edar_99: float                # EDaR at 99%
    ulcer_index: float            # sqrt(mean(dd^2)) — Ulcer Index
    pain_index: float             # mean(|dd|) — Pain Index
    calmar_ratio: float | None    # annualized return / |max DD| (if return provided)
    recovery_factor: float | None # total return / |max DD| (if equity provided)


def _drawdown_series(returns_or_equity: pd.Series | np.ndarray,
                     is_equity: bool = False) -> np.ndarray:
    """Compute the drawdown series from returns or equity curve.

    Returns array of drawdowns (≤ 0 at each point, 0 at new highs).
    """
    arr = np.asarray(returns_or_equity, dtype=float)

    if not is_equity:
        # Build equity curve from returns
        equity = np.cumprod(1 + arr)
    else:
        equity = arr.copy()

    running_max = np.maximum.accumulate(equity)
    # Avoid division by zero
    running_max = np.where(running_max > 0, running_max, 1e-10)
    dd = (equity - running_max) / running_max

    return dd


def compute_drawdown_metrics(
    returns: pd.Series | np.ndarray,
    periods_per_year: int = 365,
    annualized_return: float | None = None,
    total_return: float | None = None,
) -> DrawdownMetrics:
    """Compute comprehensive drawdown metrics from a return series.

    Args:
        returns: periodic returns (e.g., daily or 4h returns).
        periods_per_year: for Calmar ratio calculation.
        annualized_return: pre-computed annualized return (for Calmar).
        total_return: pre-computed total return (for recovery factor).

    Returns:
        DrawdownMetrics with all measures.
    """
    dd = _drawdown_series(returns, is_equity=False)

    # Basic measures
    max_dd = float(np.min(dd))
    avg_dd = float(np.mean(dd))

    # Max drawdown duration
    # Count consecutive bars below previous peak
    in_dd = dd < -1e-10
    max_duration = 0
    current_duration = 0
    for flag in in_dd:
        if flag:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
        else:
            current_duration = 0

    # Drawdown deviation
    dd_std = float(np.std(dd, ddof=1)) if len(dd) > 1 else 0.0

    # CDaR: mean of worst (1-α)% drawdowns
    # Drawdowns are negative; "worst" = most negative
    sorted_dd = np.sort(dd)
    n = len(sorted_dd)

    def cdar(alpha: float) -> float:
        k = max(int(np.ceil(n * (1 - alpha))), 1)
        worst = sorted_dd[:k]
        return float(np.mean(worst))

    cdar_95 = cdar(0.95)
    cdar_99 = cdar(0.99)

    # EDaR: CVaR of the drawdown distribution
    # For a confidence level α, EDaR = -E[DD | DD ≤ VaR_α(DD)]
    def edar(alpha: float) -> float:
        var_idx = int(np.floor(n * (1 - alpha)))
        var_idx = min(max(var_idx, 0), n - 1)
        var_threshold = sorted_dd[var_idx]
        tail = sorted_dd[sorted_dd <= var_threshold]
        if len(tail) == 0:
            return float(var_threshold)
        return float(np.mean(tail))

    edar_95 = edar(0.95)
    edar_99 = edar(0.99)

    # Ulcer Index: sqrt(mean(dd^2))
    ulcer = float(np.sqrt(np.mean(dd ** 2)))

    # Pain Index: mean(|dd|)
    pain = float(np.mean(np.abs(dd)))

    # Calmar ratio
    calmar = None
    if annualized_return is not None and abs(max_dd) > 1e-9:
        calmar = float(annualized_return / abs(max_dd))

    # Recovery factor
    recovery = None
    if total_return is not None and abs(max_dd) > 1e-9:
        recovery = float(total_return / abs(max_dd))

    return DrawdownMetrics(
        max_drawdown=max_dd,
        max_dd_duration=max_duration,
        avg_drawdown=avg_dd,
        dd_deviation=dd_std,
        cdar_95=cdar_95,
        cdar_99=cdar_99,
        edar_95=edar_95,
        edar_99=edar_99,
        ulcer_index=ulcer,
        pain_index=pain,
        calmar_ratio=calmar,
        recovery_factor=recovery,
    )


def cdar_ratio(
    returns: pd.Series | np.ndarray,
    periods_per_year: int = 365,
    confidence: float = 0.95,
) -> float:
    """Return / CDaR ratio (like Calmar but uses CDaR instead of MaxDD).

    A more robust alternative to Calmar that doesn't depend on a single
    worst drawdown observation.

    Args:
        returns: periodic returns.
        periods_per_year: annualization factor.
        confidence: CDaR confidence level.

    Returns:
        Annualized return / |CDaR|.
    """
    arr = np.asarray(returns, dtype=float)
    metrics = compute_drawdown_metrics(arr, periods_per_year)
    n = len(arr)
    years = n / periods_per_year
    equity = np.cumprod(1 + arr)
    ann_ret = float(equity[-1] ** (1.0 / max(years, 1e-9)) - 1.0) if n > 0 else 0.0

    cdar_val = metrics.cdar_95 if confidence == 0.95 else metrics.cdar_99
    return float(ann_ret / abs(cdar_val)) if abs(cdar_val) > 1e-9 else 0.0


def portfolio_drawdown_decomposition(
    weights: np.ndarray,
    asset_returns: pd.DataFrame,
) -> pd.DataFrame:
    """Decompose portfolio drawdown into per-asset contributions.

    Uses marginal drawdown: how much each asset contributed to the
    portfolio's drawdown at each point in time.

    Args:
        weights: portfolio weights (sum to 1).
        asset_returns: DataFrame of per-asset returns.

    Returns:
        DataFrame with columns: portfolio_dd, and per-asset dd contributions.
    """
    w = np.asarray(weights, dtype=float)
    R = asset_returns.values
    port_returns = R @ w
    port_dd = _drawdown_series(port_returns)

    # Per-asset drawdown contribution
    # At each point: contribution_i = w_i * R_i / portfolio_equity
    equity = np.cumprod(1 + port_returns)
    running_max = np.maximum.accumulate(equity)

    contributions = {}
    for j, col in enumerate(asset_returns.columns):
        asset_equity = np.cumprod(1 + R[:, j])
        asset_running_max = np.maximum.accumulate(asset_equity)
        asset_dd = (asset_equity - asset_running_max) / np.maximum(asset_running_max, 1e-10)
        contributions[f"{col}_dd"] = w[j] * asset_dd

    result = pd.DataFrame({"portfolio_dd": port_dd}, index=asset_returns.index)
    for name, vals in contributions.items():
        result[name] = vals

    return result
