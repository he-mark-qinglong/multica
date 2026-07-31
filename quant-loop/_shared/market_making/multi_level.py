"""Multi-level quoting — post orders at multiple price tiers.

Real market makers don't just quote at one level. They deploy a "ladder"
of orders at increasing distances from mid, with decreasing sizes. This
captures more fills in fast markets while limiting risk at outer tiers.

Jane Street: size and price balance — "balance likelihood of a trade
and the expected profit."

Structure:
  Tier 0 (inside):  tightest spread, largest size — high fill prob, low edge
  Tier 1 (middle):  moderate spread, moderate size — balanced
  Tier 2 (outside): wide spread, small size — low fill prob, high edge
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd


@dataclass(frozen=True)
class TierConfig:
    """Configuration for one quote tier."""

    spread_multiplier: float   # relative to base half-spread (1.0 = base, 2.0 = 2×)
    size_fraction: float       # fraction of total size allocated to this tier


@dataclass(frozen=True)
class TierQuote:
    """A quote at one tier level."""

    tier: int
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float


@dataclass(frozen=True)
class MultiLevelParams:
    """Multi-level quoting configuration."""

    tiers: tuple[TierConfig, ...] = (
        TierConfig(spread_multiplier=1.0, size_fraction=0.50),
        TierConfig(spread_multiplier=2.0, size_fraction=0.30),
        TierConfig(spread_multiplier=4.0, size_fraction=0.20),
    )


def generate_multi_level_quotes(
    reservation_price: float,
    base_half_spread: float,        # fraction (e.g. 0.0003)
    total_size_usd: float,
    inventory_skew_offset: float,   # price units, shifts all quotes
    tick_size: float,
    params: MultiLevelParams = MultiLevelParams(),
    timestamp: pd.Timestamp | None = None,
) -> list[TierQuote]:
    """Generate a ladder of quotes at multiple price tiers.

    Each tier widens the spread by its multiplier and gets its
    fraction of total size. All prices are tick-aligned.
    """
    quotes: list[TierQuote] = []

    for i, tier in enumerate(params.tiers):
        half = base_half_spread * tier.spread_multiplier
        size = total_size_usd * tier.size_fraction

        raw_bid = reservation_price - half - inventory_skew_offset
        raw_ask = reservation_price + half - inventory_skew_offset

        bid = round(raw_bid / tick_size) * tick_size
        ask = round(raw_ask / tick_size) * tick_size

        if bid >= ask or bid <= 0:
            continue

        bid_size = size / bid if bid > 0 else 0.0
        ask_size = size / ask if ask > 0 else 0.0

        quotes.append(TierQuote(
            tier=i,
            bid_price=bid,
            ask_price=ask,
            bid_size=bid_size,
            ask_size=ask_size,
        ))

    return quotes
