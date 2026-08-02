# `_shared.validation.compute_metrics`

Source: `_shared/validation/compute_metrics.py`

Shared helper to compute the 9-key metrics dict expected by metrics_validator.

## `compute_metrics(equity: 'pd.Series', n_trades: 'int', freq_per_year: 'int' = 365, trade_pnls: 'Sequence[float] | None' = None) -> 'dict[str, Any]'`

Compute sharpe/ann_ret/max_dd/pf/n_trades/n_bars/win_rate/calmar/sortino.

| Parameter | Type | Default |
|---|---|---|
| `equity` | 'pd.Series' | — |
| `n_trades` | 'int' | — |
| `freq_per_year` | 'int' | 365 |
| `trade_pnls` | 'Sequence[float] | None' | None |
