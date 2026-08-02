# `_shared.templates.example_strategy`

Source: `_shared/templates/example_strategy.py`

Minimal example strategy implementing strategy contract v2.

## `generate_signals(bars: 'Dict[str, pd.DataFrame]', config: 'Dict[str, Any]') -> 'List[Trade]'`

Contract v2 entry point: bars dict + config -> list[Trade].

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'Dict[str, pd.DataFrame]' | — |
| `config` | 'Dict[str, Any]' | — |
