# `_shared.paper.runner`

Source: `_shared/paper/runner.py`

Paper-trading runner skeleton — config-driven, idempotent, kill-aware.

## class `ConfigError`

Raised when a required config key is missing or out of range.

## `evaluate_kill(day_row: 'Dict[str, Any]', cfg: 'dict', state: 'Dict[str, Any]') -> 'Dict[str, Any]'`

Apply the three hard kill rules. Latches: already-killed state is sticky.

| Parameter | Type | Default |
|---|---|---|
| `day_row` | 'Dict[str, Any]' | — |
| `cfg` | 'dict' | — |
| `state` | 'Dict[str, Any]' | — |

## `load_config(path: 'Path') -> 'dict'`

Read JSON config; raise ConfigError with the missing key name(s).

| Parameter | Type | Default |
|---|---|---|
| `path` | 'Path' | — |

## `load_state(run_dir: 'Path') -> 'Dict[str, Any]'`

Read ``state.json``; return defaults if absent.

| Parameter | Type | Default |
|---|---|---|
| `run_dir` | 'Path' | — |

## `run(cfg_path: 'Path', bars_csv: 'Path', trades_csv: 'Path', run_dir: 'Path') -> 'int'`

Drive one offline paper run. Returns 0 (clean) or 2 (kill triggered).

| Parameter | Type | Default |
|---|---|---|
| `cfg_path` | 'Path' | — |
| `bars_csv` | 'Path' | — |
| `trades_csv` | 'Path' | — |
| `run_dir` | 'Path' | — |

## `save_state(run_dir: 'Path', state: 'Dict[str, Any]') -> 'None'`

Atomic write of state.json via tmp-file + os.replace.

| Parameter | Type | Default |
|---|---|---|
| `run_dir` | 'Path' | — |
| `state` | 'Dict[str, Any]' | — |
