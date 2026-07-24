"""Authoritative execution cost model for quant-loop strategies.

Replaces per-strategy hardcoded 8bp/24bp costs. Strategies should call
apply_cost() in their bar loop.

Two cost paths:

- **Futures (BINANCE_FUTURES)** — unified with the ratified SMA-34900/SMA-34913
  standard from ``backtest.factor_backtester``: 4 bps taker fee + 7 bps pure
  slippage per side = 11 bps/side = 22 bps round trip, independent of trade
  size. The constants are imported from ``backtest.factor_backtester`` (single
  source of truth); ``CostModel.sma34900_baseline()`` and
  ``apply_cost(..., venue=BINANCE_FUTURES, side="taker")`` are equivalent.

- **Spot (BINANCE_SPOT / BYBIT_SPOT)** — legacy size-dependent path kept for
  spot strategies: venue taker/maker fee (with optional BNB discount) plus a
  square-root market-impact slippage model (Torre & Ferraris 1997 / Almgren).
  Limitations: the sqrt-impact parameters are *not* ratified against any
  empirical fill study, so small notionals produce near-zero slippage and
  understate real cost. Do NOT use the spot path for USDT-M perp backtests —
  use BINANCE_FUTURES so the cost matches ``factor_backtester.CostModel``.

References:
- Binance spot fee schedule: 0.1% taker, 0.075% maker (with BNB discount)
- Slippage model: square-root impact, per Torre & Ferraris (1997) / Almgren
- SMA-34900 / SMA-34913 ratified futures cost: see backtest/factor_backtester.py
"""
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional


def _load_ratified_constants():
    """Import the ratified SMA-34900 constants from backtest.factor_backtester.

    cost_model.py is imported both as a package module
    (``_shared.execution.cost_model``) and as a bare top-level module
    (``from cost_model import ...`` after a sys.path insert in strategy
    directories). Deriving the repo root from ``__file__`` makes the import
    work in both modes without duplicating the constants.
    """
    try:
        from backtest.factor_backtester import (
            SMA34900_FEE_BPS_PER_SIDE,
            SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE,
        )
    except ImportError:
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from backtest.factor_backtester import (
            SMA34900_FEE_BPS_PER_SIDE,
            SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE,
        )
    return SMA34900_FEE_BPS_PER_SIDE, SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE


SMA34900_FEE_BPS_PER_SIDE, SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE = (
    _load_ratified_constants()
)


@dataclass(frozen=True)
class Venue:
    name: str
    taker_fee_bps: float   # e.g. 10.0 = 0.10%
    maker_fee_bps: float   # e.g. 7.5  = 0.075%
    has_bnb_discount: bool = False
    # Ratified fixed pure-slippage per side (bps). When set, apply_cost uses
    # this instead of the sqrt-impact model, making cost size-independent.
    fixed_pure_slippage_bps: Optional[float] = None


# Canonical venues used across strategies.
#
# Spot venues keep the legacy sqrt-impact path (see module docstring for
# limitations). BINANCE_FUTURES is wired to the ratified SMA-34900 standard:
# 4 bps taker fee + 7 bps pure slippage per side (22 bps round trip), exactly
# matching CostModel.sma34900_baseline() in backtest/factor_backtester.py.
BINANCE_SPOT = Venue("binance_spot", taker_fee_bps=10.0, maker_fee_bps=7.5, has_bnb_discount=True)
BINANCE_FUTURES = Venue(
    "binance_usdt_futures",
    taker_fee_bps=SMA34900_FEE_BPS_PER_SIDE,            # 4.0 (ratified)
    maker_fee_bps=2.0,
    fixed_pure_slippage_bps=SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE,  # 7.0 (ratified)
)
BYBIT_SPOT = Venue("bybit_spot", taker_fee_bps=10.0, maker_fee_bps=10.0)

VENUES = {v.name: v for v in [BINANCE_SPOT, BINANCE_FUTURES, BYBIT_SPOT]}


def slippage_bps(notional_usd: float, adv_usd: float, impact_factor: float = 0.1) -> float:
    """Square-root slippage in basis points (spot path only).

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

    For venues with ``fixed_pure_slippage_bps`` set (BINANCE_FUTURES), the
    slippage is the ratified constant and ``adv_usd`` / ``impact_factor`` are
    ignored: taker round trip is exactly 22 bps of notional, matching
    ``CostModel.sma34900_baseline().round_trip_frac``.
    """
    fee_bps = venue.taker_fee_bps if side == "taker" else venue.maker_fee_bps
    if venue.has_bnb_discount and side == "taker":
        fee_bps *= 0.75  # BNB discount
    if venue.fixed_pure_slippage_bps is not None:
        slip_bps = venue.fixed_pure_slippage_bps
    else:
        slip_bps = slippage_bps(notional_usd, adv_usd, impact_factor)
    single_leg_bps = fee_bps + slip_bps
    round_trip_bps = 2 * single_leg_bps
    return notional_usd * round_trip_bps / 10000.0


def cost_as_pct(notional_usd: float, adv_usd: float, **kwargs) -> float:
    """Round-trip cost as a fraction of notional (e.g. 0.0016 = 16bp)."""
    return apply_cost(notional_usd, adv_usd, **kwargs) / notional_usd
