# `_shared.market_making.kelly_sizing`

Source: `_shared/market_making/kelly_sizing.py`

Kelly criterion position sizing for market making.

## class `KellyParams(fraction: 'float' = 0.25, min_multiplier: 'float' = 0.1, max_multiplier: 'float' = 2.0, min_samples: 'int' = 30, confidence_threshold_bp: 'float' = 1.0) -> None`

Tunables for Kelly-based sizing.

## class `KellyResult(kelly_fraction: 'float', applied_fraction: 'float', sizing_multiplier: 'float', mean_edge_bp: 'float', std_edge_bp: 'float', n_samples: 'int', is_valid: 'bool') -> None`

Output of Kelly computation.

## `adaptive_kelly_multiplier(pnl_history_bp: 'Sequence[float]', params: 'KellyParams' = KellyParams(fraction=0.25, min_multiplier=0.1, max_multiplier=2.0, min_samples=30, confidence_threshold_bp=1.0)) -> 'float'`

Convenience: return just the sizing multiplier.

| Parameter | Type | Default |
|---|---|---|
| `pnl_history_bp` | 'Sequence[float]' | — |
| `params` | 'KellyParams' | KellyParams(fraction=0.25, min_multiplier=0.1, max_multiplier=2.0, min_samples=30, confidence_threshold_bp=1.0) |

## `compute_kelly(pnl_history_bp: 'Sequence[float]', params: 'KellyParams' = KellyParams(fraction=0.25, min_multiplier=0.1, max_multiplier=2.0, min_samples=30, confidence_threshold_bp=1.0)) -> 'KellyResult'`

Compute Kelly-optimal sizing from a history of per-trade PnL (in bp).

| Parameter | Type | Default |
|---|---|---|
| `pnl_history_bp` | 'Sequence[float]' | — |
| `params` | 'KellyParams' | KellyParams(fraction=0.25, min_multiplier=0.1, max_multiplier=2.0, min_samples=30, confidence_threshold_bp=1.0) |
