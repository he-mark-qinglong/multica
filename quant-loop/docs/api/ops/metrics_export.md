# `_shared.ops.metrics_export`

Source: `_shared/ops/metrics_export.py`

Prometheus text exposition exporter (H7).

## class `MetricsRegistry() -> 'None'`

In-memory registry of gauges and counters keyed by (name, labels).

### `get(self, name: 'str', labels: 'Optional[Mapping[str, str]]' = None) -> 'float'`

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `labels` | 'Optional[Mapping[str, str]]' | None |

### `inc_counter(self, name: 'str', amount: 'float' = 1.0, labels: 'Optional[Mapping[str, str]]' = None) -> 'None'`

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `amount` | 'float' | 1.0 |
| `labels` | 'Optional[Mapping[str, str]]' | None |

### `register_counter(self, name: 'str', help: 'str' = '') -> 'None'`

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `help` | 'str' | '' |

### `register_gauge(self, name: 'str', help: 'str' = '') -> 'None'`

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `help` | 'str' | '' |

### `render_prometheus(self) -> 'str'`

Render the standard text exposition format. Pure w.r.t. state.

### `set_gauge(self, name: 'str', value: 'float', labels: 'Optional[Mapping[str, str]]' = None) -> 'None'`

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `value` | 'float' | — |
| `labels` | 'Optional[Mapping[str, str]]' | None |

## `runner_metrics_registry() -> 'MetricsRegistry'`

Pre-registered registry with the standard paper-runner metrics.

## `snapshot_runner_state(registry: 'MetricsRegistry', state: 'Mapping[str, float]', strategy: 'Optional[str]' = None) -> 'MetricsRegistry'`

Push a paper-runner state snapshot into the registry.

| Parameter | Type | Default |
|---|---|---|
| `registry` | 'MetricsRegistry' | — |
| `state` | 'Mapping[str, float]' | — |
| `strategy` | 'Optional[str]' | None |
