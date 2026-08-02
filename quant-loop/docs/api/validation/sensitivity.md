# `_shared.validation.sensitivity`

Source: `_shared/validation/sensitivity.py`

Parameter sensitivity analysis for strategies (G18).

## class `ParamSensitivity(param: 'str', metric: 'str', base_value: 'float', base_metric: 'float', elasticity: 'float', is_cliff: 'bool', metric_at_moves: 'Mapping[float, tuple[float, float]]' = <factory>) -> None`

Sensitivity of one metric to one parameter.

## class `SensitivityReport(base_params: 'Mapping[str, float]', base_metrics: 'Mapping[str, float]', pct_moves: 'tuple[float, ...]', sensitivities: 'tuple[ParamSensitivity, ...]') -> None`

Full OAT sweep result, ranked by |elasticity| descending.

## `compute_sensitivity(strategy: 'Callable[[Mapping[str, float], object], Mapping[str, float]]', base_params: 'Mapping[str, float]', data: 'object' = None, pct_moves: 'Sequence[float]' = (0.1, 0.25), metrics: 'Sequence[str]' = ('sharpe', 'pnl')) -> 'SensitivityReport'`

Run the OAT sensitivity sweep. Pure apart from ``strategy`` itself.

| Parameter | Type | Default |
|---|---|---|
| `strategy` | 'Callable[[Mapping[str, float], object], Mapping[str, float]]' | — |
| `base_params` | 'Mapping[str, float]' | — |
| `data` | 'object' | None |
| `pct_moves` | 'Sequence[float]' | (0.1, 0.25) |
| `metrics` | 'Sequence[str]' | ('sharpe', 'pnl') |

## `sensitivity_table(report: 'SensitivityReport') -> 'str'`

Render the sensitivity ranking as a plain-text table.

| Parameter | Type | Default |
|---|---|---|
| `report` | 'SensitivityReport' | — |
