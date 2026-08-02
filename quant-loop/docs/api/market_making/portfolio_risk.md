# `_shared.market_making.portfolio_risk`

Source: `_shared/market_making/portfolio_risk.py`

Portfolio-level risk: correlation matrix and ERC allocation.

## class `CorrelationResult(correlation_matrix: 'pd.DataFrame', mean_correlation: 'float', max_correlation: 'float', max_pair: 'tuple[str, str]', diversification_ratio: 'float') -> None`

Pairwise correlation analysis across strategy/asset return series.

## class `ERCResult(weights: 'dict[str, float]', risk_contributions: 'dict[str, float]', portfolio_vol: 'float', n_assets: 'int') -> None`

Equal Risk Contribution allocation output.

## `compute_correlation(returns: 'pd.DataFrame') -> 'CorrelationResult'`

Compute pairwise correlation and diversification metrics.

| Parameter | Type | Default |
|---|---|---|
| `returns` | 'pd.DataFrame' | — |

## `erc_weights(cov_matrix: 'pd.DataFrame', max_iter: 'int' = 1000, tol: 'float' = 1e-08) -> 'ERCResult'`

Compute Equal Risk Contribution weights.

| Parameter | Type | Default |
|---|---|---|
| `cov_matrix` | 'pd.DataFrame' | — |
| `max_iter` | 'int' | 1000 |
| `tol` | 'float' | 1e-08 |

## `portfolio_cvar(strategy_returns: 'pd.DataFrame', weights: 'dict[str, float]', confidence: 'float' = 0.95) -> 'float'`

Aggregate CVaR for a weighted portfolio.

| Parameter | Type | Default |
|---|---|---|
| `strategy_returns` | 'pd.DataFrame' | — |
| `weights` | 'dict[str, float]' | — |
| `confidence` | 'float' | 0.95 |

## `portfolio_var(strategy_returns: 'pd.DataFrame', weights: 'dict[str, float]', confidence: 'float' = 0.95) -> 'float'`

Aggregate VaR for a weighted portfolio of strategies.

| Parameter | Type | Default |
|---|---|---|
| `strategy_returns` | 'pd.DataFrame' | — |
| `weights` | 'dict[str, float]' | — |
| `confidence` | 'float' | 0.95 |
