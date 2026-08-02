# `_shared.ops.heartbeat`

Source: `_shared/ops/heartbeat.py`

Heartbeat writer + timeout watcher (H14, H15).

## class `HeartbeatStatus(alive: 'bool', age_sec: 'float', last_ts: 'Optional[float]', state: 'str', timeout_sec: 'float') -> None`

Result of one freshness check.

## class `HeartbeatWatcher(path: 'Path', timeout_sec: 'float', process: 'str' = 'strategy') -> None`

Convenience wrapper combining check_heartbeat + heartbeat_alert.

### `check(self, now: 'Optional[float]' = None) -> 'Optional[Alert]'`

| Parameter | Type | Default |
|---|---|---|
| `now` | 'Optional[float]' | None |

## `check_heartbeat(path, timeout_sec: 'float', now: 'Optional[float]' = None) -> 'HeartbeatStatus'`

Pure freshness check: alive iff the last beat is within timeout_sec.

| Parameter | Type | Default |
|---|---|---|
| `path` | — | — |
| `timeout_sec` | 'float' | — |
| `now` | 'Optional[float]' | None |

## `heartbeat_alert(status: 'HeartbeatStatus', process: 'str' = 'strategy', now: 'Optional[float]' = None) -> 'Optional[Alert]'`

Map a stale status to a CRITICAL alert; None when alive (H15).

| Parameter | Type | Default |
|---|---|---|
| `status` | 'HeartbeatStatus' | — |
| `process` | 'str' | 'strategy' |
| `now` | 'Optional[float]' | None |

## `read_beat(path) -> 'Optional[Mapping[str, Any]]'`

Read the beat file; None if missing or corrupt.

| Parameter | Type | Default |
|---|---|---|
| `path` | — | — |

## `write_beat(path, state: 'str' = 'running', ts: 'Optional[float]' = None, extra: 'Optional[Mapping[str, Any]]' = None) -> 'float'`

Atomically write a heartbeat file; returns the beat timestamp.

| Parameter | Type | Default |
|---|---|---|
| `path` | — | — |
| `state` | 'str' | 'running' |
| `ts` | 'Optional[float]' | None |
| `extra` | 'Optional[Mapping[str, Any]]' | None |
