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

## `expected_max_sharpe_z(n_trials: int) -> float`

Standardized expected maximum of `n_trials` iid N(0, 1) Sharpe estimates,
per the Bailey & López de Prado (2014) extreme-value approximation:
`(1-γ)Φ⁻¹(1-1/N) + γΦ⁻¹(1-1/(N·e))`. Numerically identical to purgedcv's
reference (`_expected_max_z`).

## `deflated_sharpe(observed_sharpe: float, n_trials: int, sample_len: int, skew: float = 0.0, kurt: float = 3.0, trial_sharpe_var: float | None = None) -> float`

Deflated Sharpe value per Bailey & López de Prado (2014): returns
`observed_sharpe - SR*` where `SR* = sqrt(V̂[{SRₙ}]) · expected_max_sharpe_z(n_trials)`.
The edge survives multiple testing iff the returned value is > 0 (equivalent
to the DSR probability being > 0.5).

| Parameter | Type | Default |
|---|---|---|
| `observed_sharpe` | float | — |
| `n_trials` | int | — |
| `sample_len` | int | — |
| `skew` | float | 0.0 |
| `kurt` | float | 3.0 |
| `trial_sharpe_var` | float | None | None |

`trial_sharpe_var` is the spec's V̂[{SRₙ}] — variance of Sharpe estimates
across the `n_trials` candidates. When omitted, falls back to the Lo (2002)
variance of the single Sharpe estimator (documented in the docstring).

## `sharpe_from_returns(returns: numpy.ndarray, periods_per_year: int = 365) -> float`

Annualized Sharpe from per-period returns.

| Parameter | Type | Default |
|---|---|---|
| `returns` | numpy.ndarray | — |
| `periods_per_year` | int | 365 |
