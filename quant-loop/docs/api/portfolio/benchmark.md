# `_shared.portfolio.benchmark`

Source: `_shared/portfolio/benchmark.py`

Benchmark construction and comparison (I17).

## class `BenchmarkComparison(n_periods: 'int', alpha: 'float', beta: 'float', correlation: 'float', tracking_error: 'float', information_ratio: 'float', up_capture: 'float', down_capture: 'float', active_return: 'float') -> None`

Strategy-vs-benchmark statistics on aligned return series.

## `buy_and_hold(close: 'pd.Series') -> 'pd.Series'`

Buy & hold equity curve from a close series, normalized to 1.0.

| Parameter | Type | Default |
|---|---|---|
| `close` | 'pd.Series' | — |

## `compare_to_benchmark(strategy_returns: 'pd.Series', benchmark_returns: 'pd.Series', periods_per_year: 'int' = 365) -> 'BenchmarkComparison'`

Compare a strategy return stream against a benchmark stream.

| Parameter | Type | Default |
|---|---|---|
| `strategy_returns` | 'pd.Series' | — |
| `benchmark_returns` | 'pd.Series' | — |
| `periods_per_year` | 'int' | 365 |

## `equal_weight(prices: 'pd.DataFrame') -> 'pd.Series'`

Equal-weight, per-bar-rebalanced basket equity, normalized to 1.0.

| Parameter | Type | Default |
|---|---|---|
| `prices` | 'pd.DataFrame' | — |
