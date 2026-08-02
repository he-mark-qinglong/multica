# `_shared.ops.multi_runner`

Source: `_shared/ops/multi_runner.py`

Multi-strategy parallel runner (H9).

## class `MultiRunner(specs: 'Sequence[StrategySpec]', signal_bus_path, alerter: 'Optional[Alerter]' = None, heartbeat_timeout_sec: 'float' = 60.0)`

Launches and supervises a fixed set of strategy child processes.

### `poll(self, now: 'Optional[float]' = None) -> 'PollResult'`

One aggregation round: heartbeat freshness + unexpected exits.

| Parameter | Type | Default |
|---|---|---|
| `now` | 'Optional[float]' | None |

### `start_all(self) -> 'None'`

Launch every strategy. Idempotent for already-running children.

### `stop_all(self, grace_sec: 'float' = 5.0) -> 'Mapping[str, int]'`

SIGTERM all children, then SIGKILL survivors after the grace period.

| Parameter | Type | Default |
|---|---|---|
| `grace_sec` | 'float' | 5.0 |

## class `PollResult(statuses: 'Mapping[str, HeartbeatStatus]', returncodes: 'Mapping[str, int]', alerts: 'Tuple[Alert, ...]') -> None`

One coordination round over all children.

## class `StrategySpec(name: 'str', argv: 'Tuple[str, ...]', heartbeat_path: 'Path', isolation: 'IsolationSpec' = IsolationSpec(cpu_cores=None, mem_mb=None, restart_policy='never', max_restarts=3, mem_poll_interval_s=0.05), env: 'Mapping[str, str]' = <factory>) -> None`

One strategy to run as a child process.

## `build_child_env(spec: 'StrategySpec', signal_bus_path, base: 'Optional[Mapping[str, str]]' = None) -> 'Dict[str, str]'`

Environment for one child: parent env + spec.env + bus path. Pure.

| Parameter | Type | Default |
|---|---|---|
| `spec` | 'StrategySpec' | — |
| `signal_bus_path` | — | — |
| `base` | 'Optional[Mapping[str, str]]' | None |

## `dead_process_alert(name: 'str', returncode: 'int', now: 'Optional[float]' = None) -> 'Alert'`

CRITICAL alert for a child that exited on its own.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `returncode` | 'int' | — |
| `now` | 'Optional[float]' | None |
