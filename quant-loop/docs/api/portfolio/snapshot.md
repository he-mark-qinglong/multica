# `_shared.portfolio.snapshot`

Source: `_shared/portfolio/snapshot.py`

Portfolio state snapshots persisted to parquet (I19).

## class `PortfolioSnapshot(ts: 'pd.Timestamp', equity: 'float', cash: 'float', positions: 'Dict[str, float]', prices: 'Dict[str, float]', risk_metrics: 'Dict[str, float]') -> None`

Immutable book state at one timestamp.

## class `SnapshotDiff(ts_a: 'pd.Timestamp', ts_b: 'pd.Timestamp', equity_delta: 'float', cash_delta: 'float', positions_opened: 'Dict[str, float]', positions_closed: 'Dict[str, float]', positions_changed: 'Dict[str, tuple]', metric_deltas: 'Dict[str, float]') -> None`

Difference between two snapshots (``b`` minus ``a``).

## `diff_snapshots(a: 'PortfolioSnapshot', b: 'PortfolioSnapshot') -> 'SnapshotDiff'`

Position / equity / metric changes from ``a`` to ``b``.

| Parameter | Type | Default |
|---|---|---|
| `a` | 'PortfolioSnapshot' | — |
| `b` | 'PortfolioSnapshot' | — |

## `load_snapshots(dir_path: 'str | Path') -> 'List[PortfolioSnapshot]'`

Load all snapshots, ordered by ts.

| Parameter | Type | Default |
|---|---|---|
| `dir_path` | 'str | Path' | — |

## `save_snapshot(snap: 'PortfolioSnapshot', dir_path: 'str | Path') -> 'Path'`

Append ``snap`` to the snapshot store at ``dir_path`` (created if missing). Rewrites the two parquet files; snapshot volumes in this project are small enough that read-modify-write is the simple correct choice. Returns the directory path.

| Parameter | Type | Default |
|---|---|---|
| `snap` | 'PortfolioSnapshot' | — |
| `dir_path` | 'str | Path' | — |

## `snapshot_at(dir_path: 'str | Path', ts: 'pd.Timestamp') -> 'Optional[PortfolioSnapshot]'`

Point-in-time recovery: latest snapshot with ``ts_snap <= ts``.

| Parameter | Type | Default |
|---|---|---|
| `dir_path` | 'str | Path' | — |
| `ts` | 'pd.Timestamp' | — |
