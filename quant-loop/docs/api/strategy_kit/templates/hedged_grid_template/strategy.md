# `_shared.strategy_kit.templates.hedged_grid_template.strategy`

Source: `_shared/strategy_kit/templates/hedged_grid_template/strategy.py`

Hedged grid strategy template (A11).

## `generate_signals(bars: 'Dict[str, pd.DataFrame]', config: 'Dict') -> 'List[Trade]'`

Emit both grid legs; legs are independent (hedged overlap allowed).

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'Dict[str, pd.DataFrame]' | — |
| `config` | 'Dict' | — |
