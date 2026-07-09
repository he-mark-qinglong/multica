"""Multi-pair portfolio skeleton (B2 owner will fill this in).

What's here at B1:
    - `PairAllocation` dataclass — what B2 will mutate as positions open/close.
    - `PortfolioState` dataclass — top-of-book view of all active pairs.
    - `apply_pair_constraints()` — pure helper that clamps per-pair gross to
      the configured ceiling and the leg-notional to `leg_pct_per_pair`. This
      is the only piece of capital allocation that has a single, deterministic
      answer today; the rest (entry timing, exit logic, drawdown halts) belongs
      to B2.

What stays for B2:
    - monthly max-loss pause state machine (`pair_pause_days`, etc.)
    - risk-on / risk-off portfolio-level kill switch
    - rebalance cadence handling per the spec (weekly pair-selection,
      daily z-score, weekly hedge ratio)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PairAllocation:
    """Per-pair position state; B2 mutates this as entries/exits happen."""

    pair_key: str          # e.g. "BTCUSDT-ETHUSDT"
    alpha: float           # OLS intercept on log-prices
    beta: float            # OLS hedge ratio (units of A per unit of B)
    notional_a: float = 0.0   # signed USD notional on the A leg (+long / -short)
    notional_b: float = 0.0   # signed USD notional on the B leg (signed to balance)
    entry_date: Optional[str] = None   # ISO date string of the most recent entry
    is_paused: bool = False
    pause_until: Optional[str] = None  # ISO date string when the pause ends


@dataclass
class PortfolioState:
    """Top-of-book view across all active pairs."""

    starting_capital_usd: float
    cfg: dict
    pairs: Dict[str, PairAllocation] = field(default_factory=dict)
    is_portfolio_paused: bool = False
    portfolio_pause_until: Optional[str] = None

    @property
    def max_active_pairs(self) -> int:
        return int(self.cfg["position_sizing"]["max_active_pairs"])

    @property
    def leg_pct_per_pair(self) -> float:
        return float(self.cfg["position_sizing"]["leg_pct_per_pair"])

    @property
    def max_gross_per_pair(self) -> float:
        return float(self.cfg["position_sizing"]["max_gross_per_pair"])


def apply_pair_constraints(
    desired_a_usd: float,
    desired_b_usd: float,
    cfg: dict,
) -> tuple[float, float]:
    """Clamp each leg to the configured ceiling; pure function for B2 to call.

    Parameters
    ----------
    desired_a_usd, desired_b_usd : float
        Sign-and-magnitude USD notionals the strategy wants to take on each leg.
        `desired_b_usd` already carries the hedge-beta sign (long A → short β*B,
        so desired_b_usd should be negative when desired_a_usd is positive).
    cfg : dict
        Strategy config; reads `position_sizing.leg_pct_per_pair` and
        `position_sizing.max_gross_per_pair`. The constraints are:
            |leg| <= starting_capital * leg_pct_per_pair  (per-leg cap)
            |A| + |β*B| <= starting_capital * max_gross_per_pair  (gross cap)

    Returns
    -------
    (actual_a, actual_b) USD notionals, signed, both within the configured
    bounds. If a clamp kicks in we scale both legs by the same multiplier to
    preserve the market-neutral ratio.

    The `starting_capital_usd` is read from `cfg` — B2 callers should ensure
    the cfg in scope matches the *current* portfolio equity, not the initial
    one, since the cap is in absolute USD not pct-of-equity.
    """
    starting = float(cfg["starting_capital_usd"])
    leg_cap = starting * float(cfg["position_sizing"]["leg_pct_per_pair"])
    gross_cap = starting * float(cfg["position_sizing"]["max_gross_per_pair"])

    a, b = float(desired_a_usd), float(desired_b_usd)
    if a == 0.0 and b == 0.0:
        return 0.0, 0.0

    # Per-leg cap: independent clip, then gross cap: same-multiplier shrink.
    if abs(a) > leg_cap:
        scale = leg_cap / abs(a)
        a *= scale
        b *= scale
    if abs(b) > leg_cap:
        scale = leg_cap / abs(b)
        a *= scale
        b *= scale

    gross = abs(a) + abs(b)
    if gross > gross_cap and gross > 0:
        scale = gross_cap / gross
        a *= scale
        b *= scale

    return float(a), float(b)


__all__ = [
    "PairAllocation",
    "PortfolioState",
    "apply_pair_constraints",
]