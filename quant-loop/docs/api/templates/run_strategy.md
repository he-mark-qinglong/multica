# `_shared.templates.run_strategy`

Source: `_shared/templates/run_strategy.py`

Generic strategy runner — contract v2 -> backtest -> 9-key metrics.

## `estimate_trade_pnls(trades: 'List[Trade]', bars: 'pd.DataFrame', cost_bps_rt: 'float') -> 'List[float]'`

Approximate per-trade net pnl fractions (for per-trade win_rate).

| Parameter | Type | Default |
|---|---|---|
| `trades` | 'List[Trade]' | — |
| `bars` | 'pd.DataFrame' | — |
| `cost_bps_rt` | 'float' | — |

## `infer_freq_per_year(index: 'pd.Index') -> 'int'`

Annualisation factor from the median bar spacing (365-day year).

| Parameter | Type | Default |
|---|---|---|
| `index` | 'pd.Index' | — |

## `load_bars_dir(directory: 'str | Path', symbols: 'List[str]') -> 'Dict[str, pd.DataFrame]'`

Load ``{SYMBOL}.csv`` / ``{SYMBOL}.parquet`` for each symbol.

| Parameter | Type | Default |
|---|---|---|
| `directory` | 'str | Path' | — |
| `symbols` | 'List[str]' | — |

## `load_bars_file(path: 'str | Path') -> 'pd.DataFrame'`

Load one OHLCV frame from CSV or parquet; index = UTC timestamps.

| Parameter | Type | Default |
|---|---|---|
| `path` | 'str | Path' | — |

## `load_strategy_module(path: 'str | Path') -> 'ModuleType'`

Import a strategy module from a filesystem path.

| Parameter | Type | Default |
|---|---|---|
| `path` | 'str | Path' | — |

## `main(argv: 'List[str] | None' = None) -> 'int'`

| Parameter | Type | Default |
|---|---|---|
| `argv` | 'List[str] | None' | None |

## `run_strategy(strategy_path: 'str | Path', config: 'Mapping[str, Any] | None' = None, *, bars: 'Dict[str, pd.DataFrame] | None' = None, bars_dir: 'str | Path | None' = None, initial_capital: 'float' = 100000.0, cost_bps_rt: 'float' = 22.0, cost_mode: 'str' = 'fill', freq_per_year: 'int | None' = None) -> 'Dict[str, Any]'`

Run a contract-v2 strategy end-to-end.

| Parameter | Type | Default |
|---|---|---|
| `strategy_path` | 'str | Path' | — |
| `config` | 'Mapping[str, Any] | None' | None |
| `bars` | 'Dict[str, pd.DataFrame] | None' | None |
| `bars_dir` | 'str | Path | None' | None |
| `initial_capital` | 'float' | 100000.0 |
| `cost_bps_rt` | 'float' | 22.0 |
| `cost_mode` | 'str' | 'fill' |
| `freq_per_year` | 'int | None' | None |
