# `_shared.market_making.cancel_replace`

Source: `_shared/market_making/cancel_replace.py`

Cancel-replace decision engine — amend vs cancel+place vs hold.

## class `CancelReplaceParams(tick_size: 'float' = 0.01, amend_threshold_ticks: 'int' = 2, size_tolerance_fraction: 'float' = 0.01) -> None`

Tunables for amend-vs-replace decisions.

## class `OrderAction(action: 'ActionKind', side: 'Side', order_id: 'str | None', price: 'float', size: 'float', reason: 'str') -> None`

One exchange action. ``order_id`` is None for ``place``.

## class `QuoteTarget(side: 'Side', price: 'float', size: 'float') -> None`

Where the strategy wants to be quoting on one side.

## class `RestingOrder(order_id: 'str', side: 'Side', price: 'float', size: 'float') -> None`

An order currently working on the venue.

## `decide_actions(resting: 'Sequence[RestingOrder]', targets: 'Sequence[QuoteTarget]', params: 'CancelReplaceParams') -> 'tuple[OrderAction, ...]'`

Compare resting orders with quote targets; return ordered actions.

| Parameter | Type | Default |
|---|---|---|
| `resting` | 'Sequence[RestingOrder]' | — |
| `targets` | 'Sequence[QuoteTarget]' | — |
| `params` | 'CancelReplaceParams' | — |
