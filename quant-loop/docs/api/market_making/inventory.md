# `_shared.market_making.inventory`

Source: `_shared/market_making/inventory.py`

Inventory state tracking and risk limits for market making.

## class `InventoryState(net_qty: 'float' = 0.0, gross_qty: 'float' = 0.0, avg_price: 'float' = 0.0, notional_usd: 'float' = 0.0, last_fill_ts: 'pd.Timestamp | None' = None, max_inventory: 'float' = 1.0, open_since: 'pd.Timestamp | None' = None) -> None`

Net position held by the market maker.

## `empty_inventory(max_inventory: 'float' = 1.0) -> 'InventoryState'`

Factory for a fresh flat position.

| Parameter | Type | Default |
|---|---|---|
| `max_inventory` | 'float' | 1.0 |

## `flatten_required(state: 'InventoryState', current_price: 'float', max_hold_seconds: 'float', sl_bp: 'float' = 10.0, current_ts: 'pd.Timestamp | None' = None) -> 'bool'`

Decide whether the current inventory must be force-flattened.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'InventoryState' | — |
| `current_price` | 'float' | — |
| `max_hold_seconds` | 'float' | — |
| `sl_bp` | 'float' | 10.0 |
| `current_ts` | 'pd.Timestamp | None' | None |

## `inventory_skew(net_qty: 'float', max_inventory: 'float') -> 'float'`

Inventory skew coefficient in [-1, 1].

| Parameter | Type | Default |
|---|---|---|
| `net_qty` | 'float' | — |
| `max_inventory` | 'float' | — |

## `update_inventory(state: 'InventoryState', fill_qty: 'float', fill_price: 'float', ts: 'pd.Timestamp') -> 'InventoryState'`

Apply a fill and return a new :class:`InventoryState`.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'InventoryState' | — |
| `fill_qty` | 'float' | — |
| `fill_price` | 'float' | — |
| `ts` | 'pd.Timestamp' | — |
