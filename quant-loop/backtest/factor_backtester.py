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
from typing import Literal, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

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
    funding_rate_series:
        Optional 8h funding-rate series (indexed by settlement timestamp)
        injected for funding-aware backtests (Phase D / T10). Excluded from
        equality/repr so two models with the same fee wiring stay equal.
        ``None`` = zero funding.
    maker_fee_bps_per_side / taker_fee_bps_per_side:
        Optional execution-side fee split (Phase D sub-taker research).
        When unset, :meth:`maker_taker_cost` falls back to
        ``commission_bps_per_side`` so uniform-fee models are unchanged.
    """

    commission_bps_per_side: float
    slippage_bps_per_side: float
    hazard_flags: Tuple[str, ...] = field(default_factory=tuple)
    funding_rate_series: Optional[pd.Series] = field(
        default=None, compare=False, repr=False
    )
    maker_fee_bps_per_side: Optional[float] = None
    taker_fee_bps_per_side: Optional[float] = None

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
        if (
            self.maker_fee_bps_per_side is not None
            or self.taker_fee_bps_per_side is not None
        ):
            maker = self.maker_fee_bps_per_side
            taker = self.taker_fee_bps_per_side
            note += (
                f"; maker/taker fee="
                f"{maker if maker is not None else self.commission_bps_per_side:g}/"
                f"{taker if taker is not None else self.commission_bps_per_side:g}bps/side"
            )
        if self.funding_rate_series is not None:
            note += f"; funding n={len(self.funding_rate_series)}"
        if self.hazard_flags:
            note += f" [{', '.join(self.hazard_flags)}]"
        return note

    # -- maker/taker fees --------------------------------------------------
    def maker_taker_cost(
        self, notional: float, side: Literal["maker", "taker"]
    ) -> float:
        """Single-leg dollar fee for ``notional`` at the given execution side.

        Uses the side-specific fee when configured; otherwise falls back to
        ``commission_bps_per_side`` so uniform-fee models are backward
        compatible. Slippage is NOT included — this is the fee leg only.
        """
        if side == "maker":
            bps = self.maker_fee_bps_per_side
        elif side == "taker":
            bps = self.taker_fee_bps_per_side
        else:
            raise ValueError(f"side must be 'maker' or 'taker', got {side!r}")
        if bps is None:
            bps = self.commission_bps_per_side
        return float(notional) * float(bps) / 10000.0

    # -- funding -----------------------------------------------------------
    def apply_funding_cost(
        self,
        equity: pd.Series,
        position: pd.Series,
        funding_series: Optional[pd.Series] = None,
    ) -> pd.Series:
        """Apply 8h funding settlements to an equity curve.

        Parameters
        ----------
        equity:
            Equity curve indexed by bar timestamp (sorted ascending).
        position:
            Signed position as a fraction of equity (positive = long),
            aligned on the same bar index.
        funding_series:
            Funding rate per settlement, indexed by settlement timestamp
            (8h cadence for USDT-M perps). Defaults to
            ``self.funding_rate_series``; if neither is set the curve is
            returned unchanged (zero funding).

        Returns
        -------
        pd.Series
            Adjusted equity curve. At each settlement ``t`` the payment
            ``position(t) * equity(t) * rate(t)`` is deducted (positive rate
            → longs pay, shorts receive), using the last bar at or before
            ``t``. Costs accumulate as a drag on all subsequent bars.
        """
        fs = funding_series if funding_series is not None else self.funding_rate_series
        if fs is None or len(fs) == 0:
            return equity.copy()
        if not equity.index.equals(position.index):
            position = position.reindex(equity.index)

        fs = fs.sort_index()
        idx = equity.index
        # Last bar at or before each settlement; settlements before the
        # first bar are ignored.
        locs = idx.searchsorted(fs.index, side="right") - 1
        valid = locs >= 0
        if not valid.any():
            return equity.copy()
        locs = locs[valid]
        rates = fs.to_numpy(dtype=float)[valid]

        eq = equity.to_numpy(dtype=float)
        pos = position.to_numpy(dtype=float)
        costs = np.zeros(len(eq))
        np.add.at(costs, locs, pos[locs] * eq[locs] * rates)
        adjusted = eq - np.cumsum(costs)
        return pd.Series(adjusted, index=idx, name=equity.name)

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

        # Optional Phase-D extensions (pass-through, no rewiring).
        extras = {}
        if cfg.get("maker_fee_bps_per_side") is not None:
            extras["maker_fee_bps_per_side"] = float(cfg["maker_fee_bps_per_side"])
        if cfg.get("taker_fee_bps_per_side") is not None:
            extras["taker_fee_bps_per_side"] = float(cfg["taker_fee_bps_per_side"])
        if cfg.get("funding_rate_series") is not None:
            extras["funding_rate_series"] = cfg["funding_rate_series"]

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
                **extras,
            )

        # Plain wiring: slippage is already pure (e.g. cycle-46 4+1 bps).
        return cls(
            commission_bps_per_side=fee,
            slippage_bps_per_side=slip,
            **extras,
        )


__all__ = [
    "SMA34900_PLUGIN_BPS_PER_SIDE",
    "SMA34900_FEE_BPS_PER_SIDE",
    "SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE",
    "CostModel",
]
