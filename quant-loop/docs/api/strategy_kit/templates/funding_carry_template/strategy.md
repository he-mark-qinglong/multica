# `_shared.strategy_kit.templates.funding_carry_template.strategy`

Source: `_shared/strategy_kit/templates/funding_carry_template/strategy.py`

Funding-carry strategy template (A11).

## `generate_signals(bars: 'Dict[str, pd.DataFrame]', config: 'Dict') -> 'List[Trade]'`

Emit funding-carry trades: short high funding, long negative funding.

| Parameter | Type | Default |
|---|---|---|
| `bars` | 'Dict[str, pd.DataFrame]' | — |
| `config` | 'Dict' | — |
