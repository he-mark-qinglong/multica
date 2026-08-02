"""Strategy-level independent account views (I12, partial I11).

Given a single fill stream tagged with ``strategy_id``, reconstruct a
separate ledger per strategy: equity curve, positions, realized /
unrealized PnL, and fees. Two modes:

  ``isolated``  each strategy runs its own account starting from
                ``initial_capital`` — full capital segregation view.

  ``shared``    one capital pool shared by all strategies; per-strategy
                equity still uses the same formula but initial capital is
                split pro-rata by ``capital_weights`` (equal by default),
                and the returned mapping includes a ``"__pool__"`` entry
                with the combined view. This is the capital-sharing
                accounting half of I11 (allocation logic lives in
                ``_shared/market_making/dynamic_erc.py``).

Accounting conventions
----------------------
  Average-cost realized PnL. Positions are signed quantities
  (positive = long). Cash debited ``qty * price + fee`` on buys,
  credited on sells. Equity is marked to the last seen fill price per
  symbol (or an explicitly provided mark price), so unrealized PnL is
  always computable without an external price feed.

Pure functions, no I/O. Deterministic: fills are sorted by ``(ts, seq)``.

References:
  - Grinold & Kahn (2000), "Active Portfolio Management", Ch. 4
    (portfolio accounting and residual return attribution).
  - López de Prado (2018), "Advances in Financial Machine Learning",
    Ch. 10 (bet sizing across concurrent strategies sharing a book).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

POOL_ID = "__pool__"


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fill:
    """One execution. ``qty`` is signed: positive = buy, negative = sell."""

    ts: pd.Timestamp
    strategy_id: str
    symbol: str
    qty: float
    price: float
    fee: float = 0.0

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"fill price must be positive, got {self.price}")
        if self.fee < 0:
            raise ValueError(f"fee must be non-negative, got {self.fee}")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AccountView:
    """Per-strategy (or pool) reconstructed account."""

    account_id: str
    initial_capital: float
    final_equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_fees: float
    n_fills: int
    positions: Dict[str, float]          # symbol -> signed qty (open)
    equity_curve: pd.Series              # indexed by fill ts, mark-to-market
    total_return: float                  # final_equity / initial_capital - 1


@dataclass
class _Ledger:
    """Mutable accumulation state (internal; not exported). Public API
    objects stay frozen; only this private builder mutates."""

    cash: float
    positions: Dict[str, float]
    cost_basis: Dict[str, float]         # symbol -> avg cost per unit of open pos
    realized: float
    fees: float


def _new_ledger(initial_capital: float) -> _Ledger:
    return _Ledger(cash=initial_capital, positions={}, cost_basis={},
                   realized=0.0, fees=0.0)


def _apply_fill(led: _Ledger, fill: Fill) -> None:
    """Average-cost accounting for one fill (mutates ledger)."""
    sym = fill.symbol
    prev_qty = led.positions.get(sym, 0.0)
    qty, px, fee = fill.qty, fill.price, fill.fee

    led.cash -= qty * px + fee
    led.fees += fee

    # Realized PnL on the closing portion (position shrinks or flips).
    if prev_qty != 0.0 and (prev_qty > 0) != (qty > 0):
        closing = min(abs(qty), abs(prev_qty))
        direction = 1.0 if prev_qty > 0 else -1.0
        led.realized += closing * (px - led.cost_basis[sym]) * direction

    new_qty = prev_qty + qty
    if prev_qty == 0.0 or (prev_qty > 0) != (new_qty > 0):
        # Fresh position or flip: cost basis = this fill's price.
        led.cost_basis[sym] = px
    elif (prev_qty > 0) == (qty > 0):
        # Adding in the same direction: weighted average cost.
        led.cost_basis[sym] = (
            abs(prev_qty) * led.cost_basis[sym] + abs(qty) * px
        ) / abs(new_qty)
    # Pure reduction keeps the old cost basis.

    if new_qty == 0.0:
        led.positions.pop(sym, None)
        led.cost_basis.pop(sym, None)
    else:
        led.positions[sym] = new_qty


def _mark_equity(led: _Ledger, marks: Mapping[str, float]) -> float:
    eq = led.cash
    for sym, q in led.positions.items():
        eq += q * marks.get(sym, led.cost_basis.get(sym, 0.0))
    return eq


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_account_views(
    fills: Sequence[Fill],
    initial_capital: float,
    mode: str = "isolated",
    capital_weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, AccountView]:
    """Reconstruct per-strategy account views from a tagged fill stream.

    Parameters
    ----------
    fills : sequence of Fill
        Any order; sorted internally by timestamp (stable).
    initial_capital : float
        Per-account capital in ``isolated`` mode; total pool capital in
        ``shared`` mode.
    mode : {"isolated", "shared"}
    capital_weights : mapping strategy_id -> weight, optional
        Only used in ``shared`` mode to split the pool's initial capital.
        Defaults to equal weights.

    Returns
    -------
    dict account_id -> AccountView. ``shared`` mode adds a ``"__pool__"``
    entry whose equity curve is the sum of the strategy curves.
    """
    if mode not in ("isolated", "shared"):
        raise ValueError(f"mode must be 'isolated' or 'shared', got {mode!r}")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    ordered = sorted(enumerate(fills), key=lambda p: (p[1].ts, p[0]))
    strategy_ids = sorted({f.strategy_id for f in fills})
    if not strategy_ids:
        return {}

    if mode == "shared":
        w = _resolve_weights(strategy_ids, capital_weights)
        start = {sid: initial_capital * w[sid] for sid in strategy_ids}
    else:
        start = {sid: initial_capital for sid in strategy_ids}

    ledgers = {sid: _new_ledger(start[sid]) for sid in strategy_ids}
    curves: Dict[str, List[Tuple[pd.Timestamp, float]]] = {
        sid: [] for sid in strategy_ids
    }
    last_price: Dict[str, float] = {}

    for _, fill in ordered:
        last_price[fill.symbol] = fill.price
        led = ledgers[fill.strategy_id]
        _apply_fill(led, fill)
        curves[fill.strategy_id].append(
            (fill.ts, _mark_equity(led, last_price))
        )

    views = {
        sid: _finalize(sid, ledgers[sid], curves[sid], start[sid], last_price)
        for sid in strategy_ids
    }

    if mode == "shared":
        views[POOL_ID] = _pool_view(views, initial_capital)
    return views


def _resolve_weights(
    strategy_ids: Sequence[str],
    capital_weights: Optional[Mapping[str, float]],
) -> Dict[str, float]:
    if capital_weights is None:
        n = len(strategy_ids)
        return {sid: 1.0 / n for sid in strategy_ids}
    missing = [sid for sid in strategy_ids if sid not in capital_weights]
    if missing:
        raise ValueError(f"capital_weights missing strategies: {missing}")
    total = sum(capital_weights[sid] for sid in strategy_ids)
    if total <= 0:
        raise ValueError("capital_weights must sum to a positive value")
    return {sid: capital_weights[sid] / total for sid in strategy_ids}


def _finalize(
    sid: str,
    led: _Ledger,
    curve: List[Tuple[pd.Timestamp, float]],
    start_capital: float,
    last_price: Mapping[str, float],
) -> AccountView:
    final_eq = _mark_equity(led, last_price)
    unrealized = sum(
        q * (last_price.get(sym, led.cost_basis.get(sym, 0.0))
             - led.cost_basis.get(sym, 0.0))
        for sym, q in led.positions.items()
    )
    series = pd.Series(
        [eq for _, eq in curve],
        index=pd.DatetimeIndex([ts for ts, _ in curve]),
        name=sid,
    )
    return AccountView(
        account_id=sid,
        initial_capital=start_capital,
        final_equity=final_eq,
        realized_pnl=led.realized,
        unrealized_pnl=unrealized,
        total_fees=led.fees,
        n_fills=len(curve),
        positions=dict(led.positions),
        equity_curve=series,
        total_return=final_eq / start_capital - 1.0,
    )


def _pool_view(
    views: Mapping[str, AccountView], initial_capital: float
) -> AccountView:
    combined = pd.concat([v.equity_curve for v in views.values()], axis=1)
    pool_curve = combined.ffill().sum(axis=1, min_count=1).dropna()
    pool_curve.name = POOL_ID
    positions: Dict[str, float] = {}
    for v in views.values():
        for sym, q in v.positions.items():
            positions[sym] = positions.get(sym, 0.0) + q
    positions = {s: q for s, q in positions.items() if q != 0.0}
    final_eq = sum(v.final_equity for v in views.values())
    return AccountView(
        account_id=POOL_ID,
        initial_capital=initial_capital,
        final_equity=final_eq,
        realized_pnl=sum(v.realized_pnl for v in views.values()),
        unrealized_pnl=sum(v.unrealized_pnl for v in views.values()),
        total_fees=sum(v.total_fees for v in views.values()),
        n_fills=sum(v.n_fills for v in views.values()),
        positions=positions,
        equity_curve=pool_curve,
        total_return=final_eq / initial_capital - 1.0,
    )
