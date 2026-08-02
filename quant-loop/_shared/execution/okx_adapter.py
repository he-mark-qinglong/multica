"""OKX perpetual exchange adapter skeleton (E8).

Provides a clean adapter for the OKX perpetual swap venue, following the same
pattern as :mod:`_shared.execution.lighter_adapter`. OKX Tier-1 (VIP1) fees:

- **Maker**: 2 bps (0.02%)
- **Taker**: 5 bps (0.05%)

This is a **skeleton** — no actual API calls are made. It provides the
:class:`Venue`, fee calculation, and round-trip cost estimation needed for
backtesting and cost-model integration.

References:
  - OKX Fee Schedule (okx.com) — VIP1 perpetual swap fees.
  - :mod:`_shared.execution.cost_model` — Venue / apply_cost pattern.
  - :mod:`_shared.execution.lighter_adapter` — adapter template.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from _shared.execution.cost_model import Venue


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OKXConfig:
    """Fee and latency configuration for OKX perpetual swap venue.

    Defaults match OKX VIP1 perpetual swap fees (maker 2bp / taker 5bp).
    Override any field to model higher VIP tiers or different latency regimes.
    """

    maker_fee_bps: float = 2.0        # VIP1 maker
    taker_fee_bps: float = 5.0        # VIP1 taker
    # Estimated fill latency in milliseconds (OKX API round-trip).
    maker_latency_ms: float = 100.0
    taker_latency_ms: float = 50.0
    # Additional non-fee slippage (spread crossing, impact) in bps/side.
    extra_slippage_bps: float = 0.0


# ---------------------------------------------------------------------------
# Venue builder
# ---------------------------------------------------------------------------

def okx_venue(config: Optional[OKXConfig] = None) -> Venue:
    """Build a cost_model :class:`Venue` for the OKX perpetual swap venue.

    The returned Venue matches the ``OKX_PERP`` constant already registered in
    :mod:`cost_model` (maker 2bp, taker 5bp, no fixed slippage).
    """
    cfg = config or OKXConfig()
    return Venue(
        name="okx_swap",
        taker_fee_bps=cfg.taker_fee_bps,
        maker_fee_bps=cfg.maker_fee_bps,
        has_bnb_discount=False,
        fixed_pure_slippage_bps=None,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class OKXAdapter:
    """Skeleton adapter for the OKX perpetual swap venue.

    Combines fee economics with cost estimation. Intended for backtesting and
    cost-model integration — no live API calls.

    Usage::

        adapter = OKXAdapter()
        rt_cost_bps = adapter.round_trip_cost_bps(side="taker")

    Parameters
    ----------
    config:
        :class:`OKXConfig` with fee/latency settings. Defaults to VIP1.
    """

    def __init__(self, config: Optional[OKXConfig] = None) -> None:
        self.config = config or OKXConfig()

    @property
    def venue(self) -> Venue:
        """The cost_model Venue for this adapter's fee configuration."""
        return okx_venue(self.config)

    # -- fee economics ------------------------------------------------------

    def fee_bps(self, side: Literal["maker", "taker"]) -> float:
        """Single-leg fee in bps for the given execution side.

        Parameters
        ----------
        side:
            ``"maker"`` or ``"taker"``.

        Returns
        -------
        float
            Fee in basis points for one side.
        """
        if side == "maker":
            return self.config.maker_fee_bps
        elif side == "taker":
            return self.config.taker_fee_bps
        raise ValueError(f"side must be 'maker' or 'taker', got {side!r}")

    def round_trip_fee_bps(self, side: Literal["maker", "taker"] = "taker") -> float:
        """Total fee cost (entry + exit) in bps for a round-trip trade.

        Both legs use the same execution side. For mixed strategies (maker
        entry, taker exit) sum the two single-leg fees manually.
        """
        return 2.0 * self.fee_bps(side)

    def round_trip_cost_bps(
        self,
        side: Literal["maker", "taker"] = "taker",
        slippage_bps: float = 0.0,
    ) -> float:
        """Total round-trip cost in bps: fees + slippage.

        Parameters
        ----------
        side:
            Execution side for fee lookup.
        slippage_bps:
            Pre-computed slippage in bps per side (default 0 — caller supplies
            from their impact model).

        Returns
        -------
        float
            Total cost in basis points for entry + exit.
        """
        fee_rt = self.round_trip_fee_bps(side)
        slip_rt = 2.0 * slippage_bps
        return fee_rt + slip_rt

    # -- introspection ------------------------------------------------------

    def describe(self) -> str:
        """One-line description for run ledgers."""
        c = self.config
        return (
            f"OKX(maker={c.maker_fee_bps:g}bp, taker={c.taker_fee_bps:g}bp, "
            f"RT_taker={self.round_trip_fee_bps('taker'):g}bp)"
        )


__all__ = ["OKXConfig", "OKXAdapter", "okx_venue"]
