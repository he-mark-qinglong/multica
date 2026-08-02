"""Tests for _shared/risk/budget.py (D18)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import math

import pytest

from _shared.risk.budget import (
    StrategyBudget,
    check_all,
    check_budget,
    realized_vol_annualized,
    reallocate,
)


def test_realized_vol_annualized_scaling():
    # Constant-alternating returns: known sample stdev.
    returns = [0.01, -0.01] * 50
    # sample stdev of +/-0.01 with mean 0 = sqrt(n/(n-1)) * 0.01
    expected = math.sqrt(100 / 99) * 0.01 * math.sqrt(365) * 100.0
    assert realized_vol_annualized(returns, 365) == pytest.approx(expected)


def test_realized_vol_too_few_observations_is_zero():
    assert realized_vol_annualized([]) == 0.0
    assert realized_vol_annualized([0.05]) == 0.0


def test_under_budget_multiplier_is_one():
    budget = StrategyBudget(strategy="mm_btc", vol_budget_pct=20.0)
    status = check_budget(budget, [0.001, -0.001] * 30, periods_per_year=365)
    assert not status.over_budget
    assert status.multiplier == 1.0
    assert status.utilization < 1.0


def test_over_budget_scales_down_proportionally():
    budget = StrategyBudget(strategy="mm_btc", vol_budget_pct=10.0)
    # Daily 1% moves -> annualized ~ 1% * sqrt(365) ~ 19.1% -> ~1.91x budget
    status = check_budget(budget, [0.01, -0.01] * 50, periods_per_year=365)
    assert status.over_budget
    assert status.multiplier == pytest.approx(
        budget.vol_budget_pct / status.realized_vol_pct
    )
    assert 0.0 < status.multiplier < 1.0
    # Applying the multiplier to position would bring vol back to budget:
    assert status.realized_vol_pct * status.multiplier == pytest.approx(10.0)


def test_min_multiplier_floors_the_scale_down():
    budget = StrategyBudget(strategy="trend", vol_budget_pct=5.0)
    status = check_budget(
        budget, [0.05, -0.05] * 50, periods_per_year=365, min_multiplier=0.25
    )
    assert status.over_budget
    assert status.multiplier == 0.25


def test_no_returns_means_no_derisking():
    budget = StrategyBudget(strategy="new_strat", vol_budget_pct=5.0)
    status = check_budget(budget, [])
    assert status.realized_vol_pct == 0.0
    assert status.multiplier == 1.0
    assert not status.over_budget


def test_check_all_covers_strategies_without_returns():
    budgets = [
        StrategyBudget(strategy="a", vol_budget_pct=10.0),
        StrategyBudget(strategy="b", vol_budget_pct=10.0),
    ]
    statuses = check_all(budgets, {"a": [0.05, -0.05] * 30})
    by_name = {s.strategy: s for s in statuses}
    assert by_name["a"].over_budget
    assert by_name["b"].multiplier == 1.0


def test_reallocate_preserves_total_by_default():
    budgets = [
        StrategyBudget(strategy="a", vol_budget_pct=10.0),
        StrategyBudget(strategy="b", vol_budget_pct=20.0),
    ]
    new = reallocate(budgets, {"a": 3.0, "b": 1.0})
    by_name = {b.strategy: b for b in new}
    assert by_name["a"].vol_budget_pct == pytest.approx(22.5)
    assert by_name["b"].vol_budget_pct == pytest.approx(7.5)
    assert sum(b.vol_budget_pct for b in new) == pytest.approx(30.0)


def test_reallocate_with_explicit_total():
    budgets = [StrategyBudget(strategy="a", vol_budget_pct=10.0),
               StrategyBudget(strategy="b", vol_budget_pct=10.0)]
    new = reallocate(budgets, {"a": 1.0, "b": 1.0}, total_budget_pct=40.0)
    assert all(b.vol_budget_pct == pytest.approx(20.0) for b in new)


def test_reallocate_rejects_unknown_or_missing_strategies():
    budgets = [StrategyBudget(strategy="a", vol_budget_pct=10.0)]
    with pytest.raises(ValueError, match="unknown"):
        reallocate(budgets, {"a": 1.0, "typo": 1.0})
    with pytest.raises(ValueError, match="missing"):
        reallocate(
            budgets + [StrategyBudget(strategy="b", vol_budget_pct=5.0)],
            {"a": 1.0},
        )


def test_reallocate_rejects_all_zero_weights():
    budgets = [StrategyBudget(strategy="a", vol_budget_pct=10.0)]
    with pytest.raises(ValueError, match="positive total"):
        reallocate(budgets, {"a": 0.0})


def test_budget_validation():
    with pytest.raises(ValueError):
        StrategyBudget(strategy="", vol_budget_pct=10.0)
    with pytest.raises(ValueError):
        StrategyBudget(strategy="a", vol_budget_pct=0.0)


def test_status_is_frozen():
    budget = StrategyBudget(strategy="a", vol_budget_pct=10.0)
    status = check_budget(budget, [0.01, -0.01] * 10)
    with pytest.raises(AttributeError):
        status.multiplier = 0.5  # type: ignore[misc]
