# `_shared.ops.alerting`

Source: `_shared/ops/alerting.py`

Structured alerting with pluggable sinks (H5).

## class `Alert(ts: 'float', level: 'str', rule: 'str', message: 'str', context: 'Mapping[str, Any]' = <factory>) -> None`

One immutable structured alert.

### `to_json(self) -> 'str'`

## class `AlertLevel(*values)`

## class `AlertSink(*args, **kwargs)`

### `emit(self, alert: 'Alert') -> 'None'`

| Parameter | Type | Default |
|---|---|---|
| `alert` | 'Alert' | — |

## class `Alerter(sinks: 'Sequence[AlertSink]' = ())`

Dispatches alerts to all sinks; a sink failure never blocks the rest.

### `dispatch(self, alert: 'Alert') -> 'Alert'`

| Parameter | Type | Default |
|---|---|---|
| `alert` | 'Alert' | — |

### `evaluate(self, *alerts: 'Optional[Alert]') -> 'Tuple[Alert, ...]'`

Dispatch every non-None rule result; returns what was dispatched.

| Parameter | Type | Default |
|---|---|---|
| `alerts` | 'Optional[Alert]' | — |

## class `LogFileSink(path)`

Appends one JSON alert per line to a file.

### `emit(self, alert: 'Alert') -> 'None'`

| Parameter | Type | Default |
|---|---|---|
| `alert` | 'Alert' | — |

## class `WebhookSink(url: 'str', timeout_sec: 'float' = 5.0)`

POSTs the alert as JSON to a webhook URL (Slack/Discord-compatible).

### `emit(self, alert: 'Alert') -> 'None'`

| Parameter | Type | Default |
|---|---|---|
| `alert` | 'Alert' | — |

## `check_data_gap(last_data_ts: 'float', now_ts: 'float', max_gap_sec: 'float', feed: 'str' = 'market_data') -> 'Optional[Alert]'`

CRITICAL if no data has arrived for more than max_gap_sec seconds.

| Parameter | Type | Default |
|---|---|---|
| `last_data_ts` | 'float' | — |
| `now_ts` | 'float' | — |
| `max_gap_sec` | 'float' | — |
| `feed` | 'str' | 'market_data' |

## `check_drawdown(peak_equity: 'float', current_equity: 'float', threshold_pct: 'float', now: 'Optional[float]' = None) -> 'Optional[Alert]'`

CRITICAL if drawdown from peak exceeds threshold_pct (e.g. 10.0 = 10%).

| Parameter | Type | Default |
|---|---|---|
| `peak_equity` | 'float' | — |
| `current_equity` | 'float' | — |
| `threshold_pct` | 'float' | — |
| `now` | 'Optional[float]' | None |

## `check_kill_switch(kill_triggered: 'bool', reason: 'str' = '', now: 'Optional[float]' = None) -> 'Optional[Alert]'`

CRITICAL whenever the runner's kill switch has latched.

| Parameter | Type | Default |
|---|---|---|
| `kill_triggered` | 'bool' | — |
| `reason` | 'str' | '' |
| `now` | 'Optional[float]' | None |
