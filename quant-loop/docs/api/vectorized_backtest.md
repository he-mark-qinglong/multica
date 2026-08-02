# `_shared.vectorized_backtest`

Source: `_shared/vectorized_backtest.py`

Fully vectorised signal-driven backtest engine (B2).

## class `VectorizedBacktestConfig(initial_capital: 'float' = 100000.0, cost_bps_rt: 'float' = 24.0, freq_per_year: 'int' = 8760) -> None`

Configuration for :func:`run_vectorized_backtest`.

## `run_vectorized_backtest(close: 'np.ndarray', signals: 'np.ndarray', size_fraction: 'float | np.ndarray' = 1.0, *, index: 'Optional[pd.DatetimeIndex]' = None, config: 'VectorizedBacktestConfig' = VectorizedBacktestConfig(initial_capital=100000.0, cost_bps_rt=24.0, freq_per_year=8760)) -> 'Dict[str, Any]'`

Vectorised equity walk for a -1/0/+1 signal array.

| Parameter | Type | Default |
|---|---|---|
| `close` | 'np.ndarray' | — |
| `signals` | 'np.ndarray' | — |
| `size_fraction` | 'float | np.ndarray' | 1.0 |
| `index` | 'Optional[pd.DatetimeIndex]' | None |
| `config` | 'VectorizedBacktestConfig' | VectorizedBacktestConfig(initial_capital=100000.0, cost_bps_rt=24.0, freq_per_year=8760) |

## `signals_to_trades(index: 'pd.DatetimeIndex', signals: 'np.ndarray', size_fraction: 'float | np.ndarray' = 1.0) -> 'List[Trade]'`

Convert a signal array into the equivalent non-overlapping Trade schedule.

| Parameter | Type | Default |
|---|---|---|
| `index` | 'pd.DatetimeIndex' | — |
| `signals` | 'np.ndarray' | — |
| `size_fraction` | 'float | np.ndarray' | 1.0 |
