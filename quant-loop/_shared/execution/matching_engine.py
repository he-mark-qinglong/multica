"""Price-time priority matching engine for backtest/paper trading (B-category).

A lightweight limit-order-book matching engine that implements:

1. **Price-time priority** — orders at the best price fill first; ties broken
   by arrival time (FIFO). This is the standard CLOB matching rule used by
   Binance, CME, and most equity/perv venues.
2. **FIFO queues** — orders at the same price level are kept in a deque and
   matched in arrival order, so a faster queue-position means earlier fills.
3. **Duplicate-ack guard** — every fill is assigned a unique ``ack_id``;
   re-submitting the same ``ack_id`` is a no-op (idempotent fill), preventing
   the classic race where a WS reconnect replays a fill and double-counts it.
4. **Market-order crossing** — market orders sweep available liquidity until
   filled or the book is empty.

The engine is *deterministic*: given the same sequence of order events, it
produces exactly the same fills. No randomness, no clock dependency.

Design
------
- :class:`Order` — immutable order representation (buy/sell, limit/market,
  price, qty, ack_id).
- :class:`Fill` — immutable fill record (price, qty, ack_id, taker_side).
- :class:`MatchingEngine` — stateful book with ``bid_levels`` (descending)
  and ``ask_levels`` (ascending), each a deque of resting orders.
- :meth:`MatchingEngine.process` — submit one order, return zero or more fills.
- :meth:`MatchingEngine.cancel` — remove a resting order by ack_id.

References
----------
- Gould, Porter, Williams, McDonald, Fenn & Howison (2013),
  "Limit order books", Quantitative Finance 13(11) — the canonical LOB survey.
- Avellaneda & Stoikov (2008), "High-frequency trading in a limit order book"
  — the reservation-price / spread framework that this engine feeds into.
- Binance USDⓈ-M matching engine docs — price-time priority, FIFO queues.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Order:
    """One order submitted to the engine.

    Attributes:
        ack_id: caller-assigned unique identifier for idempotency.
            Re-submitting the same ack_id is a no-op.
        side: "buy" or "sell".
        order_type: "limit" or "market".
        price: limit price (ignored for market orders).
        qty: order quantity (positive).
        timestamp: monotonically increasing submission time (for FIFO).
    """

    ack_id: str
    side: str           # "buy" | "sell"
    order_type: str     # "limit" | "market"
    price: float = 0.0
    qty: float = 0.0
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if self.order_type not in ("limit", "market"):
            raise ValueError(
                f"order_type must be 'limit' or 'market', got {self.order_type!r}"
            )
        if self.qty <= 0:
            raise ValueError(f"qty must be positive, got {self.qty}")
        if self.order_type == "limit" and self.price <= 0:
            raise ValueError(f"limit price must be positive, got {self.price}")


@dataclass(frozen=True)
class Fill:
    """One executed fill produced by the matching engine."""

    maker_ack_id: str       # resting order that provided liquidity
    taker_ack_id: str       # incoming order that took liquidity
    price: float
    qty: float
    timestamp: float
    taker_side: str         # "buy" | "sell" — side of the taker


class MatchingEngine:
    """Price-time priority CLOB matching engine with duplicate-ack guard.

    The book maintains two sides:

    - **Bids** (buy orders): stored as an ``OrderedDict`` keyed by price,
      sorted descending (best bid = highest price).
    - **Asks** (sell orders): same structure, sorted ascending
      (best ask = lowest price).

    Each price level holds a FIFO ``deque[Order]`` of resting orders.
    When a new order arrives:

    1. The ``ack_id`` is checked — if already seen, the order is ignored
       (duplicate-ack guard).
    2. For a market order: cross against the best levels until filled.
    3. For a limit order: cross against eligible levels, then rest any
       unfilled remainder on the book.
    """

    def __init__(self) -> None:
        # bids: price -> deque[Order], accessed sorted-descending
        self._bids: "OrderedDict[float, Deque[Order]]" = OrderedDict()
        # asks: price -> deque[Order], accessed sorted-ascending
        self._asks: "OrderedDict[float, Deque[Order]]" = OrderedDict()
        # ack_id -> (side, price, order) for O(1) cancel
        self._resting: Dict[str, Tuple[str, float, Order]] = {}
        # seen ack_ids for idempotency
        self._seen_acks: set[str] = set()
        # fills log (in order)
        self._fills: List[Fill] = []

    # ------------------------------------------------------------------
    # Public read-only views
    # ------------------------------------------------------------------
    @property
    def fills(self) -> List[Fill]:
        """All fills produced, in chronological order."""
        return list(self._fills)

    @property
    def seen_acks(self) -> set[str]:
        return set(self._seen_acks)

    def best_bid(self) -> Optional[float]:
        """Best (highest) bid price, or None if the bid side is empty."""
        self._sort_bids()
        for price in self._bids:
            if self._bids[price]:
                return price
        return None

    def best_ask(self) -> Optional[float]:
        """Best (lowest) ask price, or None if the ask side is empty."""
        self._sort_asks()
        for price in self._asks:
            if self._asks[price]:
                return price
        return None

    def mid_price(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0

    def bid_depth(self) -> float:
        """Total resting buy quantity."""
        return sum(o.qty for q in self._bids.values() for o in q)

    def ask_depth(self) -> float:
        """Total resting sell quantity."""
        return sum(o.qty for q in self._asks.values() for o in q)

    # ------------------------------------------------------------------
    # Core: process one order
    # ------------------------------------------------------------------
    def process(self, order: Order) -> List[Fill]:
        """Submit one order; return the fills it produced (may be empty).

        If ``order.ack_id`` was already seen, returns ``[]`` immediately
        (idempotent no-op — duplicate-ack guard).
        """
        if order.ack_id in self._seen_acks:
            return []
        self._seen_acks.add(order.ack_id)

        fills: List[Fill] = []
        remaining = order.qty

        if order.side == "buy":
            fills, remaining = self._cross_buy(order, remaining)
        else:
            fills, remaining = self._cross_sell(order, remaining)

        # Rest unfilled limit remainder on the book
        if remaining > 0 and order.order_type == "limit":
            self._rest(order, remaining)

        self._fills.extend(fills)
        return fills

    def cancel(self, ack_id: str) -> bool:
        """Cancel a resting order by ack_id. Returns True if found and removed."""
        info = self._resting.pop(ack_id, None)
        if info is None:
            return False
        side, price, _ = info
        book = self._bids if side == "buy" else self._asks
        level = book.get(price)
        if level is None:
            return False
        # Remove the specific order from the FIFO deque
        for i, o in enumerate(level):
            if o.ack_id == ack_id:
                del level[i]
                break
        if not level:
            del book[price]
        return True

    # ------------------------------------------------------------------
    # Internals: crossing
    # ------------------------------------------------------------------
    def _cross_buy(
        self, order: Order, remaining: float
    ) -> Tuple[List[Fill], float]:
        """Buy order crosses against asks (ascending price)."""
        fills: List[Fill] = []
        self._sort_asks()
        to_remove: List[float] = []
        for price in list(self._asks.keys()):
            if remaining <= 0:
                break
            # Market: cross any level. Limit: only cross at or below order price.
            if order.order_type == "limit" and price > order.price:
                break
            level = self._asks[price]
            while level and remaining > 0:
                maker = level[0]
                fill_qty = min(remaining, maker.qty)
                fill = Fill(
                    maker_ack_id=maker.ack_id,
                    taker_ack_id=order.ack_id,
                    price=price,
                    qty=fill_qty,
                    timestamp=order.timestamp,
                    taker_side="buy",
                )
                fills.append(fill)
                remaining -= fill_qty
                # update or remove maker
                if fill_qty >= maker.qty:
                    level.popleft()
                    self._resting.pop(maker.ack_id, None)
                else:
                    # Replace with reduced qty (immutable Order)
                    new_maker = Order(
                        ack_id=maker.ack_id,
                        side=maker.side,
                        order_type=maker.order_type,
                        price=maker.price,
                        qty=maker.qty - fill_qty,
                        timestamp=maker.timestamp,
                    )
                    level[0] = new_maker
                    self._resting[maker.ack_id] = ("sell", price, new_maker)
            if not level:
                to_remove.append(price)
        for p in to_remove:
            self._asks.pop(p, None)
        return fills, remaining

    def _cross_sell(
        self, order: Order, remaining: float
    ) -> Tuple[List[Fill], float]:
        """Sell order crosses against bids (descending price)."""
        fills: List[Fill] = []
        self._sort_bids()
        to_remove: List[float] = []
        for price in list(self._bids.keys()):
            if remaining <= 0:
                break
            # Market: cross any level. Limit: only cross at or above order price.
            if order.order_type == "limit" and price < order.price:
                break
            level = self._bids[price]
            while level and remaining > 0:
                maker = level[0]
                fill_qty = min(remaining, maker.qty)
                fill = Fill(
                    maker_ack_id=maker.ack_id,
                    taker_ack_id=order.ack_id,
                    price=price,
                    qty=fill_qty,
                    timestamp=order.timestamp,
                    taker_side="sell",
                )
                fills.append(fill)
                remaining -= fill_qty
                if fill_qty >= maker.qty:
                    level.popleft()
                    self._resting.pop(maker.ack_id, None)
                else:
                    new_maker = Order(
                        ack_id=maker.ack_id,
                        side=maker.side,
                        order_type=maker.order_type,
                        price=maker.price,
                        qty=maker.qty - fill_qty,
                        timestamp=maker.timestamp,
                    )
                    level[0] = new_maker
                    self._resting[maker.ack_id] = ("buy", price, new_maker)
            if not level:
                to_remove.append(price)
        for p in to_remove:
            self._bids.pop(p, None)
        return fills, remaining

    def _rest(self, order: Order, qty: float) -> None:
        """Place the unfilled remainder as a resting limit order."""
        resting = Order(
            ack_id=order.ack_id,
            side=order.side,
            order_type="limit",
            price=order.price,
            qty=qty,
            timestamp=order.timestamp,
        )
        book = self._bids if order.side == "buy" else self._asks
        level = book.get(order.price)
        if level is None:
            level = deque()
            book[order.price] = level
        level.append(resting)
        self._resting[order.ack_id] = (order.side, order.price, resting)

    # ------------------------------------------------------------------
    # Book sorting (re-sort by priority before crossing)
    # ------------------------------------------------------------------
    def _sort_bids(self) -> None:
        """Re-sort bids descending by price (best bid first)."""
        if not self._bids:
            return
        items = sorted(self._bids.items(), key=lambda kv: -kv[0])
        self._bids = OrderedDict(items)

    def _sort_asks(self) -> None:
        """Re-sort asks ascending by price (best ask first)."""
        if not self._asks:
            return
        items = sorted(self._asks.items(), key=lambda kv: kv[0])
        self._asks = OrderedDict(items)


__all__ = ["Order", "Fill", "MatchingEngine"]
