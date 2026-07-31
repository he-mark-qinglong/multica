"""Adverse-selection guard for market making.

When a maker order is filled, the counter-party likely had a reason.
This module tracks fill events, detects sweeps, applies spread penalties,
and performs a Bayesian-style belief update on the fair value.

Jane Street, "Probability & Markets Guide" — Adverse Selection:
  "If you bought, the correct value of the security is probably a bit
   less than you initially believed."

Empirical calibration:
  T10 maker pre-SPEC measured sweep markout ≈ -1.74 bp/fill on BTCUSDT
  aggTrades (2026-04-19 → 2026-04-22, 5 M trades).

Reference:
  Albers et al. (2025), "The Market Maker's Dilemma", arXiv:2502.18625v2
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd


# Fill-side constants
BID_HIT = "bid_hit"        # our bid was sold into (we bought)
ASK_LIFTED = "ask_lifted"  # our ask was bought from (we sold)


@dataclass(frozen=True)
class AdverseSelectionParams:
    """Tunable thresholds."""

    fill_penalty_bp: float = 1.0             # half-spread penalty per fill
    penalty_decay_per_second: float = 0.5    # bp decay rate
    sweep_threshold: int = 3                 # consecutive same-side fills → sweep
    sweep_cooldown_seconds: float = 5.0      # pause quoting after sweep
    max_penalty_bp: float = 10.0             # penalty ceiling
    expected_sweep_cost_bp: float = 1.74     # T10 empirical calibration


@dataclass(frozen=True)
class AdverseSelectionState:
    """Mutable-by-copy tracking of recent fill pressure."""

    last_fill_side: str | None = None
    last_fill_ts: pd.Timestamp | None = None
    consecutive_same_side: int = 0
    penalty_bp: float = 0.0
    cooldown_until: pd.Timestamp | None = None


def empty_state() -> AdverseSelectionState:
    """Factory for a fresh guard."""
    return AdverseSelectionState()


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def on_fill(
    state: AdverseSelectionState,
    fill_side: str,
    fill_ts: pd.Timestamp,
    params: AdverseSelectionParams,
) -> AdverseSelectionState:
    """Process a fill event.

    1. Track consecutive same-side fills.
    2. If the streak hits ``sweep_threshold``, enter cooldown.
    3. Increase ``penalty_bp`` (capped at ``max_penalty_bp``).
    """
    same_side = (state.last_fill_side == fill_side)
    new_consecutive = state.consecutive_same_side + 1 if same_side else 1

    new_penalty = min(
        state.penalty_bp + params.fill_penalty_bp,
        params.max_penalty_bp,
    )

    cooldown = state.cooldown_until
    if new_consecutive >= params.sweep_threshold:
        cooldown = fill_ts + pd.Timedelta(seconds=params.sweep_cooldown_seconds)

    return AdverseSelectionState(
        last_fill_side=fill_side,
        last_fill_ts=fill_ts,
        consecutive_same_side=new_consecutive,
        penalty_bp=new_penalty,
        cooldown_until=cooldown,
    )


def decay_penalty(
    state: AdverseSelectionState,
    current_ts: pd.Timestamp,
    params: AdverseSelectionParams,
) -> AdverseSelectionState:
    """Apply time-based decay to the spread penalty.

    Also clears the cooldown flag once the timestamp has passed.
    """
    new_penalty = state.penalty_bp
    if state.last_fill_ts is not None:
        elapsed = (current_ts - state.last_fill_ts).total_seconds()
        if elapsed > 0:
            new_penalty = max(
                0.0,
                state.penalty_bp - params.penalty_decay_per_second * elapsed,
            )

    cooldown = state.cooldown_until
    if cooldown is not None and current_ts >= cooldown:
        cooldown = None

    return AdverseSelectionState(
        last_fill_side=state.last_fill_side,
        last_fill_ts=state.last_fill_ts,
        consecutive_same_side=state.consecutive_same_side,
        penalty_bp=new_penalty,
        cooldown_until=cooldown,
    )


def belief_update(
    prior_fair_value: float,
    fill_side: str,
    expected_sweep_cost_bp: float = 1.74,
) -> float:
    """Bayesian belief update — shift fair value against the fill direction.

    Our bid was hit (we bought) → price is probably declining.
    Our ask was lifted (we sold) → price is probably rising.
    """
    cost_fraction = expected_sweep_cost_bp / 10_000.0
    if fill_side == BID_HIT:
        return prior_fair_value * (1.0 - cost_fraction)
    elif fill_side == ASK_LIFTED:
        return prior_fair_value * (1.0 + cost_fraction)
    return prior_fair_value


def is_quoting_allowed(
    state: AdverseSelectionState,
    current_ts: pd.Timestamp,
) -> bool:
    """``False`` during the cooldown window."""
    if state.cooldown_until is not None and current_ts < state.cooldown_until:
        return False
    return True
