# `_shared.strategy_kit.labels`

Source: `_shared/strategy_kit/labels.py`

Triple-barrier labels (López de Prado 2018, AFML ch. 3).

## class `BarrierConfig(tp: 'float' = 0.02, sl: 'float' = 0.01, max_bars: 'int' = 24, side: 'int' = 1, sign_on_timeout: 'bool' = False) -> None`

Triple-barrier parameters.

## `triple_barrier_labels(close: 'pd.Series', config: 'BarrierConfig', high: 'Optional[pd.Series]' = None, low: 'Optional[pd.Series]' = None) -> 'pd.DataFrame'`

Compute triple-barrier labels for every bar.

| Parameter | Type | Default |
|---|---|---|
| `close` | 'pd.Series' | — |
| `config` | 'BarrierConfig' | — |
| `high` | 'Optional[pd.Series]' | None |
| `low` | 'Optional[pd.Series]' | None |
