# `_shared.strategy_kit.templates.momentum_template.strategy`

Source: `_shared/strategy_kit/templates/momentum_template/strategy.py`

EMA-cross momentum strategy template (A11).

## `generate_signals(bars: 'Dict[str, pd.DataFrame]', config: 'Dict') -> 'List[Trade]'`

Emit closed trades from EMA fast/slow crosses on the primary symbol.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'Dict[str, pd.DataFrame]' | — |
| `config` | 'Dict' | — |
