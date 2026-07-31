"""Online learning of adverse selection cost.

Instead of a fixed expected_sweep_cost_bp = 1.74 (from T10 static
measurement), this module maintains an exponentially-weighted moving
average (EWMA) of *observed* post-fill markouts. The learned parameter
adapts to changing market conditions.

Jane Street: "Our perceived probabilities of events change as we learn
more information about the circumstances."
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class OnlineASParams:
    """Online adverse selection learning parameters."""

    alpha: float = 0.05             # EWMA weight (0.05 = 5% new data per update)
    min_observations: int = 10      # need this many before trusting learned value
    prior_bp: float = 1.74          # T10 static measurement as prior
    min_cost_bp: float = 0.0        # floor (can't be negative — no edge from fills)
    max_cost_bp: float = 20.0       # ceiling


@dataclass
class OnlineASState:
    """Mutable (by convention) state for online learning."""

    learned_cost_bp: float          # current EWMA estimate
    n_observations: int             # total fills observed
    last_update_ts: pd.Timestamp | None = None


def init_online_as(
    params: OnlineASParams = OnlineASParams(),
) -> OnlineASState:
    """Initialize with the T10 prior."""
    return OnlineASState(
        learned_cost_bp=params.prior_bp,
        n_observations=0,
    )


def observe_fill(
    state: OnlineASState,
    observed_markout_bp: float,
    fill_ts: pd.Timestamp,
    params: OnlineASParams = OnlineASParams(),
) -> OnlineASState:
    """Update the learned adverse selection cost with a new observation.

    The observed markout should be the signed post-fill price drift
    (negative = adverse selection cost). We take the absolute value
    since the cost is always against us.

    Parameters
    ----------
    state : OnlineASState
        Current learning state.
    observed_markout_bp : float
        Post-fill mid-price drift in bp (typically negative).
    fill_ts : pd.Timestamp
        Timestamp of the observation.
    params : OnlineASParams
        Learning parameters.
    """
    cost = abs(observed_markout_bp)

    if state.n_observations == 0:
        # First observation — replace prior entirely
        new_cost = cost
    else:
        # EWMA update
        new_cost = (1 - params.alpha) * state.learned_cost_bp + params.alpha * cost

    # Clamp
    new_cost = max(params.min_cost_bp, min(params.max_cost_bp, new_cost))

    return OnlineASState(
        learned_cost_bp=new_cost,
        n_observations=state.n_observations + 1,
        last_update_ts=fill_ts,
    )


def get_effective_cost(
    state: OnlineASState,
    params: OnlineASParams = OnlineASParams(),
) -> float:
    """Get the effective adverse selection cost for quoting decisions.

    Before enough observations are collected, returns the T10 prior.
    After that, returns the learned value with a confidence discount
    that shrinks as observations accumulate.
    """
    if state.n_observations < params.min_observations:
        return params.prior_bp

    # Confidence-weighted blend: as n grows, trust learned value more
    confidence = min(1.0, state.n_observations / (params.min_observations * 5))
    blended = (1 - confidence) * params.prior_bp + confidence * state.learned_cost_bp

    return max(params.min_cost_bp, min(params.max_cost_bp, blended))


def adaptive_belief_update(
    prior_fair_value: float,
    fill_side: str,
    state: OnlineASState,
    params: OnlineASParams = OnlineASParams(),
) -> float:
    """Like belief_update but uses the *learned* cost instead of fixed 1.74bp."""
    cost_bp = get_effective_cost(state, params)
    cost_fraction = cost_bp / 10_000.0

    from _shared.market_making.adverse_selection import BID_HIT, ASK_LIFTED
    if fill_side == BID_HIT:
        return prior_fair_value * (1.0 - cost_fraction)
    elif fill_side == ASK_LIFTED:
        return prior_fair_value * (1.0 + cost_fraction)
    return prior_fair_value
