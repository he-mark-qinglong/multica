"""Live PnL attribution (H18).

Decomposes realized trading PnL from a fill stream into four buckets:

  * **price** — mark-to-market move between entry and exit, matched
    FIFO per (strategy, symbol). This is the "was the thesis right" part.
  * **fee** — commissions paid, as negative PnL on the fill's own day.
  * **funding** — perpetual funding payments attached to a fill
    (positive payment = cost = negative PnL).
  * **slippage** — execution price vs the fill's reference (arrival /
    decision mid): ``signed_qty * (reference - price)``. A buy above the
    reference or a sell below it records negative slippage PnL. This is
    the "was the execution good" part, kept separate so strategy alpha is
    never silently blended with execution quality.

Buckets are attributed to the UTC day of the fill that realizes them
(price PnL lands on the closing fill's day; fees/funding/slippage on the
fill's own day), then aggregated per (strategy, symbol, day). Everything
is a pure function of the fill sequence — same fills in, same table out,
in backtest and live.

References:
- Grinold & Kahn, "Active Portfolio Management", ch. 17 — performance
  attribution separating selection (price) from implementation (cost).
- Almgren & Chriss (2001), "Optimal Execution of Portfolio
  Transactions", J. Risk — implementation shortfall vs arrival price as
  the execution-quality measure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

SIDES = ("buy", "sell")
BUCKET_KEYS = ("strategy", "symbol", "day")


@dataclass(frozen=True)
class Fill:
    """One executed fill.

    Attributes:
        ts: execution time, epoch seconds.
        strategy: strategy that owns the fill.
        symbol: instrument.
        side: "buy" | "sell".
        qty: executed quantity (> 0, absolute).
        price: execution price.
        fee: commission in quote currency (positive = cost).
        funding: funding payment realized with this fill (positive = paid).
        reference_price: arrival/decision mid for slippage; None = unknown
            (slippage bucket left at 0 for this fill).
    """

    ts: float
    strategy: str
    symbol: str
    side: str
    qty: float
    price: float
    fee: float = 0.0
    funding: float = 0.0
    reference_price: Optional[float] = None

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}, got {self.side!r}")
        if self.qty <= 0:
            raise ValueError(f"qty must be positive, got {self.qty}")

    @property
    def signed_qty(self) -> float:
        return self.qty if self.side == "buy" else -self.qty


@dataclass(frozen=True)
class AttributionRow:
    """PnL decomposition for one (strategy, symbol, day) cell.

    All buckets are signed PnL in quote currency: positive = gain.
    ``fee_pnl``, ``funding_pnl``, ``slippage_pnl`` are <= 0 for costs.
    Aggregated rows carry "" in the dimensions that were rolled up.
    """

    strategy: str
    symbol: str
    day: str
    price_pnl: float = 0.0
    fee_pnl: float = 0.0
    funding_pnl: float = 0.0
    slippage_pnl: float = 0.0
    closed_qty: float = 0.0

    @property
    def total_pnl(self) -> float:
        return (
            self.price_pnl + self.fee_pnl
            + self.funding_pnl + self.slippage_pnl
        )


def _day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


@dataclass(frozen=True)
class _Lot:
    """Open FIFO lot; qty is signed (long > 0, short < 0)."""

    qty: float
    price: float


def attribute_fills(fills: Sequence[Fill]) -> Tuple[AttributionRow, ...]:
    """Decompose a fill stream into per-(strategy, symbol, day) rows. Pure.

    Fills are processed in the order given (callers sort by ts). Price
    PnL is matched FIFO within each (strategy, symbol); a fill that
    over-closes flips the position and the remainder opens a new lot.
    """
    lots: Dict[Tuple[str, str], List[_Lot]] = {}
    # cell -> [price, fee, funding, slippage, closed_qty]
    cells: Dict[Tuple[str, str, str], List[float]] = {}

    def cell(key: Tuple[str, str, str]) -> List[float]:
        return cells.setdefault(key, [0.0, 0.0, 0.0, 0.0, 0.0])

    for f in fills:
        book = (f.strategy, f.symbol)
        day_key = (f.strategy, f.symbol, _day_of(f.ts))
        acc = cell(day_key)

        acc[1] -= f.fee
        acc[2] -= f.funding
        if f.reference_price is not None:
            acc[3] += f.signed_qty * (f.reference_price - f.price)

        stack = lots.setdefault(book, [])
        remaining = f.signed_qty
        while remaining != 0.0 and stack and (stack[0].qty > 0) != (remaining > 0):
            lot = stack[0]
            matched = min(abs(lot.qty), abs(remaining))
            # sign(lot.qty): +1 long, -1 short — works for both directions.
            direction = 1.0 if lot.qty > 0 else -1.0
            acc[0] += (f.price - lot.price) * matched * direction
            acc[4] += matched
            new_lot_qty = lot.qty - matched * direction
            # remaining moves toward zero by `matched` regardless of sign.
            remaining -= matched if remaining > 0 else -matched
            stack.pop(0)
            if new_lot_qty != 0.0:
                stack.insert(0, _Lot(qty=new_lot_qty, price=lot.price))
        if remaining != 0.0:
            stack.insert(0, _Lot(qty=remaining, price=f.price))

    rows = [
        AttributionRow(
            strategy=key[0], symbol=key[1], day=key[2],
            price_pnl=v[0], fee_pnl=v[1], funding_pnl=v[2],
            slippage_pnl=v[3], closed_qty=v[4],
        )
        for key, v in cells.items()
    ]
    rows.sort(key=lambda r: (r.strategy, r.symbol, r.day))
    return tuple(rows)


def aggregate(
    rows: Sequence[AttributionRow],
    by: Sequence[str] = ("strategy",),
) -> Tuple[AttributionRow, ...]:
    """Roll rows up by a subset of ("strategy", "symbol", "day"). Pure.

    Dimensions not in ``by`` are rolled up and set to "" in the output.
    """
    keys = tuple(by)
    invalid = set(keys) - set(BUCKET_KEYS)
    if invalid:
        raise ValueError(f"invalid aggregation keys: {sorted(invalid)}")
    if not keys:
        raise ValueError("by must contain at least one key")

    merged: Dict[Tuple[str, ...], List[float]] = {}
    for r in rows:
        full = {"strategy": r.strategy, "symbol": r.symbol, "day": r.day}
        key = tuple(full[k] for k in keys)
        acc = merged.setdefault(key, [0.0] * 5)
        acc[0] += r.price_pnl
        acc[1] += r.fee_pnl
        acc[2] += r.funding_pnl
        acc[3] += r.slippage_pnl
        acc[4] += r.closed_qty

    out = []
    for key, v in merged.items():
        dims = dict(zip(keys, key))
        out.append(AttributionRow(
            strategy=dims.get("strategy", ""),
            symbol=dims.get("symbol", ""),
            day=dims.get("day", ""),
            price_pnl=v[0], fee_pnl=v[1], funding_pnl=v[2],
            slippage_pnl=v[3], closed_qty=v[4],
        ))
    out.sort(key=lambda r: (r.strategy, r.symbol, r.day))
    return tuple(out)
