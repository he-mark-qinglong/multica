"""Portfolio exposure limiter (I13).

Hard pre-trade risk limits across the whole book, checked BEFORE a new
position is opened:

  * total gross notional cap (sum of |qty*price| across symbols),
  * per-symbol notional cap,
  * per-direction notional cap (sum of longs, sum of |shorts|),
  * leverage cap (total gross notional / equity).

Core logic is the pure function :func:`check_exposure`; the
:class:`ExposureLimiter` class is a thin stateful wrapper that tracks the
current book and logs every rejection with its reason — the audit trail
is the point of the limiter (a silent clip is not enforceable risk
management).

References:
  - Jane Street, "Probability & Markets Guide" (capital preservation as
    the binding constraint).
  - López de Prado (2018), "Advances in Financial Machine Learning",
    Ch. 10 (position limits under concurrent strategies).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Tuple


@dataclass(frozen=True)
class ExposureLimits:
    """Hard caps. ``None`` disables a cap."""

    max_total_notional: float | None = None
    max_symbol_notional: float | None = None
    max_direction_notional: float | None = None   # per side (long OR short)
    max_leverage: float | None = None             # total notional / equity


@dataclass(frozen=True)
class Position:
    """Signed position in one symbol. ``qty`` positive = long."""

    symbol: str
    qty: float
    price: float

    @property
    def notional(self) -> float:
        return abs(self.qty) * self.price

    @property
    def signed_notional(self) -> float:
        return self.qty * self.price


@dataclass(frozen=True)
class Rejection:
    """Audit record of a rejected position change."""

    symbol: str
    qty: float
    price: float
    reason: str


def check_exposure(
    positions: Mapping[str, Position],
    new: Position,
    limits: ExposureLimits,
    equity: float,
) -> Tuple[bool, str]:
    """Would replacing ``positions[new.symbol]`` with ``new`` stay within
    limits? Pure function — does not mutate anything.

    Returns ``(allowed, reason)``; ``reason`` is "" when allowed.
    """
    book = dict(positions)
    if new.qty == 0.0:
        book.pop(new.symbol, None)
    else:
        book[new.symbol] = new

    total = sum(p.notional for p in book.values())
    longs = sum(p.signed_notional for p in book.values() if p.qty > 0)
    shorts = -sum(p.signed_notional for p in book.values() if p.qty < 0)

    if limits.max_symbol_notional is not None and new.qty != 0.0:
        if new.notional > limits.max_symbol_notional:
            return False, (
                f"symbol cap: {new.symbol} notional {new.notional:.2f} "
                f"> {limits.max_symbol_notional:.2f}"
            )
    if limits.max_total_notional is not None and total > limits.max_total_notional:
        return False, (
            f"total cap: book notional {total:.2f} "
            f"> {limits.max_total_notional:.2f}"
        )
    if limits.max_direction_notional is not None:
        if longs > limits.max_direction_notional:
            return False, (
                f"long-side cap: {longs:.2f} "
                f"> {limits.max_direction_notional:.2f}"
            )
        if shorts > limits.max_direction_notional:
            return False, (
                f"short-side cap: {shorts:.2f} "
                f"> {limits.max_direction_notional:.2f}"
            )
    if limits.max_leverage is not None:
        if equity <= 0:
            return False, f"leverage undefined: equity {equity:.2f} <= 0"
        lev = total / equity
        if lev > limits.max_leverage:
            return False, (
                f"leverage cap: {lev:.3f}x > {limits.max_leverage:.3f}x"
            )
    return True, ""


class ExposureLimiter:
    """Stateful book tracker enforcing :class:`ExposureLimits`.

    Usage::

        lim = ExposureLimiter(ExposureLimits(max_leverage=2.0))
        ok, reason = lim.check(Position("BTC", 0.1, 60000), equity=10000)
        if ok:
            lim.apply(Position("BTC", 0.1, 60000))
    """

    def __init__(self, limits: ExposureLimits):
        self.limits = limits
        self._positions: Dict[str, Position] = {}
        self._rejections: List[Rejection] = []

    @property
    def positions(self) -> Dict[str, Position]:
        return dict(self._positions)

    @property
    def rejections(self) -> List[Rejection]:
        return list(self._rejections)

    def check(self, new: Position, equity: float) -> Tuple[bool, str]:
        """Check and log. Rejections are appended to ``self.rejections``."""
        allowed, reason = check_exposure(
            self._positions, new, self.limits, equity
        )
        if not allowed:
            self._rejections.append(
                Rejection(new.symbol, new.qty, new.price, reason)
            )
        return allowed, reason

    def apply(self, new: Position) -> None:
        """Update the book. Call only after ``check`` returned True."""
        if new.qty == 0.0:
            self._positions.pop(new.symbol, None)
        else:
            self._positions[new.symbol] = new

    def gross_notional(self) -> float:
        return sum(p.notional for p in self._positions.values())

    def net_notional(self) -> float:
        return sum(p.signed_notional for p in self._positions.values())
