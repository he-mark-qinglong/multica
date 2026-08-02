# `_shared.ops.config_hot`

Source: `_shared/ops/config_hot.py`

Ops-layer config hot-reload with audit log and rollback (H8).

## class `AuditEntry(ts: 'float', source: 'str', applied: 'bool', diff: 'Mapping[str, Mapping[str, Any]]' = <factory>, error: 'Optional[str]' = None) -> None`

One audit-log record: every reload attempt, applied or not.

### `to_json(self) -> 'str'`

## class `ConfigVersion(ts: 'float', source: 'str', config: 'ConfigDict' = <factory>) -> None`

One applied config version (the rollback target unit).

## class `OpsConfigReloader(path, audit_path, on_reload: 'OnReload', validator: 'Optional[Validator]' = <function validate_ops_config at 0x10b301440>) -> 'None'`

ConfigReloader + audit log + versioned rollback.

### `check_once(self, source: 'str' = 'file') -> 'ReloadEvent'`

Check the file once; audit the attempt, keep history on success.

| Parameter | Type | Default |
|---|---|---|
| `source` | 'str' | 'file' |

### `load_initial(self, source: 'str' = 'initial') -> 'ConfigDict'`

Load + validate the initial config; audited; raises when bad.

| Parameter | Type | Default |
|---|---|---|
| `source` | 'str' | 'initial' |

### `read_audit_log(self) -> 'Tuple[AuditEntry, ...]'`

Parse the JSONL audit log back into AuditEntry records.

### `rollback_to(self, index: 'Optional[int]' = None, ts: 'Optional[float]' = None) -> 'ReloadEvent'`

Restore a historical version by history index or exact timestamp.

| Parameter | Type | Default |
|---|---|---|
| `index` | 'Optional[int]' | None |
| `ts` | 'Optional[float]' | None |

## `diff_configs(old: 'Mapping[str, Any]', new: 'Mapping[str, Any]', prefix: 'str' = '') -> 'Dict[str, Dict[str, Any]]'`

Flat dotted-path diff of two configs: path -> {"old", "new"}. Pure.

| Parameter | Type | Default |
|---|---|---|
| `old` | 'Mapping[str, Any]' | — |
| `new` | 'Mapping[str, Any]' | — |
| `prefix` | 'str' | '' |

## `validate_ops_config(cfg: 'ConfigDict') -> 'None'`

Default validator for ops configs. Raises ValueError on bad input.

| Parameter | Type | Default |
|---|---|---|
| `cfg` | 'ConfigDict' | — |
