# `_shared.liquidation_sim`

Source: `_shared/liquidation_sim.py`

Per-bar liquidation simulator for leveraged positions (B13).

## class `LiquidationEngine(position: 'Position', policy: 'LiquidationPolicy' = LiquidationPolicy(maintenance_margin_rate=0.005, penalty_fee_rate=0.002, mode='partial', partial_close_fraction=0.5), wallet_balance: 'Optional[float]' = None) -> 'None'`

Stateful per-position liquidation tracker.

### `liq_price(self) -> 'float'`

### `on_bar(self, bar: 'Bar') -> 'Optional[LiquidationEvent]'`

Check one bar; liquidate (once) if the range touches p*.

| Parameter | Type | Default |
|---|---|---|
| `bar` | 'Bar' | — |

## class `LiquidationEvent(ts_ns: 'int', symbol: 'str', side: 'str', mode: 'str', liq_price: 'float', exec_price: 'float', qty_closed: 'float', fee: 'float', remaining_qty: 'float', remaining_equity: 'float', deficit: 'float') -> None`

One forced-deleveraging event.

## class `LiquidationPolicy(maintenance_margin_rate: 'float' = 0.005, penalty_fee_rate: 'float' = 0.002, mode: 'str' = 'partial', partial_close_fraction: 'float' = 0.5) -> None`

Liquidation rule configuration.

## class `Position(symbol: 'str', qty: 'float', entry_price: 'float', leverage: 'float') -> None`

A leveraged position.  ``qty`` is signed: + long, − short.

## `is_liquidatable(qty: 'float', entry_price: 'float', wallet_balance: 'float', maintenance_margin_rate: 'float') -> 'bool'`

| Parameter | Type | Default |
|---|---|---|
| `qty` | 'float' | — |
| `entry_price` | 'float' | — |
| `wallet_balance` | 'float' | — |
| `maintenance_margin_rate` | 'float' | — |

## `liquidation_price(qty: 'float', entry_price: 'float', wallet_balance: 'float', maintenance_margin_rate: 'float') -> 'float'`

Mark price at which equity equals the maintenance margin.

| Parameter | Type | Default |
|---|---|---|
| `qty` | 'float' | — |
| `entry_price` | 'float' | — |
| `wallet_balance` | 'float' | — |
| `maintenance_margin_rate` | 'float' | — |

## `margin_ratio(qty: 'float', entry_price: 'float', wallet_balance: 'float', mark_price: 'float') -> 'float'`

Equity / position notional at ``mark_price``.

| Parameter | Type | Default |
|---|---|---|
| `qty` | 'float' | — |
| `entry_price` | 'float' | — |
| `wallet_balance` | 'float' | — |
| `mark_price` | 'float' | — |

## `simulate_liquidations(position: 'Position', bars: 'Sequence[Bar]', policy: 'LiquidationPolicy' = LiquidationPolicy(maintenance_margin_rate=0.005, penalty_fee_rate=0.002, mode='partial', partial_close_fraction=0.5), wallet_balance: 'Optional[float]' = None) -> 'Tuple[List[LiquidationEvent], LiquidationEngine]'`

Drive a :class:`LiquidationEngine` over a bar sequence.

| Parameter | Type | Default |
|---|---|---|
| `position` | 'Position' | — |
| `bars` | 'Sequence[Bar]' | — |
| `policy` | 'LiquidationPolicy' | LiquidationPolicy(maintenance_margin_rate=0.005, penalty_fee_rate=0.002, mode='partial', partial_close_fraction=0.5) |
| `wallet_balance` | 'Optional[float]' | None |
