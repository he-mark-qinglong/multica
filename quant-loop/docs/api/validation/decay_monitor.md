# `_shared.validation.decay_monitor`

Source: `_shared/validation/decay_monitor.py`

Signal decay monitoring (G20).

## class `DecayReport(status: 'str', half_life_years: 'float | None', ic_slope_per_year: 'float', recent_ic: 'float', early_ic: 'float', recent_sharpe: 'float', rolling_ic: 'pd.Series', rolling_sharpe: 'pd.Series', diagnostics: 'dict[str, float]' = <factory>) -> None`

Frozen summary of a signal-decay diagnostic run.

## `half_life_years(rolling: 'pd.Series') -> 'float | None'`

Half-life (years) from a log-linear fit on positive rolling IC.

| Parameter | Type | Default |
|---|---|---|
| `rolling` | 'pd.Series' | — |

## `ic_slope_per_year(rolling: 'pd.Series') -> 'float'`

OLS slope of a rolling-IC series against time, per year. Pure.

| Parameter | Type | Default |
|---|---|---|
| `rolling` | 'pd.Series' | — |

## `monitor_decay(signal: 'pd.Series', forward_returns: 'pd.Series', strategy_returns: 'pd.Series | None' = None, window: 'int' = 60, recent: 'int' = 10, ic_dead: 'float' = 0.0, slope_eps: 'float' = 0.0001, decay_fraction: 'float' = 0.5, periods_per_year: 'float' = 365.0) -> 'DecayReport'`

Run the full decay diagnostic and classify the signal.

| Parameter | Type | Default |
|---|---|---|
| `signal` | 'pd.Series' | — |
| `forward_returns` | 'pd.Series' | — |
| `strategy_returns` | 'pd.Series | None' | None |
| `window` | 'int' | 60 |
| `recent` | 'int' | 10 |
| `ic_dead` | 'float' | 0.0 |
| `slope_eps` | 'float' | 0.0001 |
| `decay_fraction` | 'float' | 0.5 |
| `periods_per_year` | 'float' | 365.0 |

## `rolling_ic(signal: 'pd.Series', forward_returns: 'pd.Series', window: 'int') -> 'pd.Series'`

Rolling Spearman rank IC between signal and forward returns. Pure.

| Parameter | Type | Default |
|---|---|---|
| `signal` | 'pd.Series' | — |
| `forward_returns` | 'pd.Series' | — |
| `window` | 'int' | — |

## `rolling_sharpe(returns: 'pd.Series', window: 'int', periods_per_year: 'float' = 365.0) -> 'pd.Series'`

Rolling annualised Sharpe of a return stream. Pure.

| Parameter | Type | Default |
|---|---|---|
| `returns` | 'pd.Series' | — |
| `window` | 'int' | — |
| `periods_per_year` | 'float' | 365.0 |
