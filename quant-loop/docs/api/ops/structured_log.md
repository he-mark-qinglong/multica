# `_shared.ops.structured_log`

Source: `_shared/ops/structured_log.py`

JSON structured logger (H6).

## class `EventType(*values)`

Closed vocabulary of log event types for the trading stack.

## class `JsonLogger(sink: 'Union[str, Path, TextIO]')`

Appends one JSON line per event to a file path or text stream.

### `close(self) -> 'None'`

### `log(self, event: 'Union[EventType, str]', level: 'LogLevel' = <LogLevel.INFO: 'INFO'>, data: 'Optional[Mapping[str, Any]]' = None, ts: 'Optional[float]' = None) -> 'LogRecord'`

| Parameter | Type | Default |
|---|---|---|
| `event` | 'Union[EventType, str]' | — |
| `level` | 'LogLevel' | <LogLevel.INFO: 'INFO'> |
| `data` | 'Optional[Mapping[str, Any]]' | None |
| `ts` | 'Optional[float]' | None |

## class `LogLevel(*values)`

## class `LogRecord(ts: 'float', level: 'str', event: 'str', data: 'Mapping[str, Any]' = <factory>) -> None`

One immutable structured log line.

## `format_record(record: 'LogRecord') -> 'str'`

Render a record as one JSON line (no trailing newline). Pure.

| Parameter | Type | Default |
|---|---|---|
| `record` | 'LogRecord' | — |

## `make_record(event: 'Union[EventType, str]', level: 'LogLevel' = <LogLevel.INFO: 'INFO'>, data: 'Optional[Mapping[str, Any]]' = None, ts: 'Optional[float]' = None) -> 'LogRecord'`

Build a LogRecord, validating the event against EventType.

| Parameter | Type | Default |
|---|---|---|
| `event` | 'Union[EventType, str]' | — |
| `level` | 'LogLevel' | <LogLevel.INFO: 'INFO'> |
| `data` | 'Optional[Mapping[str, Any]]' | None |
| `ts` | 'Optional[float]' | None |
