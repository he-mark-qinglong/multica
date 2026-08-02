# `_shared.market_making.queue_position`

Source: `_shared/market_making/queue_position.py`

Queue position fill probability model.

## class `QueueParams(base_fill_rate: 'float' = 0.13, decay_per_second: 'float' = 0.02, aggressiveness_bonus: 'float' = 2.0) -> None`

Queue position model parameters.

## `expected_fill_value(fill_prob: 'float', edge_bp: 'float') -> 'float'`

Expected value of posting an order = P(fill) × edge.

| Parameter | Type | Default |
|---|---|---|
| `fill_prob` | 'float' | — |
| `edge_bp` | 'float' | — |

## `fill_probability(seconds_in_queue: 'float', ticks_from_best: 'int', market_fill_rate: 'float', params: 'QueueParams' = QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0)) -> 'float'`

Estimate probability of being filled.

| Parameter | Type | Default |
|---|---|---|
| `seconds_in_queue` | 'float' | — |
| `ticks_from_best` | 'int' | — |
| `market_fill_rate` | 'float' | — |
| `params` | 'QueueParams' | QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0) |

## `optimal_quote_aggressiveness(spread_bp: 'float', adverse_selection_bp: 'float', market_fill_rate: 'float' = 0.13) -> 'int'`

Decide how many ticks from best to place our order.

| Parameter | Type | Default |
|---|---|---|
| `spread_bp` | 'float' | — |
| `adverse_selection_bp` | 'float' | — |
| `market_fill_rate` | 'float' | 0.13 |
