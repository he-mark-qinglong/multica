"""Canonical backtest cost assembly (SMA-34967).

Single source of truth for per-side commission + slippage wiring so the
SMA-34900 fee-inclusive slippage plug-in can never be double-counted with
a separate commission parameter.

Background
----------
SMA-34900 set the 15m BTC perp cost baseline as a *fee-inclusive*
plug-in: ``slippage_bps_per_side = 11.0`` already bundles the 4 bps
Binance USDT-M taker fee (spread ~4.5 + impact ~1.4 + fee 4.0 + jitter
~1.1 ≈ 11.0). SMA-34913 showed that wiring that plug-in *and* an
independent ``commission`` charges the fee twice: 15 bps/side, 30 bps
round trip (+36% over-cost), distorting every net metric downstream.

smark ratified the standard (SMA-34913 sign-off cascade): the correct
wiring is ``commission_bps = 4, slippage_bps = 7`` per side — total cost
**11 bps/side = 22 bps round trip, no more, no less**. The 11 bps plug-in
must never be stacked with a separate 4 bps fee.

Resolution rule (issue option "split the fee out of the 11 bps, count it
once"): when a config carries the fee-inclusive plug-in value, the fee is
split out and counted exactly once; any independent commission that would
duplicate it is dropped and flagged. Mutual exclusion is enforced by
construction — a :class:`CostModel` always totals the ratified standard
for the plug-in path instead of raising on legacy configs.

Usage
-----
::

    from backtest.factor_backtester import CostModel

    model = CostModel.from_config(cfg["params"])   # any legacy spelling
    round_trip_cost = model.round_trip_frac         # e.g. 22 bps -> 0.0022
    print(model.ledger_note())                      # for run ledgers
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

# --- Ratified constants (SMA-34900 / SMA-34913, adopted by smark 2026-07-18) ---

#: SMA-34900 fee-inclusive slippage plug-in, bps per side.
SMA34900_PLUGIN_BPS_PER_SIDE: float = 11.0

#: Binance USDT-M taker fee bundled *inside* the plug-in, bps per side.
SMA34900_FEE_BPS_PER_SIDE: float = 4.0

#: Pure slippage component of the plug-in (11.0 - 4.0), bps per side.
SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE: float = (
    SMA34900_PLUGIN_BPS_PER_SIDE - SMA34900_FEE_BPS_PER_SIDE
)  # 7.0

#: Tolerance when matching the plug-in value in legacy configs.
_PLUGIN_ATOL: float = 1e-6

#: Accepted legacy key spellings (first hit wins), commission then slippage.
_FEE_KEYS: Tuple[str, ...] = (
    "commission_bps_per_side",
    "commission_bps",
    "fees_bps_per_side",
    "fee_bps_per_fill",
    "fee_bps_per_side",
)
_SLIPPAGE_KEYS: Tuple[str, ...] = (
    "slippage_bps_per_side",
    "slippage_bps_per_fill",
)


def _first_key(cfg: Mapping, keys: Tuple[str, ...]) -> Tuple[Optional[str], float]:
    for k in keys:
        if k in cfg and cfg[k] is not None:
            return k, float(cfg[k])
    return None, 0.0


@dataclass(frozen=True)
class CostModel:
    """Resolved per-side cost wiring.

    Attributes
    ----------
    commission_bps_per_side:
        Fee component, counted exactly once.
    slippage_bps_per_side:
        *Pure* slippage component (fee excluded).
    hazard_flags:
        Audit trail of any rewiring applied (empty = config was already
        consistent). Surfaced in :meth:`ledger_note` for run ledgers.
    """

    commission_bps_per_side: float
    slippage_bps_per_side: float
    hazard_flags: Tuple[str, ...] = field(default_factory=tuple)

    # -- totals ------------------------------------------------------------
    @property
    def per_side_bps(self) -> float:
        return self.commission_bps_per_side + self.slippage_bps_per_side

    @property
    def round_trip_bps(self) -> float:
        return 2.0 * self.per_side_bps

    @property
    def per_side_frac(self) -> float:
        return self.per_side_bps / 10000.0

    @property
    def round_trip_frac(self) -> float:
        return self.round_trip_bps / 10000.0

    def ledger_note(self) -> str:
        """One-line cost ledger note for run summaries."""
        note = (
            f"cost: fee={self.commission_bps_per_side:g}bps/side + "
            f"slip={self.slippage_bps_per_side:g}bps/side = "
            f"{self.per_side_bps:g}bps/side ({self.round_trip_bps:g}bps RT)"
        )
        if self.hazard_flags:
            note += f" [{', '.join(self.hazard_flags)}]"
        return note

    # -- constructors --------------------------------------------------------
    @classmethod
    def sma34900_baseline(cls) -> "CostModel":
        """Ratified 15m BTC perp baseline: 4 fee + 7 slippage = 22 bps RT."""
        return cls(
            commission_bps_per_side=SMA34900_FEE_BPS_PER_SIDE,
            slippage_bps_per_side=SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE,
        )

    @classmethod
    def from_sma34900_plugin(
        cls, plugin_bps_per_side: float = SMA34900_PLUGIN_BPS_PER_SIDE
    ) -> "CostModel":
        """Split the fee-inclusive SMA-34900 plug-in into its components.

        The plug-in bundles the 4 bps taker fee; this decomposes it into
        ``commission = 4.0`` and ``pure slippage = plugin - 4.0`` so the fee
        is counted exactly once downstream.
        """
        plugin = float(plugin_bps_per_side)
        if plugin < SMA34900_FEE_BPS_PER_SIDE:
            raise ValueError(
                f"plug-in {plugin:g} bps/side is below the bundled "
                f"{SMA34900_FEE_BPS_PER_SIDE:g} bps fee — cannot be fee-inclusive"
            )
        return cls(
            commission_bps_per_side=SMA34900_FEE_BPS_PER_SIDE,
            slippage_bps_per_side=plugin - SMA34900_FEE_BPS_PER_SIDE,
            hazard_flags=("sma34900_plugin_fee_split",),
        )

    @classmethod
    def from_config(cls, cfg: Mapping) -> "CostModel":
        """Resolve any legacy config spelling into a single-counted CostModel.

        Hazard guard (SMA-34967): if ``slippage`` carries the fee-inclusive
        SMA-34900 plug-in value (11.0 bps/side) *and* an independent
        commission/fee key is set (> 0), the fee would be charged twice.
        The plug-in is split and the duplicate fee dropped — resolved total
        is the ratified 11 bps/side = 22 bps round trip — and the rewiring
        is recorded in ``hazard_flags``. An explicit
        ``slippage_includes_fee: true`` flag triggers the same split for
        non-standard plug-in values.
        """
        fee_key, fee = _first_key(cfg, _FEE_KEYS)
        slip_key, slip = _first_key(cfg, _SLIPPAGE_KEYS)
        includes_fee = bool(cfg.get("slippage_includes_fee", False))

        is_plugin = math.isclose(
            slip, SMA34900_PLUGIN_BPS_PER_SIDE, abs_tol=_PLUGIN_ATOL
        )

        if is_plugin or includes_fee:
            model = cls.from_sma34900_plugin(slip)
            flags = list(model.hazard_flags)
            if fee > 0.0:
                # Double-count path confirmed: fee-inclusive plug-in stacked
                # with an independent commission. Count the fee once.
                flags.append("double_count_guarded")
            return cls(
                commission_bps_per_side=model.commission_bps_per_side,
                slippage_bps_per_side=model.slippage_bps_per_side,
                hazard_flags=tuple(flags),
            )

        # Plain wiring: slippage is already pure (e.g. cycle-46 4+1 bps).
        return cls(
            commission_bps_per_side=fee,
            slippage_bps_per_side=slip,
        )


__all__ = [
    "SMA34900_PLUGIN_BPS_PER_SIDE",
    "SMA34900_FEE_BPS_PER_SIDE",
    "SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE",
    "CostModel",
]
