# `_shared.latency_model`

Source: `_shared/latency_model.py`

Backtest latency model (B7).

## class `CancelResult(order_id: 'str', status: 'CancelStatus', effective_ts_ns: 'Optional[int]') -> None`

CancelResult(order_id: 'str', status: 'CancelStatus', effective_ts_ns: 'Optional[int]')

## class `CancelStatus(*values)`

## class `EmpiricalLatency(samples: 'Tuple[LatencySample, ...]' = (), mode: 'str' = 'cycle') -> None`

Replay measured latencies.

### `sample(self, rng: 'random.Random', cursor: 'int' = 0) -> 'Tuple[LatencySample, int]'`

Return ``(sample, next_cursor)``.

| Parameter | Type | Default |
|---|---|---|
| `rng` | 'random.Random' | — |
| `cursor` | 'int' | 0 |

## class `FixedLatency(feed_ns: 'int' = 0, order_ns: 'int' = 0, cancel_ns: 'int' = 0) -> None`

Deterministic latency: every sample is identical.

### `sample(self, rng: 'random.Random') -> 'LatencySample'`

| Parameter | Type | Default |
|---|---|---|
| `rng` | 'random.Random' | — |

## class `LatencySample(feed_ns: 'int', order_ns: 'int', cancel_ns: 'int' = 0) -> None`

One latency observation, all in nanoseconds.

## class `LatencySimulator(model: 'object' = FixedLatency(feed_ns=0, order_ns=0, cancel_ns=0), *, seed: 'Optional[int]' = None) -> 'None'`

Stateful latency tracker for a backtest loop.

### `cancel(self, order_id: 'str', ts_ns: 'int') -> 'CancelResult'`

Attempt to cancel an in-flight order.

| Parameter | Type | Default |
|---|---|---|
| `order_id` | 'str' | — |
| `ts_ns` | 'int' | — |

### `fillable(self, order_id: 'str', ts_ns: 'int') -> 'bool'`

May this order execute at ``ts_ns``?

| Parameter | Type | Default |
|---|---|---|
| `order_id` | 'str' | — |
| `ts_ns` | 'int' | — |

### `mark_filled(self, order_id: 'str', ts_ns: 'int') -> 'None'`

Record that the order filled at ``ts_ns``.

| Parameter | Type | Default |
|---|---|---|
| `order_id` | 'str' | — |
| `ts_ns` | 'int' | — |

### `newly_live(self, from_ts_ns: 'int', to_ts_ns: 'int') -> 'List[str]'`

Order ids whose ``live_ts_ns`` falls inside ``[from_ts_ns, to_ts_ns)`` — the orders a bar covering that interval is the first to be able to fill.

| Parameter | Type | Default |
|---|---|---|
| `from_ts_ns` | 'int' | — |
| `to_ts_ns` | 'int' | — |

### `pending(self, order_id: 'str') -> 'Optional[PendingOrder]'`

| Parameter | Type | Default |
|---|---|---|
| `order_id` | 'str' | — |

### `submit(self, order_id: 'str', ts_ns: 'int') -> 'int'`

Register an order submitted at ``ts_ns``.

| Parameter | Type | Default |
|---|---|---|
| `order_id` | 'str' | — |
| `ts_ns` | 'int' | — |

## class `NormalLatency(feed_mean_ns: 'float' = 0.0, feed_std_ns: 'float' = 0.0, order_mean_ns: 'float' = 0.0, order_std_ns: 'float' = 0.0, cancel_mean_ns: 'float' = 0.0, cancel_std_ns: 'float' = 0.0) -> None`

Gaussian latency per leg, clipped at zero (latencies are non-negative; a heavy left tail is truncated rather than allowed to make an order executable in the past).

### `sample(self, rng: 'random.Random') -> 'LatencySample'`

| Parameter | Type | Default |
|---|---|---|
| `rng` | 'random.Random' | — |

## class `PendingOrder(order_id: 'str', submit_ts_ns: 'int', live_ts_ns: 'int', decision_ts_ns: 'int', cancel_effective_ts_ns: 'Optional[int]' = None, filled: 'bool' = False, cancelled: 'bool' = False) -> None`

Internal per-order latency state.

## `sample_latency(model: 'object', rng: 'random.Random', cursor: 'int' = 0) -> 'Tuple[LatencySample, int]'`

Uniform entry point over the three model types.

| Parameter | Type | Default |
|---|---|---|
| `model` | 'object' | — |
| `rng` | 'random.Random' | — |
| `cursor` | 'int' | 0 |
