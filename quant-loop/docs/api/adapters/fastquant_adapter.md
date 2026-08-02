# `_shared.adapters.fastquant_adapter`

Source: `_shared/adapters/fastquant_adapter.py`

fastquant framework adapter — MAP-P5 / SMA-35404.

## class `FastquantMetrics(engine: 'str', engine_version: 'str', sharpe: 'float', total_return: 'float', annualised_pct: 'float', max_dd: 'float', n_bars: 'int', n_trades: 'int', n_skipped: 'int', used_shim: 'bool') -> None`

Metrics envelope returned by :func:`run_fastquant_backtest`.

### `as_dict(self) -> 'Dict[str, Any]'`

## `run_fastquant_backtest(bars: 'pd.DataFrame', trades: 'Optional[List[Any]]' = None, *, strategy: 'str' = 'smac', commission: 'float' = 0.001, initial_capital: 'float' = 100000.0, fast_period: 'int' = 10, slow_period: 'int' = 30, freq_per_year: 'int' = 8760, size_fraction: 'float' = 1.0, force_shim: 'bool' = False) -> 'Tuple[pd.Series, FastquantMetrics]'`

Cross-validation entry point — fastquant-compatible broker replay.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `trades` | 'Optional[List[Any]]' | None |
| `strategy` | 'str' | 'smac' |
| `commission` | 'float' | 0.001 |
| `initial_capital` | 'float' | 100000.0 |
| `fast_period` | 'int' | 10 |
| `slow_period` | 'int' | 30 |
| `freq_per_year` | 'int' | 8760 |
| `size_fraction` | 'float' | 1.0 |
| `force_shim` | 'bool' | False |

## `to_framework_cv(metrics: 'FastquantMetrics') -> 'Dict[str, Any]'`

Return a dict shaped like ``framework_cv["framework"]`` for the validator.

| Parameter | Type | Default |
|---|---|---|
| `metrics` | 'FastquantMetrics' | — |
