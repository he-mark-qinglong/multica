# `_shared.l2.replay`

Source: `_shared/l2/replay.py`

L2 diff-driven replay engine (B4).

## class `L2Fill(order_id: 'str', ts_ns: 'int', price: 'float', qty: 'float', reason: 'str') -> None`

One fill leg of a replayed order (one per consumed book level).

## class `ReplayOrder(order_id: 'str', side: 'str', qty: 'float', price: 'Optional[float]', ts_placed_ns: 'int' = 0) -> None`

An order submitted into the replay.

## class `ReplayPolicy(queue_enabled: 'bool' = True, queue_params: 'QueueParams' = QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), market_fill_rate: 'float' = 0.13, price_epsilon: 'float' = 1e-09) -> None`

Tuning knobs for the L2 replay fill model.

## class `ReplayResult(fills: 'Tuple[L2Fill, ...]', final_state: 'BookState', n_events: 'int') -> None`

Output of :func:`replay`.

### `fills_for(self, order_id: 'str') -> 'Tuple[L2Fill, ...]'`

| Parameter | Type | Default |
|---|---|---|
| `order_id` | 'str' | — |

## `replay(events: 'Sequence[Union[BookState, BookDiff]]', orders: 'Sequence[ReplayOrder]', policy: 'ReplayPolicy' = ReplayPolicy(queue_enabled=True, queue_params=QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), market_fill_rate=0.13, price_epsilon=1e-09)) -> 'ReplayResult'`

Replay an event stream and match orders against the evolving book.

| Parameter | Type | Default |
|---|---|---|
| `events` | 'Sequence[Union[BookState, BookDiff]]' | — |
| `orders` | 'Sequence[ReplayOrder]' | — |
| `policy` | 'ReplayPolicy' | ReplayPolicy(queue_enabled=True, queue_params=QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), market_fill_rate=0.13, price_epsilon=1e-09) |

## `simulate_order(order: 'ReplayOrder', state: 'BookState', policy: 'ReplayPolicy' = ReplayPolicy(queue_enabled=True, queue_params=QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), market_fill_rate=0.13, price_epsilon=1e-09)) -> 'Tuple[L2Fill, ...]'`

Simulate one order against one book state.

| Parameter | Type | Default |
|---|---|---|
| `order` | 'ReplayOrder' | — |
| `state` | 'BookState' | — |
| `policy` | 'ReplayPolicy' | ReplayPolicy(queue_enabled=True, queue_params=QueueParams(base_fill_rate=0.13, decay_per_second=0.02, aggressiveness_bonus=2.0), market_fill_rate=0.13, price_epsilon=1e-09) |
