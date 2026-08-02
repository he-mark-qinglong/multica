"""Backtest partial-fill simulator (B5).

Given an order (price, qty) and a single OHLCV bar, decide whether the
order would have executed inside the bar and, if so, how much of it.

The naive backtest assumption ("limit touched => full fill") is
systematically optimistic: a bar's traded volume is shared between every
participant, and a limit order that is *barely* touched only competes
for the volume that actually traded at-or-through its price.  This
module replaces that assumption with a conservative, volume-aware fill
model:

* the strategy may capture at most ``participation_rate`` of the bar's
  volume (the rest belongs to other participants) — the classic
  participation-rate bound used throughout the optimal-execution
  literature;
* a limit order whose price lies strictly *inside* the bar's range
  (traded through) competes for the full participation budget;
* a limit order that is only *marginally* touched (bar extreme ==
  limit price) competes for ``touch_fill_factor`` of the budget —
  conservative mode, because without intra-bar data we cannot know
  whether the touch happened at the start or the end of the bar;
* a market order fills at the bar open, capped by the same
  participation budget;
* when several orders compete on the same bar,
  :func:`simulate_bar_fills` drains a shared volume budget in order,
  so the first order in queue gets the volume (queue-priority
  modelling lives in ``_shared/market_making/queue_position.py``;
  here we model the *aggregate* budget only).

Fill prices are conservative by default: a limit order fills at its
limit price (no price improvement), a market order fills at the bar
open.  Set ``allow_price_improvement=True`` to let a limit order fill
at the bar open when the open gaps through the limit.

References
----------
- Almgren & Chriss (2000), "Optimal Execution of Portfolio
  Transactions" — participation-rate bounded execution.
- Kyle (1985), "Continuous Auctions and Insider Trading" — price
  impact of order flow.
- Cartea, Jaimungal & Penalva (2015), "Algorithmic and High-Frequency
  Trading", Ch. 6 — fill-probability models for limit orders.
- Cont, Stoikov & Talreja (2010), "A Stochastic Model for Order Book
  Dynamics" — volume-at-price interpretation of bar extremes.

Pure functions + frozen dataclasses only; no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

__all__ = [
    "DEFAULT_PARTIAL_FILL_POLICY",
    "Bar",
    "Fill",
    "FILL_FILLED",
    "FILL_NOT_TOUCHED",
    "FILL_PARTIAL_TOUCH",
    "FILL_PARTIAL_VOLUME_CAP",
    "OrderSpec",
    "PartialFillPolicy",
    "fill_price",
    "is_touched",
    "simulate_bar_fill",
    "simulate_bar_fills",
]

FILL_FILLED = "FILLED"
FILL_PARTIAL_VOLUME_CAP = "PARTIAL_VOLUME_CAP"
FILL_PARTIAL_TOUCH = "PARTIAL_TOUCH"
FILL_NOT_TOUCHED = "NOT_TOUCHED"

_SIDES = frozenset({"BUY", "SELL"})


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar.  ``volume`` is in base-asset units (same unit as
    :attr:`OrderSpec.qty`)."""

    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(
                f"Bar: high ({self.high}) < low ({self.low})"
            )
        if self.volume < 0.0:
            raise ValueError(f"Bar: negative volume {self.volume!r}")
        for name in ("open", "high", "low", "close"):
            if getattr(self, name) <= 0.0:
                raise ValueError(f"Bar: {name} must be positive")


@dataclass(frozen=True)
class OrderSpec:
    """An order to be filled inside a bar.

    ``side``        ``"BUY"`` or ``"SELL"``.
    ``qty``         positive target quantity (base asset).
    ``price``       limit price; ``None`` (or
    ``order_type="MARKET"``) means a market order.
    """

    order_id: str
    side: str
    qty: float
    price: Optional[float] = None
    order_type: str = "LIMIT"

    def __post_init__(self) -> None:
        side_u = self.side.upper()
        if side_u not in _SIDES:
            raise ValueError(f"OrderSpec: side must be BUY/SELL, got {self.side!r}")
        object.__setattr__(self, "side", side_u)
        if self.qty <= 0.0:
            raise ValueError(f"OrderSpec: qty must be positive, got {self.qty!r}")
        if self.is_market:
            if self.price is not None:
                raise ValueError("OrderSpec: MARKET order must have price=None")
        elif self.price is None or self.price <= 0.0:
            raise ValueError(
                f"OrderSpec: LIMIT order requires a positive price, "
                f"got {self.price!r}"
            )

    @property
    def is_market(self) -> bool:
        return self.order_type.upper() == "MARKET"


@dataclass(frozen=True)
class Fill:
    """The (possibly partial) execution of one order inside one bar."""

    order_id: str
    ts_ns: int
    price: float
    qty: float
    fill_ratio: float
    remaining_qty: float
    reason: str

    @property
    def is_filled(self) -> bool:
        return self.reason == FILL_FILLED


