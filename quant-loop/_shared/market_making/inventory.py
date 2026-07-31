"""Inventory state tracking and risk limits for market making.

Immutable update pattern: every function returns a *new* ``InventoryState``
so the simulator can keep a clean audit trail.

Jane Street, "Probability & Markets Guide":
  "It is bad to lose a lot of money, because that means in the future
   when there are great opportunities to trade, you won't have as much
   capital."
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import pandas as pd


@dataclass(frozen=True)
class InventoryState:
    """Net position held by the market maker."""

    net_qty: float = 0.0          # positive = long, negative = short
    gross_qty: float = 0.0        # cumulative two-way volume
    avg_price: float = 0.0        # VWAP cost basis
    notional_usd: float = 0.0
    last_fill_ts: pd.Timestamp | None = None
    max_inventory: float = 1.0    # absolute qty ceiling
    open_since: pd.Timestamp | None = None  # first fill that opened the position

    # -- derived properties ------------------------------------------------

    @property
    def inventory_ratio(self) -> float:
        """``net_qty / max_inventory`` clipped to [-1, 1]."""
        if self.max_inventory <= 0:
            return 0.0
        return max(-1.0, min(1.0, self.net_qty / self.max_inventory))

    @property
    def is_at_limit(self) -> bool:
        """``True`` when |net_qty| has reached ``max_inventory``."""
        return abs(self.net_qty) >= self.max_inventory - 1e-12

    @property
    def is_flat(self) -> bool:
        return abs(self.net_qty) < 1e-12


def update_inventory(
    state: InventoryState,
    fill_qty: float,
    fill_price: float,
    ts: pd.Timestamp,
) -> InventoryState:
    """Apply a fill and return a new :class:`InventoryState`.

    ``fill_qty`` is **signed**: positive = buy (long adding), negative = sell.
    """
    old_net = state.net_qty
    new_net = old_net + fill_qty

    # VWAP cost basis
    old_notional = old_net * state.avg_price
    fill_notional = fill_qty * fill_price
    if abs(new_net) > 1e-12:
        new_avg = (old_notional + fill_notional) / new_net
    else:
        new_avg = 0.0

    new_gross = state.gross_qty + abs(fill_qty)
    new_notional = abs(new_net * fill_price)

    # Track when position was first opened
    open_since = state.open_since
    if old_net == 0.0 and new_net != 0.0:
        open_since = ts
    elif new_net == 0.0:
        open_since = None

    return InventoryState(
        net_qty=new_net,
        gross_qty=new_gross,
        avg_price=new_avg,
        notional_usd=new_notional,
        last_fill_ts=ts,
        max_inventory=state.max_inventory,
        open_since=open_since,
    )


def inventory_skew(net_qty: float, max_inventory: float) -> float:
    """Inventory skew coefficient in [-1, 1].

    Used by the quoting engine to shift bid/ask symmetrically: a long
    inventory shifts quotes *down* (encourage selling); a short inventory
    shifts quotes *up* (encourage buying).
    """
    if max_inventory <= 0:
        return 0.0
    return max(-1.0, min(1.0, net_qty / max_inventory))


def flatten_required(
    state: InventoryState,
    current_price: float,
    max_hold_seconds: float,
    sl_bp: float = 10.0,
    current_ts: pd.Timestamp | None = None,
) -> bool:
    """Decide whether the current inventory must be force-flattened.

    Conditions (any one triggers):
      1. Inventory at or beyond ``max_inventory``.
      2. Position held longer than ``max_hold_seconds``.
      3. Unrealised loss exceeds ``sl_bp`` (basis points).
    """
    if state.is_flat:
        return False

    # 1 — hard cap
    if state.is_at_limit:
        return True

    # 2 — time cap
    if state.open_since is not None and current_ts is not None:
        held = (current_ts - state.open_since).total_seconds()
        if held >= max_hold_seconds:
            return True

    # 3 — stop-loss
    if state.avg_price > 0 and current_price > 0:
        if state.net_qty > 0:
            unrealised_bp = (current_price - state.avg_price) / state.avg_price * 10_000
        else:
            unrealised_bp = (state.avg_price - current_price) / state.avg_price * 10_000
        if unrealised_bp <= -sl_bp:
            return True

    return False


def empty_inventory(max_inventory: float = 1.0) -> InventoryState:
    """Factory for a fresh flat position."""
    return InventoryState(max_inventory=max_inventory)
