"""L2 diff-driven replay engine (B4).

Advances an order book through a time-ordered stream of snapshots and
diffs (see :mod:`_shared.l2.book`) and simulates limit/market order
executions against the *actual* resting depth:

* an order whose limit price **penetrates** the book walks the depth
  level by level, consuming each level's full visible quantity — fills
  are partial when the visible depth runs out (no phantom liquidity).
  Levels priced at-or-inside the limit are takeable in full (taker
  semantics: resting contra interest at your limit matches instantly);
* when the order **does not cross** it joins the queue on its own
  side, behind the volume already resting at-or-better than its price.
  Without per-order queue data we model the expected fill as the
  fill-probability model of
  ``_shared/market_making/queue_position.py`` (Moallemi 2014) applied
  to the order's own quantity, using the count of better-priced own-
  side levels as the tick-distance proxy, plus the order's queue age;
* everything is a pure, deterministic function of the inputs — the
  same event stream and orders always produce the same fills.

References
----------
- Moallemi, C.C. (2014), "The Value of Queue Position in a Limit Order
  Book" — queue-position discount applied at the resting level.
- Cartea, Jaimungal & Penalva (2015), "Algorithmic and High-Frequency
  Trading", Ch. 6-7 — fill probability and market-order walking.
- Cont, Stoikov & Talreja (2010), "A Stochastic Model for Order Book
  Dynamics" — level-by-level depth consumption.

Pure functions + frozen dataclasses only; no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

from _shared.l2.book import BookDiff, BookState, Level
from _shared.market_making.queue_position import QueueParams, fill_probability

__all__ = [
    "L2Fill",
    "ReplayOrder",
    "ReplayPolicy",
    "ReplayResult",
    "REASON_FILLED",
    "REASON_PARTIAL_DEPTH",
    "REASON_PARTIAL_QUEUE",
    "REASON_NOT_TOUCHED",
    "replay",
    "simulate_order",
]

REASON_FILLED = "FILLED"
REASON_PARTIAL_DEPTH = "PARTIAL_DEPTH"
REASON_PARTIAL_QUEUE = "PARTIAL_QUEUE"
REASON_NOT_TOUCHED = "NOT_TOUCHED"

_SIDES = frozenset({"BUY", "SELL"})
_NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class ReplayOrder:
    """An order submitted into the replay.

    ``side``          ``"BUY"`` or ``"SELL"``.
    ``qty``           positive target quantity (base asset).
    ``price``         limit price; ``None`` means a market order.
    ``ts_placed_ns``  submission time; the order first matches against
                      the book state at or after this timestamp.
    """

    order_id: str
    side: str
    qty: float
    price: Optional[float]
    ts_placed_ns: int = 0

    def __post_init__(self) -> None:
        side_u = self.side.upper()
        if side_u not in _SIDES:
            raise ValueError(f"ReplayOrder: side must be BUY/SELL, got {self.side!r}")
        object.__setattr__(self, "side", side_u)
        if self.qty <= 0.0:
            raise ValueError(f"ReplayOrder: qty must be positive, got {self.qty!r}")
        if self.price is not None and self.price <= 0.0:
            raise ValueError(
                f"ReplayOrder: limit price must be positive, got {self.price!r}"
            )

    @property
    def is_market(self) -> bool:
        return self.price is None


@dataclass(frozen=True)
class L2Fill:
    """One fill leg of a replayed order (one per consumed book level)."""

    order_id: str
    ts_ns: int
    price: float
    qty: float
    reason: str


@dataclass(frozen=True)
class ReplayPolicy:
    """Tuning knobs for the L2 replay fill model.

    ``queue_enabled``
        When True (default), a non-crossing order captures the
        queue-model fill probability as a fraction of its own
        quantity; when False, passive orders never fill.
    ``queue_params``
        Parameters forwarded to
        ``_shared.market_making.queue_position.fill_probability``.
    ``market_fill_rate``
        Observed base fill rate for the queue model.
    ``price_epsilon``
        Absolute tolerance for "level price == limit price" (the level
        is takeable rather than strictly inside the limit).
    """

    queue_enabled: bool = True
    queue_params: QueueParams = QueueParams()
    market_fill_rate: float = 0.13
    price_epsilon: float = 1e-9


@dataclass(frozen=True)
class ReplayResult:
    """Output of :func:`replay`."""

    fills: Tuple[L2Fill, ...]
    final_state: BookState
    n_events: int

    def fills_for(self, order_id: str) -> Tuple[L2Fill, ...]:
        return tuple(f for f in self.fills if f.order_id == order_id)


def _resting_fill_qty(
    order_qty: float,
    ticks_from_best: int,
    seconds_in_queue: float,
    policy: ReplayPolicy,
) -> float:
    """Expected fill quantity for a passive (non-crossing) order.

    The queue model returns a probability; we apply it deterministically
    as the *fraction* of the order's own quantity that gets filled
    (expected-fill interpretation — no randomness, so replay stays
    deterministic).
    """
    if not policy.queue_enabled:
        return 0.0
    prob = fill_probability(
        seconds_in_queue=seconds_in_queue,
        ticks_from_best=ticks_from_best,
        market_fill_rate=policy.market_fill_rate,
        params=policy.queue_params,
    )
    return order_qty * prob


def _ticks_from_best(own_levels: Tuple[Level, ...], side: str, price: float,
                     eps: float) -> int:
    """Count own-side levels strictly better than ``price``.

    Used as the tick-distance proxy for the queue model: 0 means the
    order sits at (or inside) the best quote on its side.
    """
    if side == "BUY":
        return sum(1 for p, _ in own_levels if p > price + eps)
    return sum(1 for p, _ in own_levels if p < price - eps)


def simulate_order(
    order: ReplayOrder,
    state: BookState,
    policy: ReplayPolicy = ReplayPolicy(),
) -> Tuple[L2Fill, ...]:
    """Simulate one order against one book state.

    Returns one :class:`L2Fill` per consumed level, plus at most one
    queue-model leg.  A BUY walks the asks from best upward while
    ``level_price <= limit + eps``; a SELL walks the bids downward
    while ``level_price >= limit - eps``.  Walked levels are consumed
    in full (taker semantics).  When nothing is takeable the order
    rests on its own side and fills :func:`_resting_fill_qty` of its
    quantity at its limit price (``REASON_PARTIAL_QUEUE``), or not at
    all (``REASON_NOT_TOUCHED``) when the queue model is disabled.
    """
    levels = state.bids if order.side == "SELL" else state.asks
    seconds_in_queue = max(0.0, (state.ts_ns - order.ts_placed_ns) / _NS_PER_SECOND)

    if order.is_market:
        walk: Tuple[Level, ...] = levels
    else:
        assert order.price is not None
        eps = policy.price_epsilon
        if order.side == "BUY":
            walk = tuple(lv for lv in state.asks if lv[0] <= order.price + eps)
        else:
            walk = tuple(lv for lv in state.bids if lv[0] >= order.price - eps)

    fills: List[L2Fill] = []
    remaining = order.qty
    for price, level_qty in walk:
        if remaining <= 0.0:
            break
        take = min(remaining, level_qty)
        if take > 0.0:
            fills.append(
                L2Fill(
                    order_id=order.order_id,
                    ts_ns=state.ts_ns,
                    price=price,
                    qty=take,
                    reason=REASON_FILLED,
                )
            )
        remaining -= take

    if fills:
        if remaining > 0.0:
            # consumed every takeable level and still short: depth ran out
            last = fills[-1]
            fills[-1] = L2Fill(
                order_id=last.order_id,
                ts_ns=last.ts_ns,
                price=last.price,
                qty=last.qty,
                reason=REASON_PARTIAL_DEPTH,
            )
        return tuple(fills)

    # Nothing takeable: passive order joins its own-side queue.
    if order.is_market:
        # market order with an empty opposite side: no fill possible
        return (
            L2Fill(
                order_id=order.order_id,
                ts_ns=state.ts_ns,
                price=0.0,
                qty=0.0,
                reason=REASON_NOT_TOUCHED,
            ),
        )
    assert order.price is not None
    own_levels = state.bids if order.side == "BUY" else state.asks
    ticks = _ticks_from_best(own_levels, order.side, order.price,
                             policy.price_epsilon)
    qty = _resting_fill_qty(order.qty, ticks, seconds_in_queue, policy)
    if qty > 0.0:
        return (
            L2Fill(
                order_id=order.order_id,
                ts_ns=state.ts_ns,
                price=order.price,
                qty=qty,
                reason=REASON_PARTIAL_QUEUE,
            ),
        )
    return (
        L2Fill(
            order_id=order.order_id,
            ts_ns=state.ts_ns,
            price=order.price,
            qty=0.0,
            reason=REASON_NOT_TOUCHED,
        ),
    )


def replay(
    events: Sequence[Union[BookState, BookDiff]],
    orders: Sequence[ReplayOrder],
    policy: ReplayPolicy = ReplayPolicy(),
) -> ReplayResult:
    """Replay an event stream and match orders against the evolving book.

    ``events`` is a time-ordered mix of full :class:`BookState`
    snapshots and incremental :class:`BookDiff` updates; timestamps
    must be monotonically non-decreasing.  The stream must start with
    a snapshot (a diff before any snapshot raises ``ValueError``).

    Each order is matched once, against the first book state with
    ``ts_ns >= order.ts_placed_ns`` — i.e. the replay clock advances
    event by event, and an order sees the book as it stood at its
    arrival.  Matching is delegated to :func:`simulate_order`.
    """
    state: Optional[BookState] = None
    fills: List[L2Fill] = []
    pending = sorted(orders, key=lambda o: o.ts_placed_ns)
    order_idx = 0
    prev_ts: Optional[int] = None

    for event in events:
        if prev_ts is not None and event.ts_ns < prev_ts:
            raise ValueError(
                f"replay: non-monotonic event stream "
                f"({event.ts_ns} < {prev_ts})"
            )
        prev_ts = event.ts_ns

        if isinstance(event, BookState):
            state = event
        elif isinstance(event, BookDiff):
            if state is None:
                raise ValueError("replay: diff received before any snapshot")
            state = state.apply_diff(event)
        else:
            raise TypeError(f"replay: unsupported event type {type(event)!r}")

        while order_idx < len(pending) and pending[order_idx].ts_placed_ns <= event.ts_ns:
            fills.extend(simulate_order(pending[order_idx], state, policy))
            order_idx += 1

    if state is None:
        raise ValueError("replay: empty event stream")

    # Orders placed after the last event match against the final state.
    while order_idx < len(pending):
        fills.extend(simulate_order(pending[order_idx], state, policy))
        order_idx += 1

    return ReplayResult(
        fills=tuple(fills),
        final_state=state,
        n_events=len(events),
    )
