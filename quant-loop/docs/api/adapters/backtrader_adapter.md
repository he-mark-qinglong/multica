# `_shared.adapters.backtrader_adapter`

Source: `_shared/adapters/backtrader_adapter.py`

backtrader framework adapter — SMA-35409 / MAP-P5 #042.

## class `BacktraderMetrics(engine: 'str', engine_version: 'str', sharpe: 'float', total_return: 'float', annualised_pct: 'float', max_dd: 'float', n_bars: 'int', n_trades: 'int', n_skipped: 'int', used_shim: 'bool') -> None`

Metrics envelope returned by :func:`run_backtrader_backtest`.

### `as_dict(self) -> 'Dict[str, Any]'`

## `import_error() -> 'Optional[BaseException]'`

Return the most recent import error if backtrader is unavailable, else None.

## `is_available() -> 'bool'`

True iff backtrader is importable in this Python environment.

## `run_backtrader_backtest(bars: 'pd.DataFrame', trades: 'Optional[List[Any]]' = None, *, strategy: 'str' = 'sma_cross', commission: 'float' = 0.0002, initial_capital: 'float' = 100000.0, sma_fast: 'int' = 10, sma_slow: 'int' = 30, freq_per_year: 'int' = 8760, size_fraction: 'float' = 1.0, force_shim: 'bool' = False) -> 'Tuple[pd.Series, BacktraderMetrics]'`

Cross-validation entry point — backtrader-compatible broker replay.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `trades` | 'Optional[List[Any]]' | None |
| `strategy` | 'str' | 'sma_cross' |
| `commission` | 'float' | 0.0002 |
| `initial_capital` | 'float' | 100000.0 |
| `sma_fast` | 'int' | 10 |
| `sma_slow` | 'int' | 30 |
| `freq_per_year` | 'int' | 8760 |
| `size_fraction` | 'float' | 1.0 |
| `force_shim` | 'bool' | False |

## `to_framework_cv(metrics: 'BacktraderMetrics') -> 'Dict[str, Any]'`

Return a dict shaped like ``framework_cv["framework"]`` for the validator.

| Parameter | Type | Default |
|---|---|---|
| `metrics` | 'BacktraderMetrics' | — |
