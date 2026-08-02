"""Per-bar liquidation simulator for leveraged positions (B13).

Given a leveraged position, a wallet (margin) balance and a maintenance
margin rate, check each bar's mark-price range against the position's
liquidation price and, on a touch, force-close under one of two
liquidation rules:

* ``mode="partial"`` — rank-down step: close
  ``partial_close_fraction`` of the open position per trigger (the
  surviving fraction inherits the remaining equity, so its margin
  ratio roughly doubles, less the penalty fee); if equity cannot
  cover the liquidation fee, escalate to a full close;
* ``mode="full"``    — close the entire position immediately.

Both modes charge ``penalty_fee_rate`` on the liquidated notional (the
insurance-fund fee).  Any residual deficit after a full close
(``fee > equity``) is recorded on the event as
:attr:`LiquidationEvent.deficit` — the amount the insurance fund (and,
in a backtest, the strategy's hidden assumption of bounded losses)
must absorb.

Liquidation-price derivation (linear, USDT-M style, isolated margin):

    equity(p) = wallet_balance + qty * (p - entry)
    liquidate when equity(p) = |qty| * p * mmr

    long  (qty > 0):  p* = (qty*entry - B) / (qty * (1 - mmr))
    short (qty < 0):  p* = (qty*entry - B) / (qty * (1 + mmr))

With the default wallet balance ``B = |qty| * entry / leverage``
(margin posted at entry, isolated) these reduce to the familiar
``entry * (1 - 1/L) / (1 - mmr)`` / ``entry * (1 + 1/L) / (1 + mmr)``.

Execution price: liquidation fires as a stop-market at ``p*``; if the
bar *opens* already beyond ``p*`` (gap through the liquidation level)
the fill happens at the bar open — worse, and deliberately so
(conservative).

References
----------
- Binance Futures, "Liquidation Protocols" / USDT-M margin &
  maintenance-margin specification (exchange mechanics).
- Gorton & Metrick (2012), "Securitized banking and the run on repo"
  — margin spirals under forced deleveraging.
- Brunnermeier & Pedersen (2009), "Market Liquidity and Funding
  Liquidity" — margin-constrained forced selling.

Pure functions + frozen dataclasses for the maths; the
:class:`LiquidationEngine` is the only stateful object and does no I/O.
The :class:`Bar` type is shared with ``_shared/partial_fill.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from _shared.partial_fill import Bar

__all__ = [
    "DEFAULT_LIQUIDATION_POLICY",
    "LiquidationEngine",
    "LiquidationEvent",
    "LiquidationPolicy",
    "Position",
    "is_liquidatable",
    "liquidation_price",
    "margin_ratio",
    "simulate_liquidations",
]

_MODES = frozenset({"partial", "full"})


@dataclass(frozen=True)
class Position:
    """A leveraged position.  ``qty`` is signed: + long, − short."""

    symbol: str
    qty: float
    entry_price: float
    leverage: float

    def __post_init__(self) -> None:
        if self.qty == 0.0:
            raise ValueError("Position: qty must be non-zero")
        if self.entry_price <= 0.0:
            raise ValueError("Position: entry_price must be positive")
        if self.leverage < 1.0:
            raise ValueError(
                f"Position: leverage must be >= 1, got {self.leverage!r}"
            )

    @property
    def side(self) -> str:
        return "LONG" if self.qty > 0 else "SHORT"


@dataclass(frozen=True)
class LiquidationPolicy:
    """Liquidation rule configuration.

    ``maintenance_margin_rate``  mmr — equity / notional floor.
    ``penalty_fee_rate``         fee on liquidated notional.
    ``mode``                     ``"partial"`` or ``"full"``.
    ``partial_close_fraction``   fraction of the open position closed
                                 per trigger in ``"partial"`` mode
                                 (Binance's rank-down step reduces the
                                 position one notional tier at a time;
                                 0.5 is a middle-of-the-road step).
    """

    maintenance_margin_rate: float = 0.005
    penalty_fee_rate: float = 0.002
    mode: str = "partial"
    partial_close_fraction: float = 0.5

    def __post_init__(self) -> None:
        if not (0.0 < self.maintenance_margin_rate < 1.0):
            raise ValueError("maintenance_margin_rate must be in (0, 1)")
        if not (0.0 <= self.penalty_fee_rate < 1.0):
            raise ValueError("penalty_fee_rate must be in [0, 1)")
        if self.mode not in _MODES:
            raise ValueError(
                f"mode must be one of {sorted(_MODES)}, got {self.mode!r}"
            )
        if not (0.0 < self.partial_close_fraction <= 1.0):
            raise ValueError("partial_close_fraction must be in (0, 1]")


DEFAULT_LIQUIDATION_POLICY = LiquidationPolicy()


def equity_at(
    qty: float,
    entry_price: float,
    wallet_balance: float,
    mark_price: float,
) -> float:
    """Margin-account equity at a mark price (unrealized PnL + wallet)."""
    return wallet_balance + qty * (mark_price - entry_price)


def margin_ratio(
    qty: float,
    entry_price: float,
    wallet_balance: float,
    mark_price: float,
) -> float:
    """Equity / position notional at ``mark_price``.

    The position is liquidatable when this falls to the maintenance
    margin rate.
    """
    notional = abs(qty) * mark_price
    if notional <= 0.0:
        raise ValueError("margin_ratio: non-positive notional")
    return equity_at(qty, entry_price, wallet_balance, mark_price) / notional


def liquidation_price(
    qty: float,
    entry_price: float,
    wallet_balance: float,
    maintenance_margin_rate: float,
) -> float:
    """Mark price at which equity equals the maintenance margin.

    Raises ``ValueError`` when the position can never be liquidated
    (e.g. leverage 1 long with no borrow — equity stays above the
    maintenance margin at every non-negative price).
    """
    if qty == 0.0:
        raise ValueError("liquidation_price: zero qty")
    side_sign = 1.0 if qty > 0 else -1.0
    denom = qty * (1.0 - side_sign * maintenance_margin_rate)
    if denom == 0.0:
        raise ValueError("liquidation_price: degenerate configuration")
    p_star = (qty * entry_price - wallet_balance) / denom
    if p_star <= 0.0:
        # Never liquidatable at a non-negative price (equity exceeds
        # the maintenance margin even at p -> 0 / p -> inf).
        raise ValueError(
            "liquidation_price: position is not liquidatable at any "
            f"positive price (p* = {p_star!r})"
        )
    return p_star


def is_liquidatable(
    qty: float,
    entry_price: float,
    wallet_balance: float,
    maintenance_margin_rate: float,
) -> bool:
    try:
        liquidation_price(
            qty, entry_price, wallet_balance, maintenance_margin_rate,
        )
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class LiquidationEvent:
    """One forced-deleveraging event."""

    ts_ns: int
    symbol: str
    side: str                 # side of the *position* ('LONG'/'SHORT')
    mode: str                 # 'PARTIAL' | 'FULL'
    liq_price: float          # trigger price p*
    exec_price: float         # actual forced-close price (worse on gaps)
    qty_closed: float         # signed, opposite sign of the position
    fee: float                # penalty fee charged
    remaining_qty: float      # signed position after the event (0 on FULL)
    remaining_equity: float   # wallet balance after the event
    deficit: float            # shortfall absorbed by the insurance fund


class LiquidationEngine:
    """Stateful per-position liquidation tracker.

    The engine owns the position and its wallet balance; each
    :meth:`on_bar` call checks the bar against the liquidation price
    and, on a touch, applies the policy's liquidation rule and mutates
    the tracked state.  ``closed`` becomes True after a full close
    (equity exhausted or full-mode liquidation).
    """

    def __init__(
        self,
        position: Position,
        policy: LiquidationPolicy = DEFAULT_LIQUIDATION_POLICY,
        wallet_balance: Optional[float] = None,
    ) -> None:
        if wallet_balance is None:
            wallet_balance = (
                abs(position.qty) * position.entry_price / position.leverage
            )
        if wallet_balance <= 0.0:
            raise ValueError("wallet_balance must be positive")
        if not is_liquidatable(
            position.qty, position.entry_price,
            wallet_balance, policy.maintenance_margin_rate,
        ):
            raise ValueError(
                "position is not liquidatable at any positive price "
                "with the given wallet balance"
            )
        self._position = position
        self._policy = policy
        self._wallet = float(wallet_balance)
        self._closed = False
        self._events: List[LiquidationEvent] = []

    @property
    def position(self) -> Position:
        return self._position

    @property
    def wallet_balance(self) -> float:
        return self._wallet

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def events(self) -> Tuple[LiquidationEvent, ...]:
        return tuple(self._events)

    def liq_price(self) -> float:
        return liquidation_price(
            self._position.qty, self._position.entry_price,
            self._wallet, self._policy.maintenance_margin_rate,
        )

    def on_bar(self, bar: Bar) -> Optional[LiquidationEvent]:
        """Check one bar; liquidate (once) if the range touches p*.

        Returns the :class:`LiquidationEvent` or ``None``.  Once the
        engine is ``closed`` every subsequent call returns ``None``.
        """
        if self._closed:
            return None
        qty = self._position.qty
        p_star = self.liq_price()
        if qty > 0:
            touched = bar.low <= p_star
            # gap down through p*: fill at the (worse) open
            exec_price = bar.open if bar.open < p_star else p_star
        else:
            touched = bar.high >= p_star
            exec_price = bar.open if bar.open > p_star else p_star
        if not touched:
            return None

        equity = equity_at(qty, self._position.entry_price,
                           self._wallet, exec_price)
        if self._policy.mode == "partial":
            # Rank-down step: close ``partial_close_fraction`` of the
            # open position.  The surviving half inherits the
            # remaining equity, so its margin ratio roughly doubles
            # (less the penalty fee) — the position steps back from
            # the liquidation boundary but stays leveraged, which is
            # exactly the death-spiral shape a sustained grind
            # produces on real venues.
            closed_qty = qty * self._policy.partial_close_fraction
            target_qty = qty - closed_qty
            fee = (abs(closed_qty) * exec_price
                   * self._policy.penalty_fee_rate)
            if fee >= equity:
                event = self._full_close(bar, p_star, exec_price,
                                         equity)
            else:
                realized = closed_qty * (
                    exec_price - self._position.entry_price
                )
                self._wallet += realized - fee
                self._position = Position(
                    symbol=self._position.symbol,
                    qty=target_qty,
                    entry_price=self._position.entry_price,
                    leverage=self._position.leverage,
                )
                event = LiquidationEvent(
                    ts_ns=bar.ts_ns,
                    symbol=self._position.symbol,
                    side=self._position.side,
                    mode="PARTIAL",
                    liq_price=p_star,
                    exec_price=exec_price,
                    qty_closed=-closed_qty,  # signed opposite the position
                    fee=fee,
                    remaining_qty=target_qty,
                    remaining_equity=self._wallet,
                    deficit=0.0,
                )
        else:
            event = self._full_close(bar, p_star, exec_price, equity)
        self._events.append(event)
        return event

    def _full_close(
        self,
        bar: Bar,
        p_star: float,
        exec_price: float,
        equity: float,
    ) -> LiquidationEvent:
        qty = self._position.qty
        fee = abs(qty) * exec_price * self._policy.penalty_fee_rate
        deficit = max(0.0, fee - equity)
        remaining_equity = max(0.0, equity - fee)
        event = LiquidationEvent(
            ts_ns=bar.ts_ns,
            symbol=self._position.symbol,
            side=self._position.side,
            mode="FULL",
            liq_price=p_star,
            exec_price=exec_price,
            qty_closed=-qty,
            fee=fee,
            remaining_qty=0.0,
            remaining_equity=remaining_equity,
            deficit=deficit,
        )
        self._wallet = remaining_equity
        self._closed = True
        return event


def simulate_liquidations(
    position: Position,
    bars: Sequence[Bar],
    policy: LiquidationPolicy = DEFAULT_LIQUIDATION_POLICY,
    wallet_balance: Optional[float] = None,
) -> Tuple[List[LiquidationEvent], LiquidationEngine]:
    """Drive a :class:`LiquidationEngine` over a bar sequence.

    Returns ``(events, engine)`` so the caller can inspect both the
    event stream and the terminal position/wallet state.
    """
    engine = LiquidationEngine(
        position, policy, wallet_balance=wallet_balance,
    )
    events: List[LiquidationEvent] = []
    for bar in bars:
        event = engine.on_bar(bar)
        if event is not None:
            events.append(event)
    return events, engine
