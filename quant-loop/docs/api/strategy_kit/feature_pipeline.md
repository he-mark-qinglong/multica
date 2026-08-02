# `_shared.strategy_kit.feature_pipeline`

Source: `_shared/strategy_kit/feature_pipeline.py`

Declarative feature pipeline with topological ordering and a no-lookahead assertion checker.

## class `FeatureDef(name: 'str', inputs: 'Tuple[str, ...]', func: 'Callable[[pd.DataFrame], pd.Series]', lookback: 'int' = 0) -> None`

One declarative feature.

## class `FeaturePipeline(defs: 'Tuple[FeatureDef, ...] | list[FeatureDef]', version: 'str' = '1.0.0') -> 'None'`

Topologically-ordered, version-tagged feature computation.

### `assert_no_lookahead(self, df: 'pd.DataFrame', sample_points: 'int' = 5, rtol: 'float' = 1e-09) -> 'None'`

Empirically verify every feature is causal on this data sample.

| Parameter | Type | Default |
|---|---|---|
| `df` | 'pd.DataFrame' | — |
| `sample_points` | 'int' | 5 |
| `rtol` | 'float' | 1e-09 |

### `compute(self, df: 'pd.DataFrame', include_inputs: 'bool' = False) -> 'pd.DataFrame'`

Compute all features; result carries ``attrs["feature_version"]``.

| Parameter | Type | Default |
|---|---|---|
| `df` | 'pd.DataFrame' | — |
| `include_inputs` | 'bool' | False |

### `compute_cached(self, df: 'pd.DataFrame', path: 'str | Path', force: 'bool' = False) -> 'pd.DataFrame'`

Load from cache when version matches, else compute + save.

| Parameter | Type | Default |
|---|---|---|
| `df` | 'pd.DataFrame' | — |
| `path` | 'str | Path' | — |
| `force` | 'bool' | False |

### `load_cache(self, path: 'str | Path') -> 'pd.DataFrame'`

Load a cached frame, refusing stale versions.

| Parameter | Type | Default |
|---|---|---|
| `path` | 'str | Path' | — |

### `resolve_order(self, available_columns: 'Tuple[str, ...] | list[str]') -> 'Tuple[FeatureDef, ...]'`

Kahn topological sort over feature dependencies.

| Parameter | Type | Default |
|---|---|---|
| `available_columns` | 'Tuple[str, ...] | list[str]' | — |

### `save_cache(self, features: 'pd.DataFrame', path: 'str | Path') -> 'Path'`

Write features to parquet + version sidecar.

| Parameter | Type | Default |
|---|---|---|
| `features` | 'pd.DataFrame' | — |
| `path` | 'str | Path' | — |

## class `LookaheadError`

A feature's value depends on data after its timestamp.

## class `PipelineDefinitionError`

Bad DAG: unknown input, cycle, or duplicate feature name.
