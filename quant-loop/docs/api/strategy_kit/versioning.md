# `_shared.strategy_kit.versioning`

Source: `_shared/strategy_kit/versioning.py`

Strategy version management (metric A12).

## class `StrategyVersion(strategy_name: 'str', version_id: 'str', path: 'str', config_hash: 'str', code_hash: 'str', code_files: 'Mapping[str, str]' = <factory>, config: 'Mapping' = <factory>, parent: 'Optional[str]' = None, created_at: 'str' = '') -> None`

One immutable strategy-directory snapshot.

## class `VersionDiff(config_changed: 'Mapping[str, Tuple]' = <factory>, config_added: 'Mapping[str, object]' = <factory>, config_removed: 'Mapping[str, object]' = <factory>, code_added: 'Tuple[str, ...]' = (), code_removed: 'Tuple[str, ...]' = (), code_changed: 'Tuple[str, ...]' = ()) -> None`

Difference between two versions.

## class `VersioningError`

Unknown version / strategy, or malformed store.

## `checkout(store_path: 'Path | str', strategy_name: 'str', version_id: 'str') -> 'StrategyVersion'`

Move the current pointer to ``version_id`` (logical checkout — no files are rewritten). Returns the now-current version.

| Parameter | Type | Default |
|---|---|---|
| `store_path` | 'Path | str' | — |
| `strategy_name` | 'str' | — |
| `version_id` | 'str' | — |

## `current(store_path: 'Path | str', strategy_name: 'str') -> 'StrategyVersion'`

The strategy's checked-out (most recently registered) version.

| Parameter | Type | Default |
|---|---|---|
| `store_path` | 'Path | str' | — |
| `strategy_name` | 'str' | — |

## `diff_versions(store_path: 'Path | str', strategy_name: 'str', version_a: 'str', version_b: 'str') -> 'VersionDiff'`

Config + code file-list diff between two recorded versions.

| Parameter | Type | Default |
|---|---|---|
| `store_path` | 'Path | str' | — |
| `strategy_name` | 'str' | — |
| `version_a` | 'str' | — |
| `version_b` | 'str' | — |

## `hash_code(strategy_dir: 'Path') -> 'Tuple[str, Dict[str, str]]'`

Hash every ``*.py`` file under ``strategy_dir`` (non-recursive into ``__pycache__`` / results / data). Returns (combined_hash, per-file hashes keyed by path relative to the directory).

| Parameter | Type | Default |
|---|---|---|
| `strategy_dir` | 'Path' | — |

## `hash_config(config_path: 'Path') -> 'Tuple[str, Mapping]'`

Canonical sha256 of a config.json + its parsed content.

| Parameter | Type | Default |
|---|---|---|
| `config_path` | 'Path' | — |

## `lineage(store_path: 'Path | str', strategy_name: 'str', version_id: 'Optional[str]' = None) -> 'List[StrategyVersion]'`

Parent chain from ``version_id`` (default: current) back to the root, newest first. Cycle-safe.

| Parameter | Type | Default |
|---|---|---|
| `store_path` | 'Path | str' | — |
| `strategy_name` | 'str' | — |
| `version_id` | 'Optional[str]' | None |

## `list_versions(store_path: 'Path | str', strategy_name: 'Optional[str]' = None) -> 'List[StrategyVersion]'`

All recorded versions (oldest first), optionally for one strategy.

| Parameter | Type | Default |
|---|---|---|
| `store_path` | 'Path | str' | — |
| `strategy_name` | 'Optional[str]' | None |

## `register_version(strategy_dir: 'Path | str', store_path: 'Path | str', parent: 'Optional[str]' = None, created_at: 'Optional[str]' = None) -> 'StrategyVersion'`

Snapshot ``strategy_dir`` into the version store.

| Parameter | Type | Default |
|---|---|---|
| `strategy_dir` | 'Path | str' | — |
| `store_path` | 'Path | str' | — |
| `parent` | 'Optional[str]' | None |
| `created_at` | 'Optional[str]' | None |
