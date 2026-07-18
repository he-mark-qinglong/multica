"""Authoritative execution cost model for quant-loop strategies.

Replaces per-strategy hardcoded 8bp/24bp costs. Default is Binance spot taker
realistic; strategies should call apply_cost() in their bar loop.

References:
- Binance spot fee schedule: 0.1% taker, 0.075% maker (with BNB discount)
- Slippage model: square-root impact, per Torre & Ferraris (1997) / Almgren
"""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Venue:
    name: str
    taker_fee_bps: float   # e.g. 10.0 = 0.10%
    maker_fee_bps: float   # e.g. 7.5  = 0.075%
    has_bnb_discount: bool = False


# Canonical venues used across strategies
BINANCE_SPOT = Venue("binance_spot", taker_fee_bps=10.0, maker_fee_bps=7.5, has_bnb_discount=True)
BINANCE_FUTURES = Venue("binance_usdt_futures", taker_fee_bps=4.0, maker_fee_bps=2.0)
BYBIT_SPOT = Venue("bybit_spot", taker_fee_bps=10.0, maker_fee_bps=10.0)

VENUES = {v.name: v for v in [BINANCE_SPOT, BINANCE_FUTURES, BYBIT_SPOT]}


def slippage_bps(notional_usd: float, adv_usd: float, impact_factor: float = 0.1) -> float:
    """Square-root slippage in basis points.

    Args:
        notional_usd: dollar size of the trade
        adv_usd: average daily volume in USD for the symbol
        impact_factor: empirical multiplier (0.1 = conservative spot, 0.05 = large-cap futures)

    Returns:
        slippage in bps, always non-negative. Caps at 100 bps (10%) to avoid degenerate.
    """
    if adv_usd <= 0:
        return 50.0  # unknown liquidity, assume pessimistic 50bp
    participation = notional_usd / adv_usd
    slip = impact_factor * (participation ** 0.5) * 10000.0
    return min(slip, 100.0)


def apply_cost(
    notional_usd: float,
    adv_usd: float,
    venue: Venue = BINANCE_SPOT,
    side: Literal["taker", "maker"] = "taker",
    impact_factor: float = 0.1,
) -> float:
    """Total round-trip cost in USD for a single-leg entry+exit.

    Returns the dollar cost of entering AND exiting (2x single-leg cost).
    """
    fee_bps = venue.taker_fee_bps if side == "taker" else venue.maker_fee_bps
    if venue.has_bnb_discount and side == "taker":
        fee_bps *= 0.75  # BNB discount
    slip_bps = slippage_bps(notional_usd, adv_usd, impact_factor)
    single_leg_bps = fee_bps + slip_bps
    round_trip_bps = 2 * single_leg_bps
    return notional_usd * round_trip_bps / 10000.0


def cost_as_pct(notional_usd: float, adv_usd: float, **kwargs) -> float:
    """Round-trip cost as a fraction of notional (e.g. 0.0016 = 16bp)."""
    return apply_cost(notional_usd, adv_usd, **kwargs) / notional_usd
