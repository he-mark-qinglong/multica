# `_shared.market_making.quoting_engine`

Source: `_shared/market_making/quoting_engine.py`

Quote generation engine for market making.

## class `Quote(bid_price: 'float', ask_price: 'float', bid_size: 'float', ask_size: 'float', timestamp: 'pd.Timestamp', reservation_price: 'float', spread_bp: 'float', skew_bp: 'float') -> None`

A two-sided quote with audit fields.

## class `QuotingParams(base_spread_bp: 'float' = 2.0, min_spread_ticks: 'int' = 2, size_usd: 'float' = 1000.0, inventory_skew_factor: 'float' = 1.0, vol_spread_coeff: 'float' = 0.5, tick_size: 'float' = 0.01) -> None`

Tunables for spread and size computation.

## `compute_spread(sigma: 'float', inventory_ratio: 'float', adverse_selection_penalty: 'float', tick_size: 'float', base_spread_bp: 'float' = 2.0, min_spread_ticks: 'int' = 2, vol_spread_coeff: 'float' = 0.5) -> 'float'`

Dynamic half-spread in **price** units.

| Parameter | Type | Default |
|---|---|---|
| `sigma` | 'float' | — |
| `inventory_ratio` | 'float' | — |
| `adverse_selection_penalty` | 'float' | — |
| `tick_size` | 'float' | — |
| `base_spread_bp` | 'float' | 2.0 |
| `min_spread_ticks` | 'int' | 2 |
| `vol_spread_coeff` | 'float' | 0.5 |

## `generate_quotes(reservation_price: 'float', sigma: 'float', inventory_state: 'InventoryState', adverse_selection_penalty_bp: 'float', mcls_size_multiplier: 'float', params: 'QuotingParams', timestamp: 'pd.Timestamp') -> 'Quote | None'`

Generate a two-sided quote.

| Parameter | Type | Default |
|---|---|---|
| `reservation_price` | 'float' | — |
| `sigma` | 'float' | — |
| `inventory_state` | 'InventoryState' | — |
| `adverse_selection_penalty_bp` | 'float' | — |
| `mcls_size_multiplier` | 'float' | — |
| `params` | 'QuotingParams' | — |
| `timestamp` | 'pd.Timestamp' | — |
