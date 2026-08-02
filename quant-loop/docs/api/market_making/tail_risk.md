# `_shared.market_making.tail_risk`

Source: `_shared/market_making/tail_risk.py`

Tail risk metrics: VaR, CVaR, and stress scenarios.

## class `TailRiskResult(var_95_bp: 'float', var_99_bp: 'float', cvar_95_bp: 'float', cvar_99_bp: 'float', mean_bp: 'float', std_bp: 'float', skewness: 'float', excess_kurtosis: 'float', cf_var_95_bp: 'float', cf_var_99_bp: 'float', worst_case_bp: 'float', max_consecutive_losses: 'int', n_samples: 'int') -> None`

Comprehensive tail-risk snapshot.

## `compute_tail_risk(pnl_bp: 'Sequence[float]') -> 'TailRiskResult'`

Full tail-risk analysis from a PnL history.

| Parameter | Type | Default |
|---|---|---|
| `pnl_bp` | 'Sequence[float]' | — |

## `cornish_fisher_var(pnl_bp: 'Sequence[float]', confidence: 'float' = 0.95) -> 'float'`

Cornish-Fisher VaR — adjusts parametric VaR for skew & kurtosis.

| Parameter | Type | Default |
|---|---|---|
| `pnl_bp` | 'Sequence[float]' | — |
| `confidence` | 'float' | 0.95 |

## `historical_cvar(pnl_bp: 'Sequence[float]', confidence: 'float' = 0.95) -> 'float'`

Historical CVaR (Expected Shortfall).

| Parameter | Type | Default |
|---|---|---|
| `pnl_bp` | 'Sequence[float]' | — |
| `confidence` | 'float' | 0.95 |

## `historical_var(pnl_bp: 'Sequence[float]', confidence: 'float' = 0.95) -> 'float'`

Historical VaR — the loss at the given confidence level.

| Parameter | Type | Default |
|---|---|---|
| `pnl_bp` | 'Sequence[float]' | — |
| `confidence` | 'float' | 0.95 |

## `max_consecutive_losses(pnl_bp: 'Sequence[float]') -> 'int'`

Longest run of consecutive negative trades.

| Parameter | Type | Default |
|---|---|---|
| `pnl_bp` | 'Sequence[float]' | — |
