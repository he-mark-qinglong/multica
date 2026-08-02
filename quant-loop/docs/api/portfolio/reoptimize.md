# `_shared.portfolio.reoptimize`

Source: `_shared/portfolio/reoptimize.py`

Portfolio re-optimization scheduler (I18).

## class `ReoptRecord(ts: 'str', trigger: 'str', cov_summary: 'Mapping[str, float]', weight_diff: 'Mapping[str, float]', applied: 'bool', skip_reason: 'str', weights: 'Mapping[str, float]') -> None`

Audit record of one fired re-optimization trigger.

## class `ReoptimizeConfig(every_n_bars: 'int | None' = None, daily: 'bool' = False, cron_times: 'Tuple[str, ...]' = (), weight_change_threshold: 'float' = 0.01, erc_params: 'DynamicERCParams' = <factory>) -> None`

Schedule + debounce configuration.

## class `Reoptimizer(config: 'ReoptimizeConfig', audit_path: 'str | Path | None' = None, initial_weights: 'Optional[Mapping[str, float]]' = None)`

Scheduled, debounced dynamic-ERC weight updater.

### `on_bar(self, bar_index: 'int', timestamp: 'pd.Timestamp', returns: 'pd.DataFrame') -> 'ReoptRecord | None'`

Feed one bar; returns a record only when a schedule fired.

| Parameter | Type | Default |
|---|---|---|
| `bar_index` | 'int' | — |
| `timestamp` | 'pd.Timestamp' | — |
| `returns` | 'pd.DataFrame' | — |

### `trigger_manual(self, timestamp: 'pd.Timestamp', returns: 'pd.DataFrame') -> 'ReoptRecord'`

Operator-forced recompute, bypassing the schedule.

| Parameter | Type | Default |
|---|---|---|
| `timestamp` | 'pd.Timestamp' | — |
| `returns` | 'pd.DataFrame' | — |
