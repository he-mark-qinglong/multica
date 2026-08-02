# `_shared.risk.budget`

Source: `_shared/risk/budget.py`

Per-strategy risk budget allocation (D18).

## class `BudgetStatus(strategy: 'str', vol_budget_pct: 'float', realized_vol_pct: 'float', utilization: 'float', multiplier: 'float', over_budget: 'bool') -> None`

Realized-vs-budget snapshot for one strategy.

## class `StrategyBudget(strategy: 'str', vol_budget_pct: 'float') -> None`

Annualized volatility budget for one strategy.

## `check_all(budgets: 'Sequence[StrategyBudget]', returns_by_strategy: 'Mapping[str, Sequence[float]]', periods_per_year: 'float' = 365, min_multiplier: 'float' = 0.0) -> 'Tuple[BudgetStatus, ...]'`

Evaluate every budget; strategies without returns get realized 0. Pure.

| Parameter | Type | Default |
|---|---|---|
| `budgets` | 'Sequence[StrategyBudget]' | — |
| `returns_by_strategy` | 'Mapping[str, Sequence[float]]' | — |
| `periods_per_year` | 'float' | 365 |
| `min_multiplier` | 'float' | 0.0 |

## `check_budget(budget: 'StrategyBudget', returns: 'Sequence[float]', periods_per_year: 'float' = 365, min_multiplier: 'float' = 0.0) -> 'BudgetStatus'`

Compare trailing realized vol against the budget. Pure.

| Parameter | Type | Default |
|---|---|---|
| `budget` | 'StrategyBudget' | — |
| `returns` | 'Sequence[float]' | — |
| `periods_per_year` | 'float' | 365 |
| `min_multiplier` | 'float' | 0.0 |

## `realized_vol_annualized(returns: 'Sequence[float]', periods_per_year: 'float' = 365) -> 'float'`

Annualized volatility (%) of a series of simple period returns. Pure.

| Parameter | Type | Default |
|---|---|---|
| `returns` | 'Sequence[float]' | — |
| `periods_per_year` | 'float' | 365 |

## `reallocate(budgets: 'Sequence[StrategyBudget]', target_weights: 'Mapping[str, float]', total_budget_pct: 'Optional[float]' = None) -> 'Tuple[StrategyBudget, ...]'`

Redistribute the total budget across strategies by weight. Pure.

| Parameter | Type | Default |
|---|---|---|
| `budgets` | 'Sequence[StrategyBudget]' | — |
| `target_weights` | 'Mapping[str, float]' | — |
| `total_budget_pct` | 'Optional[float]' | None |
