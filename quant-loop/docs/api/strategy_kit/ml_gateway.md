# `_shared.strategy_kit.ml_gateway`

Source: `_shared/strategy_kit/ml_gateway.py`

ML gateway — single, version-checked entry point for model inference.

## class `FeatureSchemaError`

Feature columns do not match the model's training-time schema.

## class `MLGateway(bundles: 'Optional[dict[str, ModelBundle]]' = None) -> 'None'`

Version-checked inference entry point for one or more models.

### `bundle(self, name: 'str') -> 'ModelBundle'`

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |

### `predict(self, features: 'pd.DataFrame', model: 'Optional[str]' = None) -> 'pd.Series'`

Run inference after version + schema checks.

| Parameter | Type | Default |
|---|---|---|
| `features` | 'pd.DataFrame' | — |
| `model` | 'Optional[str]' | None |

### `register(self, name: 'str', bundle: 'ModelBundle', replace: 'bool' = False) -> 'None'`

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `bundle` | 'ModelBundle' | — |
| `replace` | 'bool' | False |

## class `ModelBundle(model: 'Predictor', model_version: 'str', feature_version: 'str', feature_names: 'tuple[str, ...]') -> None`

A model plus the schema/version it was trained against.

## class `PassthroughModel(column: 'Optional[str]' = None) -> 'None'`

Baseline "model": echoes a column (or row mean) as the prediction.

### `predict(self, X: 'pd.DataFrame') -> 'np.ndarray'`

| Parameter | Type | Default |
|---|---|---|
| `X` | 'pd.DataFrame' | — |

## class `Predictor(*args, **kwargs)`

Anything with a ``predict`` method (sklearn-compatible).

### `predict(self, X: 'Any') -> 'Any'`

| Parameter | Type | Default |
|---|---|---|
| `X` | 'Any' | — |

## class `VersionMismatchError`

Features were produced by a different pipeline version than the model was trained on.

## `load_pickle_model(path: 'str | Path', model_version: 'str', feature_version: 'str', feature_names: 'Sequence[str]') -> 'ModelBundle'`

Load a pickled model and bind it to its training-time schema.

| Parameter | Type | Default |
|---|---|---|
| `path` | 'str | Path' | — |
| `model_version` | 'str' | — |
| `feature_version` | 'str' | — |
| `feature_names` | 'Sequence[str]' | — |

## `make_passthrough_bundle(feature_names: 'Sequence[str]', feature_version: 'str', column: 'Optional[str]' = None, model_version: 'str' = 'passthrough-1.0') -> 'ModelBundle'`

Convenience: a ModelBundle wrapping ``PassthroughModel``.

| Parameter | Type | Default |
|---|---|---|
| `feature_names` | 'Sequence[str]' | — |
| `feature_version` | 'str' | — |
| `column` | 'Optional[str]' | None |
| `model_version` | 'str' | 'passthrough-1.0' |
