# `_shared.ops.isolation`

Source: `_shared/ops/isolation.py`

Per-strategy process resource isolation (H10).

## class `ExitKind(*values)`

How a wrapped child terminated.

## class `IsolationSpec(cpu_cores: 'Tuple[int, ...] | None' = None, mem_mb: 'float | None' = None, restart_policy: 'str' = 'never', max_restarts: 'int' = 3, mem_poll_interval_s: 'float' = 0.05) -> None`

Resource envelope for one strategy process.

## class `RunResult(argv: 'Tuple[str, ...]', exit_kind: 'str', returncode: 'int', stderr_tail: 'str', duration_s: 'float', restarts: 'int', killed_by_watchdog: 'bool') -> None`

Outcome of one :func:`run_isolated` call (including restarts).

## `run_isolated(argv: 'Sequence[str]', spec: 'IsolationSpec') -> 'RunResult'`

Run ``argv`` as a child process under ``spec``.

| Parameter | Type | Default |
|---|---|---|
| `argv` | 'Sequence[str]' | — |
| `spec` | 'IsolationSpec' | — |
