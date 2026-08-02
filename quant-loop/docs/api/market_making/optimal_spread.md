# `_shared.market_making.optimal_spread`

Source: `_shared/market_making/optimal_spread.py`

Avellaneda-Stoikov analytically optimal spread.

## class `OptimalSpreadParams(gamma: 'float' = 0.1, kappa: 'float' = 1.5, horizon_seconds: 'float' = 300.0, min_spread_bp: 'float' = 1.0, max_spread_bp: 'float' = 50.0) -> None`

Parameters for the A-S optimal spread.

## `estimate_kappa(fills_per_second: 'float', our_quote_share: 'float' = 0.1) -> 'float'`

Estimate order arrival intensity κ from observed fill rate.

| Parameter | Type | Default |
|---|---|---|
| `fills_per_second` | 'float' | — |
| `our_quote_share` | 'float' | 0.1 |

## `optimal_half_spread(sigma: 'float', time_remaining: 'float', params: 'OptimalSpreadParams' = OptimalSpreadParams(gamma=0.1, kappa=1.5, horizon_seconds=300.0, min_spread_bp=1.0, max_spread_bp=50.0)) -> 'float'`

Compute the A-S optimal half-spread as a fraction of price.

| Parameter | Type | Default |
|---|---|---|
| `sigma` | 'float' | — |
| `time_remaining` | 'float' | — |
| `params` | 'OptimalSpreadParams' | OptimalSpreadParams(gamma=0.1, kappa=1.5, horizon_seconds=300.0, min_spread_bp=1.0, max_spread_bp=50.0) |
