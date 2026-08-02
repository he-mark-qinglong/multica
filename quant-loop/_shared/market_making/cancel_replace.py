"""Cancel-replace decision engine — amend vs cancel+place vs hold.

Compares the resting order set against a new quote target and emits the
cheapest exchange action per side:

  - **hold** — target matches the resting order (price on the same tick,
    size within tolerance).
  - **amend** — price moved by less than ``amend_threshold_ticks`` ticks
    (same side). Amending preserves queue position and, on most venues,
    costs less than a cancel+replace round trip.
  - **cancel + place** — price moved by at least the threshold, or the
    side disappeared / flipped. Queue position is worthless when the new
    price is far away, so the old order is cancelled and a fresh one is
    placed.

A direction change falls out naturally: the old side has a resting order
with no target (cancel) and the new side has a target with no resting
order (place).

References
----------
  - Guéant, Lehalle & Fernandez-Tapia (2013), "Dealing with the inventory
    risk: a solution to the market making problem", Mathematics and
    Financial Economics — quote re-anchoring as the inventory-control
    actuator.
  - Cont & de Larrard (2013), "Price Dynamics in a Markovian Limit Order
    Market", SIAM J. Financial Math. — queue position value, the reason
    small moves are amended rather than replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

Side = Literal["buy", "sell"]
ActionKind = Literal["hold", "amend", "cancel", "place"]

# Output ordering: free margin first (cancels), then in-place fixes, then
# new orders; holds last. Deterministic for a given input.
_ACTION_ORDER = {"cancel": 0, "amend": 1, "place": 2, "hold": 3}


@dataclass(frozen=True)
class CancelReplaceParams:
    """Tunables for amend-vs-replace decisions."""

    tick_size: float = 0.01
    amend_threshold_ticks: int = 2      # < N ticks → amend; ≥ N → cancel+place
    size_tolerance_fraction: float = 0.01  # size drift below this → still "hold"


@dataclass(frozen=True)
class RestingOrder:
    """An order currently working on the venue."""

    order_id: str
    side: Side
    price: float
    size: float


@dataclass(frozen=True)
class QuoteTarget:
    """Where the strategy wants to be quoting on one side."""

    side: Side
    price: float
    size: float


@dataclass(frozen=True)
class OrderAction:
    """One exchange action. ``order_id`` is None for ``place``."""

    action: ActionKind
    side: Side
    order_id: str | None
    price: float
    size: float
    reason: str


def _first(items: Sequence, side: str):
    for it in items:
        if it.side == side:
            return it
    return None


def _decide_side(
    resting: RestingOrder | None,
    target: QuoteTarget | None,
    params: CancelReplaceParams,
) -> list[OrderAction]:
    """Decide actions for one side (buy or sell)."""
    if resting is None and target is None:
        return []
    if resting is not None and target is None:
        return [OrderAction("cancel", resting.side, resting.order_id,
                            resting.price, resting.size, "target_removed")]
    if resting is None and target is not None:
        return [OrderAction("place", target.side, None,
                            target.price, target.size, "new_quote")]

    assert resting is not None and target is not None
    tick_diff = abs(target.price - resting.price) / params.tick_size
    size_same = (
        abs(target.size - resting.size)
        <= params.size_tolerance_fraction * max(resting.size, 1e-12)
    )

    if tick_diff < 1e-9:
        if size_same:
            return [OrderAction("hold", resting.side, resting.order_id,
                                resting.price, resting.size, "unchanged")]
        return [OrderAction("amend", resting.side, resting.order_id,
                            resting.price, target.size, "size_change")]

    # epsilon keeps exact-threshold moves (float noise like 1.9999...96)
    # on the cancel+replace side: "amend only when strictly below N ticks".
    if tick_diff < params.amend_threshold_ticks - 1e-6:
        reason = f"small_move_{tick_diff:.2f}ticks"
        if not size_same:
            reason += "_and_size"
        return [OrderAction("amend", resting.side, resting.order_id,
                            target.price, target.size, reason)]

    # Large move: queue position at the old price is worthless.
    return [
        OrderAction("cancel", resting.side, resting.order_id,
                    resting.price, resting.size,
                    f"large_move_{tick_diff:.2f}ticks"),
        OrderAction("place", target.side, None,
                    target.price, target.size, "replace"),
    ]


def decide_actions(
    resting: Sequence[RestingOrder],
    targets: Sequence[QuoteTarget],
    params: CancelReplaceParams,
) -> tuple[OrderAction, ...]:
    """Compare resting orders with quote targets; return ordered actions.

    One resting order and one target per side are matched (the first of
    each); extra resting orders on a side are cancelled, extra targets on
    a side are placed as additional levels.
    """
    actions: list[OrderAction] = []
    for side in ("buy", "sell"):
        r_side = [r for r in resting if r.side == side]
        t_side = [t for t in targets if t.side == side]
        actions.extend(_decide_side(
            r_side[0] if r_side else None,
            t_side[0] if t_side else None,
            params,
        ))
        for extra in r_side[1:]:
            actions.append(OrderAction("cancel", extra.side, extra.order_id,
                                       extra.price, extra.size,
                                       "duplicate_side"))
        for extra in t_side[1:]:
            actions.append(OrderAction("place", extra.side, None,
                                       extra.price, extra.size,
                                       "additional_level"))

    actions.sort(key=lambda a: _ACTION_ORDER[a.action])
    return tuple(actions)


__all__ = [
    "CancelReplaceParams",
    "OrderAction",
    "QuoteTarget",
    "RestingOrder",
    "decide_actions",
]
