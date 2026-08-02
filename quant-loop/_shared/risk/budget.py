"""Per-strategy risk budget allocation (D18).

Each strategy receives an *annualized volatility budget* (in %). The
allocator tracks the strategy's realized volatility from its recent period
returns and, once realized vol exceeds the budget, emits a position
``multiplier`` that scales the strategy down proportionally:

    multiplier = min(1, budget / realized)        (0 when realized == 0 usage is under budget)

A multiplier of 1.0 means "fully within budget"; 0.5 means "halve the
position to come back inside the budget". The multiplier is a pure
function of (budget, trailing returns) — no hidden state — so it can be
recomputed identically in backtest and live.

``reallocate`` redistributes a fixed total budget across strategies by
target weights (normalized defensively), which is how a portfolio manager
moves risk from a decaying strategy to a performing one without changing
gross risk.

References:
- Grinold & Kahn, "Active Portfolio Management", ch. 5 — risk budgeting
  as the binding constraint on active positions.
- Maillard, Roncalli & Teiletche (2010), "The Properties of Equally
  Weighted Risk Contribution Portfolios", JPM — proportional de-risking
  when a sleeve's risk contribution exceeds its allocation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

TRADING_DAYS_PER_YEAR = 365  # crypto trades 24/7; override via callers if not


@dataclass(frozen=True)
class StrategyBudget:
    """Annualized volatility budget for one strategy.

    Attributes:
        strategy: strategy identifier.
        vol_budget_pct: annualized volatility cap, in percent (e.g. 10.0 = 10%).
    """

    strategy: str
    vol_budget_pct: float

    def __post_init__(self) -> None:
        if not self.strategy:
            raise ValueError("strategy must be non-empty")
        if self.vol_budget_pct <= 0:
            raise ValueError(
                f"vol_budget_pct must be positive, got {self.vol_budget_pct}"
            )


@dataclass(frozen=True)
class BudgetStatus:
    """Realized-vs-budget snapshot for one strategy.

    Attributes:
        strategy: strategy identifier.
        vol_budget_pct: the budget, annualized %.
        realized_vol_pct: trailing realized vol, annualized %.
        utilization: realized / budget (1.0 = exactly at budget).
        multiplier: position scale to apply, in [min_multiplier, 1.0].
        over_budget: True iff realized vol exceeds the budget.
    """

    strategy: str
    vol_budget_pct: float
    realized_vol_pct: float
    utilization: float
    multiplier: float
    over_budget: bool


def realized_vol_annualized(
    returns: Sequence[float],
    periods_per_year: float = TRADING_DAYS_PER_YEAR,
) -> float:
    """Annualized volatility (%) of a series of simple period returns. Pure.

    Uses the sample standard deviation (ddof=1) scaled by
    ``sqrt(periods_per_year)``. Returns 0.0 for fewer than 2 observations —
    with no evidence of risk we do not de-risk.
    """
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year) * 100.0


def check_budget(
    budget: StrategyBudget,
    returns: Sequence[float],
    periods_per_year: float = TRADING_DAYS_PER_YEAR,
    min_multiplier: float = 0.0,
) -> BudgetStatus:
    """Compare trailing realized vol against the budget. Pure.

    The multiplier is 1.0 while utilization <= 1; past the budget it is
    ``budget / realized`` (proportional scale-down), floored at
    ``min_multiplier`` so an operator can keep a token position for
    monitoring instead of going flat.
    """
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    if not 0.0 <= min_multiplier <= 1.0:
        raise ValueError("min_multiplier must be in [0, 1]")
    realized = realized_vol_annualized(returns, periods_per_year)
    utilization = realized / budget.vol_budget_pct
    over = realized > budget.vol_budget_pct
    if over:
        multiplier = max(min_multiplier, budget.vol_budget_pct / realized)
    else:
        multiplier = 1.0
    return BudgetStatus(
        strategy=budget.strategy,
        vol_budget_pct=budget.vol_budget_pct,
        realized_vol_pct=realized,
        utilization=utilization,
        multiplier=multiplier,
        over_budget=over,
    )


def check_all(
    budgets: Sequence[StrategyBudget],
    returns_by_strategy: Mapping[str, Sequence[float]],
    periods_per_year: float = TRADING_DAYS_PER_YEAR,
    min_multiplier: float = 0.0,
) -> Tuple[BudgetStatus, ...]:
    """Evaluate every budget; strategies without returns get realized 0. Pure."""
    return tuple(
        check_budget(
            b,
            returns_by_strategy.get(b.strategy, ()),
            periods_per_year=periods_per_year,
            min_multiplier=min_multiplier,
        )
        for b in budgets
    )


def reallocate(
    budgets: Sequence[StrategyBudget],
    target_weights: Mapping[str, float],
    total_budget_pct: Optional[float] = None,
) -> Tuple[StrategyBudget, ...]:
    """Redistribute the total budget across strategies by weight. Pure.

    ``target_weights`` maps strategy -> relative weight; weights are
    normalized to sum to 1 (non-positive weights are clipped to 0). The
    total budget defaults to the sum of the current budgets, so
    reallocation is risk-neutral unless the caller explicitly passes a new
    ``total_budget_pct``. Every named strategy must appear in
    ``target_weights``; unknown weight keys are rejected to catch typos.
    """
    names = {b.strategy for b in budgets}
    unknown = set(target_weights) - names
    if unknown:
        raise ValueError(f"target_weights for unknown strategies: {sorted(unknown)}")
    missing = names - set(target_weights)
    if missing:
        raise ValueError(f"target_weights missing strategies: {sorted(missing)}")

    clipped = {k: max(0.0, float(v)) for k, v in target_weights.items()}
    weight_sum = sum(clipped.values())
    if weight_sum <= 0:
        raise ValueError("target_weights must have a positive total")

    total = (
        sum(b.vol_budget_pct for b in budgets)
        if total_budget_pct is None
        else float(total_budget_pct)
    )
    if total <= 0:
        raise ValueError("total_budget_pct must be positive")

    return tuple(
        StrategyBudget(
            strategy=b.strategy,
            vol_budget_pct=total * clipped[b.strategy] / weight_sum,
        )
        for b in budgets
    )
