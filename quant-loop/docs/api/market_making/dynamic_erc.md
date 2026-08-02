# `_shared.market_making.dynamic_erc`

Source: `_shared/market_making/dynamic_erc.py`

Dynamic ERC rebalancing with correlation regime detection.

## class `DynamicERC(params: 'DynamicERCParams' = DynamicERCParams(lookback=504, min_lookback=60, rebalance_freq=24, crisis_corr_threshold=0.7, crisis_shrinkage=0.5, crisis_vol_threshold=2.0))`

Rolling-window ERC with crisis detection.

### `update(self, returns: 'pd.DataFrame') -> 'DynamicERCResult | None'`

Compute dynamic ERC weights from a returns DataFrame.

| Parameter | Type | Default |
|---|---|---|
| `returns` | 'pd.DataFrame' | — |

## class `DynamicERCParams(lookback: 'int' = 504, min_lookback: 'int' = 60, rebalance_freq: 'int' = 24, crisis_corr_threshold: 'float' = 0.7, crisis_shrinkage: 'float' = 0.5, crisis_vol_threshold: 'float' = 2.0) -> None`

Dynamic ERC configuration.

## class `DynamicERCResult(weights: 'dict[str, float]', raw_weights: 'dict[str, float]', is_crisis: 'bool', mean_correlation: 'float', diversification_ratio: 'float', n_observations: 'int') -> None`

Output of one dynamic ERC step.
