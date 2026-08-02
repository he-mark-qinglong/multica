# `_shared.ops.supervisor`

Source: `_shared/ops/supervisor.py`

Process supervisor: crash-restart, version rollback, graceful drain (H16, H17, H4).

## class `LaunchRecord(attempt: 'int', pid: 'int', started_ts: 'float', exit_code: 'Optional[int]', log_path: 'str', version: 'Mapping[str, str]' = <factory>) -> None`

One supervised child process launch.

## class `RestartPolicy(max_restarts: 'int' = 5, base_delay_sec: 'float' = 1.0, backoff_factor: 'float' = 2.0, max_delay_sec: 'float' = 60.0, keep_logs: 'int' = 10, clean_exit_codes: 'Tuple[int, ...]' = (0,)) -> None`

Tunables for crash-restart behaviour.

## class `Supervisor(cmd: 'Sequence[str]', workdir, log_dir, policy: 'RestartPolicy' = RestartPolicy(max_restarts=5, base_delay_sec=1.0, backoff_factor=2.0, max_delay_sec=60.0, keep_logs=10, clean_exit_codes=(0,)), ledger: 'Optional[VersionLedger]' = None, repo_dir=None)`

Runs ``cmd`` as a child process with restart + drain semantics.

### `drain(self, timeout_sec: 'float' = 30.0) -> 'bool'`

Graceful stop (H4): SIGTERM, wait up to timeout, then SIGKILL.

| Parameter | Type | Default |
|---|---|---|
| `timeout_sec` | 'float' | 30.0 |

### `supervise(self) -> 'int'`

Run the child, restarting on crash with exponential backoff.

## class `VersionLedger(ledger_path, pid_path)`

JSONL ledger of launched versions + pid file; supports rollback().

### `current_pid(self) -> 'Optional[int]'`

### `history(self) -> 'Tuple[Mapping[str, object], ...]'`

### `record_launch(self, pid: 'int', version_dir: 'str', git_hash: 'str' = '', ts: 'Optional[float]' = None) -> 'Mapping[str, object]'`

| Parameter | Type | Default |
|---|---|---|
| `pid` | 'int' | — |
| `version_dir` | 'str' | — |
| `git_hash` | 'str' | '' |
| `ts` | 'Optional[float]' | None |

### `rollback(self) -> 'Optional[Mapping[str, object]]'`

Return the previous version's info (the one before the latest).

## `backoff_delays(policy: 'RestartPolicy' = RestartPolicy(max_restarts=5, base_delay_sec=1.0, backoff_factor=2.0, max_delay_sec=60.0, keep_logs=10, clean_exit_codes=(0,))) -> 'Tuple[float, ...]'`

Pure: the delay before each restart attempt, capped at max_delay_sec.

| Parameter | Type | Default |
|---|---|---|
| `policy` | 'RestartPolicy' | RestartPolicy(max_restarts=5, base_delay_sec=1.0, backoff_factor=2.0, max_delay_sec=60.0, keep_logs=10, clean_exit_codes=(0,)) |

## `get_git_hash(repo_dir) -> 'str'`

Best-effort short git hash; 'unknown' outside a repo.

| Parameter | Type | Default |
|---|---|---|
| `repo_dir` | — | — |
