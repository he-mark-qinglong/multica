# `_shared.market_making.hmm_regime`

Source: `_shared/market_making/hmm_regime.py`

HMM regime detector — classify market state for conditional quoting.

## class `Regime(*values)`

## class `RegimeQuoteAdjustment(spread_multiplier: 'float' = 1.0, size_multiplier: 'float' = 1.0, inventory_limit_multiplier: 'float' = 1.0, skew_direction: 'float' = 0.0) -> None`

How to adjust quoting parameters based on regime.

## class `RegimeState(regime: 'Regime', probabilities: 'dict[str, float]', log_likelihood: 'float', n_observations: 'int') -> None`

Current regime classification.

## `detect_regime(prices: 'pd.Series | np.ndarray', use_hmm: 'bool' = True) -> 'RegimeState'`

Classify the current market regime from a price series.

| Parameter | Type | Default |
|---|---|---|
| `prices` | 'pd.Series | np.ndarray' | — |
| `use_hmm` | 'bool' | True |

## `get_regime_adjustment(state: 'RegimeState', trend_sign: 'float' = 0.0) -> 'RegimeQuoteAdjustment'`

Get quoting parameter adjustments for the current regime.

| Parameter | Type | Default |
|---|---|---|
| `state` | 'RegimeState' | — |
| `trend_sign` | 'float' | 0.0 |
