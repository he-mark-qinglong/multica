"""Avellaneda-Stoikov analytically optimal spread.

Replaces the heuristic spread (base + vol + inventory + AS penalty) with
the closed-form optimal half-spread from the A-S model:

  δ* = γσ²(T-t)/2  +  ln(1 + γσ²(T-t)/κ) / (γσ²(T-t))

where:
  γ   = risk aversion
  σ   = per-second volatility
  T-t = time remaining
  κ   = order arrival intensity (higher = more competition = tighter spread)

The first term is the inventory-risk compensation; the second is the
monopoly rent from the order-arrival process.

Reference:
  Avellaneda & Stoikov (2008), eq. (14)–(16)
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class OptimalSpreadParams:
    """Parameters for the A-S optimal spread."""

    gamma: float = 0.1           # risk aversion
    kappa: float = 1.5           # order arrival intensity (orders/sec)
    horizon_seconds: float = 300.0
    min_spread_bp: float = 1.0   # floor
    max_spread_bp: float = 50.0  # ceiling


def optimal_half_spread(
    sigma: float,
    time_remaining: float,
    params: OptimalSpreadParams = OptimalSpreadParams(),
) -> float:
    """Compute the A-S optimal half-spread as a fraction of price.

    Returns the half-spread as a dimensionless fraction (e.g. 0.0003 = 3bp).
    When σ=0 or T-t=0, returns the minimum spread.
    """
    g = params.gamma
    sig2 = sigma ** 2
    tau = max(time_remaining, 1e-10)
    kappa = max(params.kappa, 1e-10)

    gt = g * sig2 * tau
    if gt < 1e-12:
        return params.min_spread_bp / 10_000.0

    # δ* = γσ²τ/2 + ln(1 + γσ²τ/κ) / (γσ²τ)
    inventory_comp = gt / 2.0
    arrival_rent = math.log(1.0 + gt / kappa) / gt

    spread_frac = inventory_comp + arrival_rent
    spread_bp = spread_frac * 10_000.0

    return max(params.min_spread_bp, min(params.max_spread_bp, spread_bp)) / 10_000.0


def estimate_kappa(
    fills_per_second: float,
    our_quote_share: float = 0.1,
) -> float:
    """Estimate order arrival intensity κ from observed fill rate.

    Parameters
    ----------
    fills_per_second : float
        Total market trades per second at the best bid/ask.
    our_quote_share : float
        Fraction of book depth we represent (0.1 = we're ~10% of queue).

    Returns
    -------
    float
        Effective κ — higher means more competition (tighter optimal spread).
    """
    if fills_per_second <= 0:
        return 1.5  # default
    # κ scales with market activity but inversely with our share
    return fills_per_second * (1.0 - our_quote_share)
