# `_shared.market_making.backtest_live_parity`

Source: `_shared/market_making/backtest_live_parity.py`

Backtest ↔ paper-path parity validator (B19).

## class `Fill(ts: 'pd.Timestamp', side: 'str', price: 'float', reason: 'str') -> None`

One execution record produced by either path.

## class `FillMismatch(index: 'int', backtest_fill: 'Fill | None', paper_fill: 'Fill | None', price_diff_bp: 'float | None', time_diff_bars: 'float | None') -> None`

One divergent (or unpaired) fill.

## class `ParityParams(initial_capital: 'float' = 100000.0, cost_bps_rt: 'float' = 24.0, size_fraction: 'float' = 1.0, freq_per_year: 'int' = 8760, price_tol_bp: 'float' = 1.0, time_tol_bars: 'float' = 1.0) -> None`

Tolerances and shared economics for both paths.

## class `ParityReport(ok: 'bool', n_backtest_fills: 'int', n_paper_fills: 'int', n_mismatches: 'int', mismatches: 'tuple[FillMismatch, ...]', max_price_diff_bp: 'float', max_time_diff_bars: 'float', equity_final_diff_pct: 'float', metrics_backtest: 'Mapping[str, float]', metrics_paper: 'Mapping[str, float]') -> None`

Parity verdict + diagnostics.

## class `PathResult(fills: 'tuple[Fill, ...]', equity: 'pd.Series', metrics: 'Mapping[str, float]') -> None`

Output of one driver path.

## `compare_fills(backtest_fills: 'Sequence[Fill]', paper_fills: 'Sequence[Fill]', bar_seconds: 'float', params: 'ParityParams') -> 'tuple[tuple[FillMismatch, ...], float, float]'`

Pair fills by sequence position; return (mismatches, max Δbp, max Δbars).

| Parameter | Type | Default |
|---|---|---|
| `backtest_fills` | 'Sequence[Fill]' | — |
| `paper_fills` | 'Sequence[Fill]' | — |
| `bar_seconds` | 'float' | — |
| `params` | 'ParityParams' | — |

## `infer_bar_seconds(bars: 'pd.DataFrame') -> 'float'`

Median bar spacing in seconds.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |

## `run_backtest_path(bars: 'pd.DataFrame', strategy: 'StrategyFn', params: 'ParityParams') -> 'PathResult'`

Batch path: strategy → Trade schedule → ``run_backtest``.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `strategy` | 'StrategyFn' | — |
| `params` | 'ParityParams' | — |

## `run_paper_path(bars: 'pd.DataFrame', strategy: 'StrategyFn', params: 'ParityParams') -> 'PathResult'`

Online path: bar-by-bar event loop with incremental compounding.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `strategy` | 'StrategyFn' | — |
| `params` | 'ParityParams' | — |

## `validate_parity(bars: 'pd.DataFrame', strategy: 'StrategyFn', params: 'ParityParams | None' = None) -> 'ParityReport'`

Run both paths over ``bars`` and compare the fill sequences.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'pd.DataFrame' | — |
| `strategy` | 'StrategyFn' | — |
| `params` | 'ParityParams | None' | None |
