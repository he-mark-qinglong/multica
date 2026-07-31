"""Quote generation engine for market making.

Combines reservation price, inventory skew, adverse-selection penalty,
and MCLS sizing into concrete bid/ask quotes.

Jane Street, "Probability & Markets Guide" — Making Markets:
  "If you know the expected value of something, you should be happy to
   buy for less or sell for more."
  "I'm 2 at 4, 10 up." — bid, offer, size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from _shared.market_making.inventory import InventoryState, inventory_skew
from _shared.market_making.adverse_selection import AdverseSelectionState


@dataclass(frozen=True)
class QuotingParams:
    """Tunables for spread and size computation."""

    base_spread_bp: float = 2.0          # base half-spread (bp)
    min_spread_ticks: int = 2            # minimum spread in ticks
    size_usd: float = 1000.0             # base quote size (USD)
    inventory_skew_factor: float = 1.0   # skew strength multiplier
    vol_spread_coeff: float = 0.5        # vol → spread coefficient
    tick_size: float = 0.01              # BTCUSDT perp tick


@dataclass(frozen=True)
class Quote:
    """A two-sided quote with audit fields."""

    bid_price: float
    ask_price: float
    bid_size: float            # USD notional
    ask_size: float
    timestamp: pd.Timestamp
    reservation_price: float   # anchor used
    spread_bp: float           # actual half-spread (bp)
    skew_bp: float             # inventory skew applied (bp)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _round_to_tick(price: float, tick_size: float) -> float:
    """Snap *price* to the nearest tick boundary."""
    if tick_size <= 0:
        return price
    return round(price / tick_size) * tick_size


def compute_spread(
    sigma: float,
    inventory_ratio: float,
    adverse_selection_penalty: float,
    tick_size: float,
    base_spread_bp: float = 2.0,
    min_spread_ticks: int = 2,
    vol_spread_coeff: float = 0.5,
) -> float:
    """Dynamic half-spread in **price** units.

    Components:
      base + vol_component + inventory_component + adverse_selection

    Floor: ``max(min_spread_ticks * tick_size, computed)``.
    """
    min_spread = min_spread_ticks * tick_size

    base_price = base_spread_bp / 10_000.0  # fraction
    vol_comp = vol_spread_coeff * sigma      # per-second vol scaling
    inv_comp = abs(inventory_ratio) * base_price * 0.5

    total_fraction = base_price + vol_comp + inv_comp + adverse_selection_penalty

    # The caller passes mid_price to convert; here we return the *fraction*
    # — but for direct tick comparison we return a price-space spread at
    # a reference of 1.0.  The quote generator multiplies by mid/reservation.
    # For tick-flooring we need a concrete price, so callers pass the
    # reservation price separately.
    return total_fraction


def _half_spread_price(
    reservation_price: float,
    sigma: float,
    inventory_ratio: float,
    adverse_selection_penalty_bp: float,
    params: QuotingParams,
) -> float:
    """Half-spread in absolute price, floored at ``min_spread_ticks``."""
    min_half = params.min_spread_ticks * params.tick_size

    base = params.base_spread_bp / 10_000.0 * reservation_price
    vol_comp = params.vol_spread_coeff * sigma * reservation_price
    inv_comp = abs(inventory_ratio) * params.base_spread_bp / 10_000.0 * reservation_price * 0.5
    as_comp = adverse_selection_penalty_bp / 10_000.0 * reservation_price

    return max(min_half, base + vol_comp + inv_comp + as_comp)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_quotes(
    reservation_price: float,
    sigma: float,
    inventory_state: InventoryState,
    adverse_selection_penalty_bp: float,
    mcls_size_multiplier: float,
    params: QuotingParams,
    timestamp: pd.Timestamp,
) -> Quote | None:
    """Generate a two-sided quote.

    Returns ``None`` when ``mcls_size_multiplier`` is zero (kill-switch)
    or reservation_price is non-positive.

    Skew logic:
        skew_offset = inventory_ratio * inventory_skew_factor * half_spread
        bid = reservation_price - half_spread - skew_offset
        ask = reservation_price + half_spread - skew_offset

    Long inventory (ratio > 0) → skew_offset > 0 → both quotes shift down
    → encourages selling (reducing the long).
    """
    if reservation_price <= 0 or mcls_size_multiplier <= 0:
        return None

    ratio = inventory_state.inventory_ratio
    half_spread = _half_spread_price(
        reservation_price, sigma, ratio,
        adverse_selection_penalty_bp, params,
    )
    half_spread_bp = half_spread / reservation_price * 10_000.0

    skew_offset = ratio * params.inventory_skew_factor * half_spread
    skew_bp = skew_offset / reservation_price * 10_000.0

    raw_bid = reservation_price - half_spread - skew_offset
    raw_ask = reservation_price + half_spread - skew_offset

    bid_price = _round_to_tick(raw_bid, params.tick_size)
    ask_price = _round_to_tick(raw_ask, params.tick_size)

    # Ensure bid < ask after tick rounding
    if bid_price >= ask_price:
        bid_price = _round_to_tick(reservation_price - half_spread, params.tick_size)
        ask_price = _round_to_tick(reservation_price + half_spread, params.tick_size)
        if bid_price >= ask_price:
            return None

    size = params.size_usd * mcls_size_multiplier
    # When at inventory limit, only quote the reducing side
    if inventory_state.is_at_limit:
        if inventory_state.net_qty > 0:
            return Quote(
                bid_price=0.0, ask_price=ask_price,
                bid_size=0.0, ask_size=size,
                timestamp=timestamp,
                reservation_price=reservation_price,
                spread_bp=half_spread_bp, skew_bp=skew_bp,
            )
        else:
            return Quote(
                bid_price=bid_price, ask_price=0.0,
                bid_size=size, ask_size=0.0,
                timestamp=timestamp,
                reservation_price=reservation_price,
                spread_bp=half_spread_bp, skew_bp=skew_bp,
            )

    return Quote(
        bid_price=bid_price,
        ask_price=ask_price,
        bid_size=size,
        ask_size=size,
        timestamp=timestamp,
        reservation_price=reservation_price,
        spread_bp=half_spread_bp,
        skew_bp=skew_bp,
    )
