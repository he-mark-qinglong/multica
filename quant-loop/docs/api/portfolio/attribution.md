# `_shared.portfolio.attribution`

Source: `_shared/portfolio/attribution.py`

Portfolio performance & drawdown attribution (I10, I15).

## class `BrinsonResult(segments: 'tuple', allocation: 'Dict[str, float]', selection: 'Dict[str, float]', interaction: 'Dict[str, float]', total_active_return: 'float') -> None`

Per-segment Brinson active-return decomposition.

## class `ContributionResult(pnl: 'Dict[str, float]', share_of_total: 'Dict[str, float]', share_of_gross: 'Dict[str, float]', total_pnl: 'float', top_contributor: 'str', worst_contributor: 'str') -> None`

PnL-share attribution across contributors.

## class `DrawdownAttribution(peak: 'pd.Timestamp', trough: 'pd.Timestamp', max_drawdown: 'float', contributions: 'Dict[str, float]', contribution_shares: 'Dict[str, float]', top_detractor: 'str') -> None`

Decomposition of the max-drawdown window by contributor.

## `brinson_decomposition(portfolio_weights: 'Mapping[str, float]', portfolio_returns: 'Mapping[str, float]', benchmark_weights: 'Mapping[str, float]', benchmark_returns: 'Mapping[str, float]') -> 'BrinsonResult'`

Brinson-Hood-Beebower decomposition over matching segments.

| Parameter | Type | Default |
|---|---|---|
| `portfolio_weights` | 'Mapping[str, float]' | — |
| `portfolio_returns` | 'Mapping[str, float]' | — |
| `benchmark_weights` | 'Mapping[str, float]' | — |
| `benchmark_returns` | 'Mapping[str, float]' | — |

## `drawdown_attribution(returns: 'pd.DataFrame', weights: 'Mapping[str, float] | None' = None, initial_equity: 'float' = 1.0) -> 'DrawdownAttribution'`

Find the portfolio's max-drawdown window and attribute the loss.

| Parameter | Type | Default |
|---|---|---|
| `returns` | 'pd.DataFrame' | — |
| `weights` | 'Mapping[str, float] | None' | None |
| `initial_equity` | 'float' | 1.0 |

## `pnl_contribution(pnl: 'Mapping[str, float]') -> 'ContributionResult'`

Attribute total PnL across strategies or symbols.

| Parameter | Type | Default |
|---|---|---|
| `pnl` | 'Mapping[str, float]' | — |

## `time_contribution(returns: 'pd.DataFrame', freq: 'str' = 'ME') -> 'pd.DataFrame'`

Period-sliced compounded contribution per contributor.

| Parameter | Type | Default |
|---|---|---|
| `returns` | 'pd.DataFrame' | — |
| `freq` | 'str' | 'ME' |
