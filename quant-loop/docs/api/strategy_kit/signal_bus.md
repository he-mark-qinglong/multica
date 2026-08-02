# `_shared.strategy_kit.signal_bus`

Source: `_shared/strategy_kit/signal_bus.py`

Inter-strategy signal bus — in-memory pub/sub with TTL and versioning.

## class `BusConfig(history_size: 'int' = 100, spill_path: 'Optional[str]' = None) -> None`

Signal bus parameters.

## class `Signal(symbol: 'str', signal_type: 'str', value: 'Any', ts: 'float', ttl: 'Optional[float]' = None, version: 'int' = 0, publisher: 'str' = '') -> None`

One published fact on the bus. Immutable by construction.

### `is_valid(self, now: 'float') -> 'bool'`

True iff the signal has not expired at ``now`` (inclusive end).

| Parameter | Type | Default |
|---|---|---|
| `now` | 'float' | — |

## class `SignalBus(config: 'Optional[BusConfig]' = None) -> 'None'`

In-memory pub/sub bus. Thread-unsafe by design — one loop owns it.

### `get(self, symbol: 'str', signal_type: 'str', now: 'float') -> 'Optional[Signal]'`

Latest non-expired signal for the key, or None.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |
| `signal_type` | 'str' | — |
| `now` | 'float' | — |

### `get_since(self, symbol: 'str', signal_type: 'str', now: 'float', min_version: 'int') -> 'Optional[Signal]'`

Latest valid signal strictly newer than ``min_version``, else None.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |
| `signal_type` | 'str' | — |
| `now` | 'float' | — |
| `min_version` | 'int' | — |

### `get_value(self, symbol: 'str', signal_type: 'str', now: 'float', default: 'Any' = None) -> 'Any'`

Convenience: payload of ``get`` or ``default`` when absent/expired.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |
| `signal_type` | 'str' | — |
| `now` | 'float' | — |
| `default` | 'Any' | None |

### `history(self, symbol: 'str', signal_type: 'str', n: 'int', now: 'Optional[float]' = None) -> 'List[Signal]'`

Newest ``n`` retained signals for the key (newest first).

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |
| `signal_type` | 'str' | — |
| `n` | 'int' | — |
| `now` | 'Optional[float]' | None |

### `keys(self) -> 'List[Key]'`

All keys that currently have any retained signal.

### `publish(self, symbol: 'str', signal_type: 'str', value: 'Any', ts: 'float', ttl: 'Optional[float]' = None, publisher: 'str' = '') -> 'Signal'`

Post a signal; returns the stamped (immutable) Signal.

| Parameter | Type | Default |
|---|---|---|
| `symbol` | 'str' | — |
| `signal_type` | 'str' | — |
| `value` | 'Any' | — |
| `ts` | 'float' | — |
| `ttl` | 'Optional[float]' | None |
| `publisher` | 'str' | '' |

## `load_spill(path: 'str', now: 'Optional[float]' = None) -> 'SignalBus'`

Rebuild a bus from a spill file written by another process.

| Parameter | Type | Default |
|---|---|---|
| `path` | 'str' | — |
| `now` | 'Optional[float]' | None |
