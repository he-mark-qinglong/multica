"""Tests for ``_shared/validation/sensitivity.py`` (G18)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import math

import pytest

from _shared.validation.sensitivity import (
    CLIFF_THRESHOLD,
    SensitivityReport,
    compute_sensitivity,
    sensitivity_table,
)


def _linear_strategy(params, _data):
    """sharpe = 1.0 * a + 0.5 * b; pnl = 100 * a + 10 * b."""
    return {
        "sharpe": 1.0 * params["a"] + 0.5 * params["b"],
        "pnl": 100.0 * params["a"] + 10.0 * params["b"],
    }


def _cliff_strategy(params, _data):
    """Sharpe collapses away from threshold=1.0 (narrow spike)."""
    t = params["threshold"]
    sharpe = 2.0 if abs(t - 1.0) < 0.05 else -1.0
    return {"sharpe": sharpe, "pnl": 100.0 * sharpe}


def test_report_shape_and_baseline():
    rep = compute_sensitivity(
        _linear_strategy, {"a": 2.0, "b": 4.0}, pct_moves=(0.10,)
    )
    assert isinstance(rep, SensitivityReport)
    assert rep.base_metrics == {"sharpe": 4.0, "pnl": 240.0}
    # 2 params × 2 metrics = 4 sensitivity rows.
    assert len(rep.sensitivities) == 4


def test_elasticity_matches_analytic_linear_case():
    # sharpe = a + 0.5b at a=2, b=4 → ∂sharpe/∂a = 1
    # elasticity_a = (Δs/s) / (Δa/a) = (1*0.2 / 4.0) / 0.10 = 0.5
    rep = compute_sensitivity(
        _linear_strategy, {"a": 2.0, "b": 4.0}, pct_moves=(0.10,)
    )
    by_key = {(s.param, s.metric): s for s in rep.sensitivities}
    assert by_key[("a", "sharpe")].elasticity == pytest.approx(0.5)
    # pnl = 100a + 10b → ∂pnl/∂b = 10; (10*0.4 / 240) / 0.10 = 1/6
    assert by_key[("b", "pnl")].elasticity == pytest.approx(1.0 / 6.0)


def test_ranking_is_by_abs_elasticity_descending():
    rep = compute_sensitivity(
        _linear_strategy, {"a": 2.0, "b": 4.0}, pct_moves=(0.10,)
    )
    mags = [abs(s.elasticity) for s in rep.sensitivities]
    assert mags == sorted(mags, reverse=True)


def test_cliff_detection_flags_narrow_spike():
    rep = compute_sensitivity(
        _cliff_strategy, {"threshold": 1.0}, pct_moves=(0.10, 0.25)
    )
    cliffs = rep.cliffs
    assert cliffs, "a ±10%/±25% move off the spike must be flagged"
    assert all(abs(s.elasticity) > CLIFF_THRESHOLD for s in cliffs)
    # Every metric for the only param is a cliff here.
    assert {s.param for s in cliffs} == {"threshold"}


def test_flat_strategy_has_no_cliffs():
    rep = compute_sensitivity(
        lambda p, _d: {"sharpe": 1.0, "pnl": 50.0}, {"x": 3.0}
    )
    assert rep.cliffs == ()
    assert all(s.elasticity == 0.0 for s in rep.sensitivities)


def test_zero_baseline_metric_gives_inf_when_metric_moves():
    def strat(params, _d):
        return {"sharpe": 0.0 + params["x"], "pnl": 1.0}

    # x baseline 0 → sharpe baseline 0; any move makes the metric move.
    rep = compute_sensitivity(strat, {"x": 0.0}, pct_moves=(0.10,))
    by_key = {(s.param, s.metric): s for s in rep.sensitivities}
    assert math.isinf(by_key[("x", "sharpe")].elasticity)
    assert by_key[("x", "sharpe")].is_cliff
    # pnl constant at 1.0 → elasticity 0 regardless.
    assert by_key[("x", "pnl")].elasticity == 0.0


def test_non_numeric_params_are_skipped():
    rep = compute_sensitivity(
        _linear_strategy, {"a": 2.0, "b": 4.0, "name": "v1", "flag": True}
    )
    assert {s.param for s in rep.sensitivities} == {"a", "b"}


def test_data_is_forwarded_verbatim():
    seen = []

    def strat(params, data):
        seen.append(data)
        return {"sharpe": 1.0, "pnl": 1.0}

    sentinel = object()
    compute_sensitivity(strat, {"x": 1.0}, data=sentinel)
    assert seen and all(d is sentinel for d in seen)


def test_worst_case_across_move_sizes_is_reported():
    # Elastic at ±25% but flat at ±10%.
    def strat(params, _d):
        x = params["x"]
        sharpe = 1.0 if abs(x - 1.0) <= 0.11 else -2.0
        return {"sharpe": sharpe, "pnl": sharpe}

    rep = compute_sensitivity(strat, {"x": 1.0}, pct_moves=(0.10, 0.25))
    s = next(s for s in rep.sensitivities if s.metric == "sharpe")
    # ±25% move crosses the boundary → elasticity must reflect that move.
    assert abs(s.elasticity) > CLIFF_THRESHOLD
    assert set(s.metric_at_moves) == {0.10, 0.25}


def test_table_renders_ranking_and_cliff_marks():
    rep = compute_sensitivity(
        _cliff_strategy, {"threshold": 1.0}, pct_moves=(0.10, 0.25)
    )
    text = sensitivity_table(rep)
    assert "threshold" in text
    assert "*CLIFF*" in text
    assert "elasticity" in text.splitlines()[0]
    assert "cliff(s)" in text.splitlines()[-1]


def test_report_is_frozen():
    rep = compute_sensitivity(_linear_strategy, {"a": 2.0, "b": 4.0})
    with pytest.raises(Exception):
        rep.base_metrics = {}
