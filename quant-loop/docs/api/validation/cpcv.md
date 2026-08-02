# `_shared.validation.cpcv`

Source: `_shared/validation/cpcv.py`

Combinatorial Purged Cross-Validation (CPCV) harness.

## class `CPCVResult(n_groups: int, k_test: int, n_paths: int, folds: list[_shared.validation.cpcv.FoldResult] = <factory>) -> None`

CPCVResult(n_groups: int, k_test: int, n_paths: int, folds: list[_shared.validation.cpcv.FoldResult] = <factory>)

## class `FoldResult(train_start: pandas.Timestamp, train_end: pandas.Timestamp, test_start: pandas.Timestamp, test_end: pandas.Timestamp, oos_sharpe: float, oos_returns: numpy.ndarray, n_trades: int) -> None`

FoldResult(train_start: pandas.Timestamp, train_end: pandas.Timestamp, test_start: pandas.Timestamp, test_end: pandas.Timestamp, oos_sharpe: float, oos_returns: numpy.ndarray, n_trades: int)

## `cpcv(data: pandas.DataFrame, strategy_fn, n_groups: int = 6, k_test: int = 2, purge_bars: int = 100, embargo_bars: int = 50, periods_per_year: int = 365) -> _shared.validation.cpcv.CPCVResult`

Run CPCV on a strategy.

| Parameter | Type | Default |
|---|---|---|
| `data` | pandas.DataFrame | — |
| `strategy_fn` | — | — |
| `n_groups` | int | 6 |
| `k_test` | int | 2 |
| `purge_bars` | int | 100 |
| `embargo_bars` | int | 50 |
| `periods_per_year` | int | 365 |

## `deflated_sharpe(observed_sharpe: float, n_trials: int, sample_len: int, skew: float = 0.0, kurt: float = 3.0) -> float`

Deflated Sharpe Ratio per Bailey & López de Prado (2014).

| Parameter | Type | Default |
|---|---|---|
| `observed_sharpe` | float | — |
| `n_trials` | int | — |
| `sample_len` | int | — |
| `skew` | float | 0.0 |
| `kurt` | float | 3.0 |

## `sharpe_from_returns(returns: numpy.ndarray, periods_per_year: int = 365) -> float`

Annualized Sharpe from per-period returns.

| Parameter | Type | Default |
|---|---|---|
| `returns` | numpy.ndarray | — |
| `periods_per_year` | int | 365 |
