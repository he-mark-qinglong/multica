# `_shared.bench_backtest`

Source: `_shared/bench_backtest.py`

Backtest performance benchmark (B16) — target: >100K bars/s.

## class `BenchConfig(n_bars: 'int' = 1000000, n_trades: 'int' = 2000, seed: 'int' = 7, repeat: 'int' = 3) -> None`

Benchmark dimensions.

## `benchmark(config: 'BenchConfig' = BenchConfig(n_bars=1000000, n_trades=2000, seed=7, repeat=3)) -> 'Dict[str, Any]'`

Time both engines; profile the authoritative one.

| Parameter | Type | Default |
|---|---|---|
| `config` | 'BenchConfig' | BenchConfig(n_bars=1000000, n_trades=2000, seed=7, repeat=3) |

## `format_report(result: 'Dict[str, Any]') -> 'str'`

Human-readable benchmark report.

| Parameter | Type | Default |
|---|---|---|
| `result` | 'Dict[str, Any]' | — |

## `synthetic_bars(n: 'int', seed: 'int' = 7) -> 'pd.DataFrame'`

Deterministic synthetic 1h close series of length ``n``.

| Parameter | Type | Default |
|---|---|---|
| `n` | 'int' | — |
| `seed` | 'int' | 7 |
