# `_shared.validation.fee_shock`

Source: `_shared/validation/fee_shock.py`

Fee-shock replay for validation reports.

## `fee_shock_sweep(equity: 'pd.Series', trades: 'Iterable[dict]', bps_levels: 'Iterable[float]') -> 'dict[str, dict]'`

Replay ``equity`` under each extra round-trip cost tier.

| Parameter | Type | Default |
|---|---|---|
| `equity` | 'pd.Series' | — |
| `trades` | 'Iterable[dict]' | — |
| `bps_levels` | 'Iterable[float]' | — |
