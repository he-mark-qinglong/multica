"""L2 order-book reconstruction engine (B4).

Rebuilds a limit order book from a full snapshot plus a stream of
incremental diffs, following the Binance futures depth-update
semantics: each diff carries absolute ``(price, quantity)`` updates
per level, where ``quantity == 0`` removes the level.  The book itself
is an immutable value: :meth:`BookState.apply_diff` returns a *new*
:class:`BookState` and never mutates in place, so replay engines can
keep a history of states for free and replay is deterministic.

Layout conventions:

* ``bids`` is a tuple of ``(price, qty)`` sorted by price **descending**
  (best bid first);
* ``asks`` is sorted by price **ascending** (best ask first);
* timestamps are integer nanoseconds (``ts_ns``), matching
  ``_shared/partial_fill.py``.

References
----------
- Binance Futures API docs, "Diff. Depth Stream" — snapshot + absolute
  quantity diff semantics (qty=0 removes the level).
- Cont, Stoikov & Talreja (2010), "A Stochastic Model for Order Book
  Dynamics" — order book as a queueing system of price levels.
- Bouchaud, Mézard & Potters (2002), "Statistical properties of stock
  order books" — depth profile / weighted-depth statistics.

Pure functions + frozen dataclasses only; no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "BID",
    "ASK",
    "SIDES",
    "BookDiff",
    "BookState",
    "Level",
    "snapshot",
]

BID = "BID"
ASK = "ASK"
SIDES = frozenset({BID, ASK})

Level = Tuple[float, float]  # (price, qty)


def _validate_levels(levels: Sequence[Level], side: str) -> None:
    for price, qty in levels:
        if price <= 0.0:
            raise ValueError(f"{side}: level price must be positive, got {price!r}")
        if qty < 0.0:
            raise ValueError(f"{side}: level qty must be non-negative, got {qty!r}")


def _sort_side(levels: Sequence[Level], side: str) -> Tuple[Level, ...]:
    """Sort levels into canonical book order and drop zero-qty levels."""
    cleaned = [(float(p), float(q)) for p, q in levels if q > 0.0]
    reverse = side == BID
    cleaned.sort(key=lambda lv: lv[0], reverse=reverse)
    return tuple(cleaned)


@dataclass(frozen=True)
class BookState:
    """Immutable order-book state.

    ``bids``    ``(price, qty)`` levels, best (highest) bid first.
    ``asks``    ``(price, qty)`` levels, best (lowest) ask first.
    ``ts_ns``   timestamp of the last update applied, in nanoseconds.
    """

    ts_ns: int
    bids: Tuple[Level, ...]
    asks: Tuple[Level, ...]

    def __post_init__(self) -> None:
        _validate_levels(self.bids, BID)
        _validate_levels(self.asks, ASK)

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    @property
    def best_bid(self) -> Optional[Level]:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[Level]:
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        """Mid-price; ``None`` when either side is empty."""
        bb, ba = self.best_bid, self.best_ask
        if bb is None or ba is None:
            return None
        return (bb[0] + ba[0]) / 2.0

    @property
    def spread(self) -> Optional[float]:
        bb, ba = self.best_bid, self.best_ask
        if bb is None or ba is None:
            return None
        return ba[0] - bb[0]

    def top(self, n: int, side: str) -> Tuple[Level, ...]:
        """Top-``n`` levels of one side in book order."""
        if side not in SIDES:
            raise ValueError(f"side must be BID/ASK, got {side!r}")
        if n < 0:
            raise ValueError("n must be non-negative")
        return (self.bids if side == BID else self.asks)[:n]

    def depth_qty(self, side: str, n_levels: Optional[int] = None) -> float:
        """Total resting quantity over the first ``n_levels`` (default all)."""
        levels = self.bids if side == BID else self.asks
        if n_levels is not None:
            levels = levels[:n_levels]
        return sum(q for _, q in levels)

    def weighted_depth_price(self, side: str, qty: float) -> Optional[float]:
        """Volume-weighted average price to fill ``qty`` from ``side``.

        Walks the book from the top, consuming level by level; returns
        the VWAP of the levels consumed, or ``None`` when the visible
        depth is insufficient.  This is the instantaneous price-impact
        measure of Bouchaud et al. (2002).
        """
        if qty <= 0.0:
            raise ValueError(f"qty must be positive, got {qty!r}")
        levels = self.bids if side == BID else self.asks
        remaining = qty
        cost = 0.0
        for price, level_qty in levels:
            take = min(remaining, level_qty)
            cost += take * price
            remaining -= take
            if remaining <= 0.0:
                return cost / qty
        return None

    def levels_through(self, side: str, limit_price: float) -> Tuple[Level, ...]:
        """Levels of ``side`` that a marketable order at ``limit_price``
        would trade against, in walk order.

        A BUY order crossing the book trades against ASK levels priced
        ``<= limit_price``; a SELL order against BID levels priced
        ``>= limit_price``.
        """
        if side == ASK:
            return tuple(lv for lv in self.asks if lv[0] <= limit_price)
        if side == BID:
            return tuple(lv for lv in self.bids if lv[0] >= limit_price)
        raise ValueError(f"side must be BID/ASK, got {side!r}")

    # ------------------------------------------------------------------
    # immutable update
    # ------------------------------------------------------------------
    def apply_diff(self, diff: "BookDiff") -> "BookState":
        """Apply an incremental diff, returning a new :class:`BookState`.

        Each ``(price, qty)`` update sets the absolute quantity at that
        price: ``qty > 0`` inserts/replaces the level, ``qty == 0``
        removes it.  Unknown prices with ``qty == 0`` are ignored, per
        Binance diff-depth semantics.
        """
        bids = _apply_side(self.bids, diff.bids, BID)
        asks = _apply_side(self.asks, diff.asks, ASK)
        return BookState(ts_ns=diff.ts_ns, bids=bids, asks=asks)


def _apply_side(
    levels: Tuple[Level, ...],
    updates: Sequence[Level],
    side: str,
) -> Tuple[Level, ...]:
    _validate_levels(updates, side)
    book: Dict[float, float] = dict(levels)
    for price, qty in updates:
        if qty > 0.0:
            book[float(price)] = float(qty)
        else:
            book.pop(float(price), None)
    return _sort_side(tuple(book.items()), side)


@dataclass(frozen=True)
class BookDiff:
    """One incremental book update (absolute per-level quantities).

    ``bids`` / ``asks`` are ``(price, qty)`` updates; ``qty == 0``
    removes the level.  ``ts_ns`` must be monotonically non-decreasing
    across a diff stream (enforced by the replay engine).
    """

    ts_ns: int
    bids: Tuple[Level, ...] = ()
    asks: Tuple[Level, ...] = ()

    def __post_init__(self) -> None:
        _validate_levels(self.bids, BID)
        _validate_levels(self.asks, ASK)


def snapshot(
    ts_ns: int,
    bids: Sequence[Level],
    asks: Sequence[Level],
) -> BookState:
    """Build a :class:`BookState` from a full depth snapshot.

    Levels may be given in any order; they are sorted into canonical
    book order and zero-quantity entries are dropped.
    """
    _validate_levels(bids, BID)
    _validate_levels(asks, ASK)
    return BookState(
        ts_ns=ts_ns,
        bids=_sort_side(bids, BID),
        asks=_sort_side(asks, ASK),
    )
