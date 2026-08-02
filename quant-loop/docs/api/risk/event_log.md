# `_shared.risk.event_log`

Source: `_shared/risk/event_log.py`

Risk event audit log (D20).

## class `RiskEvent(ts: 'float', event_type: 'str', strategy: 'str' = '', severity: 'str' = 'WARN', message: 'str' = '', context: 'Mapping[str, Any]' = <factory>) -> None`

One immutable risk event.

### `to_json(self) -> 'str'`

## class `RiskEventType(*values)`

Kinds of auditable risk events.

## `append_event(path, event: 'RiskEvent') -> 'RiskEvent'`

Append one event as a JSON line; returns the event written.

| Parameter | Type | Default |
|---|---|---|
| `path` | — | — |
| `event` | 'RiskEvent' | — |

## `filter_events(events: 'Sequence[RiskEvent]', event_type: 'Optional[str]' = None, strategy: 'Optional[str]' = None, start_ts: 'Optional[float]' = None, end_ts: 'Optional[float]' = None) -> 'Tuple[RiskEvent, ...]'`

Filter events by type / strategy / time window [start_ts, end_ts]. Pure.

| Parameter | Type | Default |
|---|---|---|
| `events` | 'Sequence[RiskEvent]' | — |
| `event_type` | 'Optional[str]' | None |
| `strategy` | 'Optional[str]' | None |
| `start_ts` | 'Optional[float]' | None |
| `end_ts` | 'Optional[float]' | None |

## `load_events(path) -> 'Tuple[RiskEvent, ...]'`

Load all events from a jsonl file; empty tuple if the file is absent.

| Parameter | Type | Default |
|---|---|---|
| `path` | — | — |

## `query_events(path, event_type: 'Optional[str]' = None, strategy: 'Optional[str]' = None, start_ts: 'Optional[float]' = None, end_ts: 'Optional[float]' = None) -> 'Tuple[RiskEvent, ...]'`

Load + filter in one call.

| Parameter | Type | Default |
|---|---|---|
| `path` | — | — |
| `event_type` | 'Optional[str]' | None |
| `strategy` | 'Optional[str]' | None |
| `start_ts` | 'Optional[float]' | None |
| `end_ts` | 'Optional[float]' | None |
