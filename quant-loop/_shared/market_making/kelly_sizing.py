"""Kelly criterion position sizing for market making.

Computes the optimal fraction of capital to deploy per quote, based on
the expected edge and variance of outcomes from historical round-trips.

Key insight: in market making, Kelly connects directly to the
Avellaneda-Stoikov framework — the risk-aversion parameter γ that
controls inventory skew IS the Kelly fraction in disguise.

Jane Street, "Probability & Markets Guide":
  "How much are you making? Balance your likelihood of a trade and
   the expected profit."
  "It is bad to lose a lot of money."

Reference:
  Kelly, J.L. (1956), "A New Interpretation of Information Rate"
  Thorp, E.O. (1969), "Optimal Gambling Systems for Favorable Games"
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class KellyParams:
    """Tunables for Kelly-based sizing."""

    fraction: float = 0.25          # fractional Kelly (0.25 = quarter-Kelly)
    min_multiplier: float = 0.1     # floor on sizing multiplier
    max_multiplier: float = 2.0     # cap on sizing multiplier
    min_samples: int = 30           # need at least this many trades
    confidence_threshold_bp: float = 1.0  # skip sizing if mean edge < this


@dataclass(frozen=True)
class KellyResult:
    """Output of Kelly computation."""

    kelly_fraction: float           # raw Kelly f* = μ / σ²
    applied_fraction: float         # f* × params.fraction
    sizing_multiplier: float        # clamped multiplier for quote size
    mean_edge_bp: float             # historical mean edge
    std_edge_bp: float              # historical std of edge
    n_samples: int
    is_valid: bool                  # False if insufficient data


def compute_kelly(
    pnl_history_bp: Sequence[float],
    params: KellyParams = KellyParams(),
) -> KellyResult:
    """Compute Kelly-optimal sizing from a history of per-trade PnL (in bp).

    The continuous Kelly formula:  f* = μ / σ²

    For market making, this tells us what fraction of max inventory to
    deploy. A negative f* means the strategy has negative edge — do not
    size up.

    Parameters
    ----------
    pnl_history_bp : sequence of float
        Historical per-trade PnL in basis points (net of fees).
    params : KellyParams
        Tunables.

    Returns
    -------
    KellyResult
        Contains the raw Kelly fraction, the applied (fractional) value,
        and a sizing multiplier clamped to [min, max].
    """
    arr = np.array(pnl_history_bp, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)

    if n < params.min_samples:
        return KellyResult(
            kelly_fraction=0.0,
            applied_fraction=0.0,
            sizing_multiplier=params.min_multiplier,
            mean_edge_bp=0.0,
            std_edge_bp=0.0,
            n_samples=n,
            is_valid=False,
        )

    mu = float(np.mean(arr))
    sigma2 = float(np.var(arr, ddof=1))

    # Negative edge → do not size up
    if mu < params.confidence_threshold_bp:
        return KellyResult(
            kelly_fraction=0.0,
            applied_fraction=0.0,
            sizing_multiplier=params.min_multiplier,
            mean_edge_bp=mu,
            std_edge_bp=math.sqrt(sigma2) if sigma2 > 0 else 0.0,
            n_samples=n,
            is_valid=True,
        )

    # Kelly formula: f* = μ / σ²
    # Scale: pnl_bp / 1e4 to convert to fraction of capital per trade
    mu_frac = mu / 10_000.0
    var_frac = sigma2 / 1e8  # (bp/1e4)²

    if var_frac <= 0:
        kelly_f = params.max_multiplier
    else:
        kelly_f = mu_frac / var_frac

    # Apply fractional Kelly
    applied = kelly_f * params.fraction

    # Clamp
    multiplier = max(params.min_multiplier, min(params.max_multiplier, applied))

    return KellyResult(
        kelly_fraction=kelly_f,
        applied_fraction=applied,
        sizing_multiplier=multiplier,
        mean_edge_bp=mu,
        std_edge_bp=math.sqrt(sigma2) if sigma2 > 0 else 0.0,
        n_samples=n,
        is_valid=True,
    )


def adaptive_kelly_multiplier(
    pnl_history_bp: Sequence[float],
    params: KellyParams = KellyParams(),
) -> float:
    """Convenience: return just the sizing multiplier."""
    return compute_kelly(pnl_history_bp, params).sizing_multiplier