@dataclass(frozen=True)
class PartialFillPolicy:
    """Tuning knobs for the conservative fill model.

    ``participation_rate``
        Maximum fraction of the bar's volume the strategy may capture.
        Default 0.25 — a common ceiling in optimal-execution schedules.
    ``touch_fill_factor``
        Fraction of the participation budget available when the bar
        only *marginally* touches the limit price (bar extreme ==
        limit).  Default 0.5 (conservative: without intra-bar data the
        touch may have happened at the last trade of the bar).
    ``allow_price_improvement``
        When True, a limit order that the bar's open gaps through
        fills at the open (better price).  Default False (conservative:
        fill at the limit price).
    ``price_epsilon``
        Absolute tolerance when comparing the bar extreme to the
        limit price.
    """

    participation_rate: float = 0.25
    touch_fill_factor: float = 0.5
    allow_price_improvement: bool = False
    price_epsilon: float = 1e-9

    def __post_init__(self) -> None:
        if not (0.0 < self.participation_rate <= 1.0):
            raise ValueError(
                f"participation_rate must be in (0, 1], "
                f"got {self.participation_rate!r}"
            )
        if not (0.0 < self.touch_fill_factor <= 1.0):
            raise ValueError(
                f"touch_fill_factor must be in (0, 1], "
                f"got {self.touch_fill_factor!r}"
            )
        if self.price_epsilon < 0.0:
            raise ValueError("price_epsilon must be non-negative")


DEFAULT_PARTIAL_FILL_POLICY = PartialFillPolicy()


def is_touched(
    order: OrderSpec,
    bar: Bar,
    policy: PartialFillPolicy = DEFAULT_PARTIAL_FILL_POLICY,
) -> bool:
    """True when the bar's price range reaches the order's limit.

    Market orders are always touched.  A BUY limit is touched when
    ``bar.low <= price + eps``; a SELL limit when
    ``bar.high >= price - eps``.
    """
    if order.is_market:
        return True
    assert order.price is not None  # for the type checker
    if order.side == "BUY":
        return bar.low <= order.price + policy.price_epsilon
    return bar.high >= order.price - policy.price_epsilon


def _is_marginal_touch(
    order: OrderSpec,
    bar: Bar,
    policy: PartialFillPolicy,
) -> bool:
    """True when the bar extreme equals the limit price (the order was
    touched but never traded through)."""
    assert order.price is not None
    if order.side == "BUY":
        return abs(bar.low - order.price) <= policy.price_epsilon
    return abs(bar.high - order.price) <= policy.price_epsilon


def fill_price(
    order: OrderSpec,
    bar: Bar,
    policy: PartialFillPolicy = DEFAULT_PARTIAL_FILL_POLICY,
) -> float:
    """Execution price under the conservative model.

    * market order -> bar open;
    * limit order -> limit price, unless the open gaps through the
      limit and ``policy.allow_price_improvement`` is set, in which
      case the (better) open price is used.
    """
    if order.is_market:
        return bar.open
    assert order.price is not None
    if policy.allow_price_improvement:
        if order.side == "BUY" and bar.open < order.price:
            return bar.open
        if order.side == "SELL" and bar.open > order.price:
            return bar.open
    return order.price


def _fill_with_budget(
    order: OrderSpec,
    bar: Bar,
    budget: float,
    policy: PartialFillPolicy,
) -> Tuple[Fill, float]:
    """Fill ``order`` against at most ``budget`` units of bar volume.

    Returns ``(fill, budget_consumed)``.  ``fill.qty`` may be ``0.0``
    when the order was not touched or no budget remains.
    """
    if not is_touched(order, bar, policy):
        return (
            Fill(
                order_id=order.order_id,
                ts_ns=bar.ts_ns,
                price=fill_price(order, bar, policy),
                qty=0.0,
                fill_ratio=0.0,
                remaining_qty=order.qty,
                reason=FILL_NOT_TOUCHED,
            ),
            0.0,
        )

    budget = max(0.0, min(budget, bar.volume * policy.participation_rate))
    if not order.is_market and _is_marginal_touch(order, bar, policy):
        budget *= policy.touch_fill_factor
        partial_reason = FILL_PARTIAL_TOUCH
    else:
        partial_reason = FILL_PARTIAL_VOLUME_CAP

    qty_filled = min(order.qty, budget)
    price = fill_price(order, bar, policy)
    if qty_filled >= order.qty:
        reason = FILL_FILLED
    else:
        reason = partial_reason
    return (
        Fill(
            order_id=order.order_id,
            ts_ns=bar.ts_ns,
            price=price,
            qty=qty_filled,
            fill_ratio=qty_filled / order.qty,
            remaining_qty=order.qty - qty_filled,
            reason=reason,
        ),
        qty_filled,
    )


def simulate_bar_fill(
    order: OrderSpec,
    bar: Bar,
    policy: PartialFillPolicy = DEFAULT_PARTIAL_FILL_POLICY,
) -> Fill:
    """Simulate a single order against a single bar.

    Always returns a :class:`Fill`; an untouched order yields
    ``qty == 0.0`` and ``reason == FILL_NOT_TOUCHED``.
    """
    fill, _ = _fill_with_budget(
        order, bar, bar.volume * policy.participation_rate, policy,
    )
    return fill


def simulate_bar_fills(
    orders: Sequence[OrderSpec],
    bar: Bar,
    policy: PartialFillPolicy = DEFAULT_PARTIAL_FILL_POLICY,
) -> List[Fill]:
    """Simulate several orders competing for one bar's volume.

    Orders are processed in sequence order; the bar's participation
    budget (``bar.volume * participation_rate``) is shared between
    them, so an order later in the sequence can only capture volume
    the earlier orders left behind.  Untouched orders consume no
    budget.
    """
    budget = bar.volume * policy.participation_rate
    fills: List[Fill] = []
    for order in orders:
        fill, consumed = _fill_with_budget(order, bar, budget, policy)
        budget = max(0.0, budget - consumed)
        fills.append(fill)
    return fills
