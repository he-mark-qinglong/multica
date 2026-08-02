# `_shared.strategy_kit.composer`

Source: `_shared/strategy_kit/composer.py`

Signal composer — combine multiple strategy signals into one composite.

## class `ComposerConfig(method: 'str' = 'fixed', weights: 'Mapping[str, float]' = <factory>, ic_lookback: 'int' = 250, ic_min_abs: 'float' = 0.0, decorrelate: 'bool' = True, corr_threshold: 'float' = 0.7, corr_lookback: 'int' = 250) -> None`

Configuration for ``compose_signals``.

## `compose_signals(signals: 'pd.DataFrame', config: 'ComposerConfig', forward_returns: 'Optional[pd.Series]' = None) -> 'pd.Series'`

Combine signal columns of ``signals`` into one composite in [-1, 1].

| Parameter | Type | Default |
|---|---|---|
| `signals` | 'pd.DataFrame' | — |
| `config` | 'ComposerConfig' | — |
| `forward_returns` | 'Optional[pd.Series]' | None |

## `decorrelate_weights(weights: 'pd.Series', signals: 'pd.DataFrame', threshold: 'float' = 0.7, lookback: 'int' = 250) -> 'pd.Series'`

Shrink weights of mutually-correlated signals, greedy by |weight|.

| Parameter | Type | Default |
|---|---|---|
| `weights` | 'pd.Series' | — |
| `signals` | 'pd.DataFrame' | — |
| `threshold` | 'float' | 0.7 |
| `lookback` | 'int' | 250 |

## `fixed_weights(signals: 'pd.DataFrame', weights: 'Mapping[str, float]') -> 'pd.Series'`

Normalise user weights onto the signal columns (missing -> 0).

| Parameter | Type | Default |
|---|---|---|
| `signals` | 'pd.DataFrame' | — |
| `weights` | 'Mapping[str, float]' | — |

## `ic_weights(signals: 'pd.DataFrame', forward_returns: 'pd.Series', lookback: 'int' = 250, min_abs_ic: 'float' = 0.0) -> 'pd.Series'`

Full-sample causal IC weights.

| Parameter | Type | Default |
|---|---|---|
| `signals` | 'pd.DataFrame' | — |
| `forward_returns` | 'pd.Series' | — |
| `lookback` | 'int' | 250 |
| `min_abs_ic` | 'float' | 0.0 |

## `vote_weights(signals: 'pd.DataFrame') -> 'pd.Series'`

One vote per signal.

| Parameter | Type | Default |
|---|---|---|
| `signals` | 'pd.DataFrame' | — |
