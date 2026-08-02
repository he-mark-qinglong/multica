# `_shared.strategy_kit.meta_labeling`

Source: `_shared/strategy_kit/meta_labeling.py`

Meta-labeling (López de Prado 2018, AFML ch. 3.6–3.8).

## class `MetaDataset(X: 'pd.DataFrame', y: 'pd.Series', w: 'pd.Series', events: 'pd.DataFrame') -> None`

Aligned meta-labeling dataset. Immutable container.

## class `MetaModel(*args, **kwargs)`

Minimal contract for a meta-label classifier.

### `fit(self, X: 'np.ndarray', y: 'np.ndarray', sample_weight: 'Optional[np.ndarray]' = None) -> "'MetaModel'"`

| Parameter | Type | Default |
|---|---|---|
| `X` | 'np.ndarray' | — |
| `y` | 'np.ndarray' | — |
| `sample_weight` | 'Optional[np.ndarray]' | None |

### `predict_proba(self, X: 'np.ndarray') -> 'np.ndarray'`

| Parameter | Type | Default |
|---|---|---|
| `X` | 'np.ndarray' | — |

## class `ToyLogistic(config: 'Optional[ToyLogisticConfig]' = None) -> 'None'`

Weighted binary logistic regression via full-batch gradient descent.

### `fit(self, X: 'np.ndarray', y: 'np.ndarray', sample_weight: 'Optional[np.ndarray]' = None) -> "'ToyLogistic'"`

| Parameter | Type | Default |
|---|---|---|
| `X` | 'np.ndarray' | — |
| `y` | 'np.ndarray' | — |
| `sample_weight` | 'Optional[np.ndarray]' | None |

### `predict_proba(self, X: 'np.ndarray') -> 'np.ndarray'`

P(y=1) per row, in (0, 1).

| Parameter | Type | Default |
|---|---|---|
| `X` | 'np.ndarray' | — |

## class `ToyLogisticConfig(lr: 'float' = 0.1, n_iter: 'int' = 2000, l2: 'float' = 0.001) -> None`

Hyperparameters for ``ToyLogistic``.

## `build_meta_dataset(data: 'pd.DataFrame', side: 'pd.Series', config: 'Optional[BarrierConfig]' = None, features: 'Optional[Mapping[str, FeatureFn]]' = None, weight: 'str' = 'return') -> 'MetaDataset'`

Build (X, y, w) for meta-labeling from a primary side series.

| Parameter | Type | Default |
|---|---|---|
| `data` | 'pd.DataFrame' | — |
| `side` | 'pd.Series' | — |
| `config` | 'Optional[BarrierConfig]' | None |
| `features` | 'Optional[Mapping[str, FeatureFn]]' | None |
| `weight` | 'str' | 'return' |

## `market_state_features(vol_lookback: 'int' = 20, trend_lookback: 'int' = 20, vol_of_vol_lookback: 'int' = 60) -> 'Dict[str, FeatureFn]'`

Default pluggable feature set (all causal, data <= t only).

| Parameter | Type | Default |
|---|---|---|
| `vol_lookback` | 'int' | 20 |
| `trend_lookback` | 'int' | 20 |
| `vol_of_vol_lookback` | 'int' | 60 |

## `uniqueness_weights(t0: 'pd.Series', t1: 'pd.Series', index: 'pd.Index') -> 'pd.Series'`

Average uniqueness per event (sequential-bootstrap spirit).

| Parameter | Type | Default |
|---|---|---|
| `t0` | 'pd.Series' | — |
| `t1` | 'pd.Series' | — |
| `index` | 'pd.Index' | — |
