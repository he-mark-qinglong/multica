# `_shared.partial_fill`

Source: `_shared/partial_fill.py`

Backtest partial-fill simulator (B5).

## class `Bar(ts_ns: 'int', open: 'float', high: 'float', low: 'float', close: 'float', volume: 'float') -> None`

One OHLCV bar.  ``volume`` is in base-asset units (same unit as :attr:`OrderSpec.qty`).

## class `Fill(order_id: 'str', ts_ns: 'int', price: 'float', qty: 'float', fill_ratio: 'float', remaining_qty: 'float', reason: 'str') -> None`

The (possibly partial) execution of one order inside one bar.

## class `OrderSpec(order_id: 'str', side: 'str', qty: 'float', price: 'Optional[float]' = None, order_type: 'str' = 'LIMIT') -> None`

An order to be filled inside a bar.

## class `PartialFillPolicy(participation_rate: 'float' = 0.25, touch_fill_factor: 'float' = 0.5, allow_price_improvement: 'bool' = False, price_epsilon: 'float' = 1e-09) -> None`

Tuning knobs for the conservative fill model.

## `fill_price(order: 'OrderSpec', bar: 'Bar', policy: 'PartialFillPolicy' = PartialFillPolicy(participation_rate=0.25, touch_fill_factor=0.5, allow_price_improvement=False, price_epsilon=1e-09)) -> 'float'`

Execution price under the conservative model.

| Parameter | Type | Default |
|---|---|---|
| `order` | 'OrderSpec' | — |
| `bar` | 'Bar' | — |
| `policy` | 'PartialFillPolicy' | PartialFillPolicy(participation_rate=0.25, touch_fill_factor=0.5, allow_price_improvement=False, price_epsilon=1e-09) |

## `is_touched(order: 'OrderSpec', bar: 'Bar', policy: 'PartialFillPolicy' = PartialFillPolicy(participation_rate=0.25, touch_fill_factor=0.5, allow_price_improvement=False, price_epsilon=1e-09)) -> 'bool'`

True when the bar's price range reaches the order's limit.

| Parameter | Type | Default |
|---|---|---|
| `order` | 'OrderSpec' | — |
| `bar` | 'Bar' | — |
| `policy` | 'PartialFillPolicy' | PartialFillPolicy(participation_rate=0.25, touch_fill_factor=0.5, allow_price_improvement=False, price_epsilon=1e-09) |

## `simulate_bar_fill(order: 'OrderSpec', bar: 'Bar', policy: 'PartialFillPolicy' = PartialFillPolicy(participation_rate=0.25, touch_fill_factor=0.5, allow_price_improvement=False, price_epsilon=1e-09)) -> 'Fill'`

Simulate a single order against a single bar.

| Parameter | Type | Default |
|---|---|---|
| `order` | 'OrderSpec' | — |
| `bar` | 'Bar' | — |
| `policy` | 'PartialFillPolicy' | PartialFillPolicy(participation_rate=0.25, touch_fill_factor=0.5, allow_price_improvement=False, price_epsilon=1e-09) |

## `simulate_bar_fills(orders: 'Sequence[OrderSpec]', bar: 'Bar', policy: 'PartialFillPolicy' = PartialFillPolicy(participation_rate=0.25, touch_fill_factor=0.5, allow_price_improvement=False, price_epsilon=1e-09)) -> 'List[Fill]'`

Simulate several orders competing for one bar's volume.

| Parameter | Type | Default |
|---|---|---|
| `orders` | 'Sequence[OrderSpec]' | — |
| `bar` | 'Bar' | — |
| `policy` | 'PartialFillPolicy' | PartialFillPolicy(participation_rate=0.25, touch_fill_factor=0.5, allow_price_improvement=False, price_epsilon=1e-09) |
