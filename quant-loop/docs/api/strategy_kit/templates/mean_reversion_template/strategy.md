# `_shared.strategy_kit.templates.mean_reversion_template.strategy`

Source: `_shared/strategy_kit/templates/mean_reversion_template/strategy.py`

RSI mean-reversion strategy template (A11).

## `generate_signals(bars: 'Dict[str, pd.DataFrame]', config: 'Dict') -> 'List[Trade]'`

Emit closed trades fading RSI extremes back toward the midline.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'Dict[str, pd.DataFrame]' | — |
| `config` | 'Dict' | — |
