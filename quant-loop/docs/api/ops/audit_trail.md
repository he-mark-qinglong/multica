# `_shared.ops.audit_trail`

Source: `_shared/ops/audit_trail.py`

Runtime audit trail (H20).

## class `AuditRecord(ts: 'float', kind: 'str', actor: 'str', strategy: 'str' = '', before: 'Mapping[str, Any]' = <factory>, after: 'Mapping[str, Any]' = <factory>, note: 'str' = '') -> None`

One immutable state-transition record.

### `to_json(self) -> 'str'`

## class `TransitionKind(*values)`

Auditable state transitions.

## `append_record(path, record: 'AuditRecord') -> 'AuditRecord'`

Append one record as a JSON line; returns the record written.

| Parameter | Type | Default |
|---|---|---|
| `path` | — | — |
| `record` | 'AuditRecord' | — |

## `diff_record(record: 'AuditRecord') -> 'Dict[str, Tuple[Any, Any]]'`

The before->after delta of one record. Pure.

| Parameter | Type | Default |
|---|---|---|
| `record` | 'AuditRecord' | — |

## `diff_summary(before: 'Mapping[str, Any]', after: 'Mapping[str, Any]') -> 'Dict[str, Tuple[Any, Any]]'`

Key-level delta between two state summaries. Pure.

| Parameter | Type | Default |
|---|---|---|
| `before` | 'Mapping[str, Any]' | — |
| `after` | 'Mapping[str, Any]' | — |

## `load_trail(path) -> 'Tuple[AuditRecord, ...]'`

Load the whole trail; empty tuple if absent. Corrupt lines skipped.

| Parameter | Type | Default |
|---|---|---|
| `path` | — | — |

## `query_trail(records: 'Sequence[AuditRecord]', kind: 'Optional[str]' = None, actor: 'Optional[str]' = None, strategy: 'Optional[str]' = None, start_ts: 'Optional[float]' = None, end_ts: 'Optional[float]' = None) -> 'Tuple[AuditRecord, ...]'`

Filter the trail by kind / actor / strategy / time window. Pure.

| Parameter | Type | Default |
|---|---|---|
| `records` | 'Sequence[AuditRecord]' | — |
| `kind` | 'Optional[str]' | None |
| `actor` | 'Optional[str]' | None |
| `strategy` | 'Optional[str]' | None |
| `start_ts` | 'Optional[float]' | None |
| `end_ts` | 'Optional[float]' | None |

## `tail(records: 'Sequence[AuditRecord]', n: 'int' = 10) -> 'Tuple[AuditRecord, ...]'`

The newest ``n`` records, oldest-first. Pure.

| Parameter | Type | Default |
|---|---|---|
| `records` | 'Sequence[AuditRecord]' | — |
| `n` | 'int' | 10 |
