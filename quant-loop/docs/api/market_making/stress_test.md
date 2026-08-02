# `_shared.market_making.stress_test`

Source: `_shared/market_making/stress_test.py`

Stress testing engine — predefined crisis scenarios.

## class `StressResult(scenario: 'str', pnl_impact_usd: 'float', pnl_impact_pct: 'float', max_drawdown_pct: 'float', var_before_bp: 'float', var_after_bp: 'float', survival: 'bool') -> None`

Impact estimate for one scenario.

## class `StressScenario(name: 'str', description: 'str', price_shock_pct: 'float' = 0.0, funding_shock_pct: 'float' = 0.0, spread_multiplier: 'float' = 1.0, correlation_override: 'float | None' = None, duration_hours: 'float' = 1.0) -> None`

A predefined stress scenario.

## `run_all_stress_tests(capital_usd: 'float', net_inventory_usd: 'float', historical_pnl_bp: 'list[float]', historical_spread_bp: 'float' = 4.0) -> 'list[StressResult]'`

Run all predefined stress scenarios.

| Parameter | Type | Default |
|---|---|---|
| `capital_usd` | 'float' | — |
| `net_inventory_usd` | 'float' | — |
| `historical_pnl_bp` | 'list[float]' | — |
| `historical_spread_bp` | 'float' | 4.0 |

## `run_stress_test(scenario: 'StressScenario', capital_usd: 'float', net_inventory_usd: 'float', historical_pnl_bp: 'list[float]', historical_spread_bp: 'float' = 4.0, survival_threshold_pct: 'float' = -0.25) -> 'StressResult'`

Estimate portfolio impact under a stress scenario.

| Parameter | Type | Default |
|---|---|---|
| `scenario` | 'StressScenario' | — |
| `capital_usd` | 'float' | — |
| `net_inventory_usd` | 'float' | — |
| `historical_pnl_bp` | 'list[float]' | — |
| `historical_spread_bp` | 'float' | 4.0 |
| `survival_threshold_pct` | 'float' | -0.25 |
