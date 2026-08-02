"""Tests for ``_shared/validation/self_review.py`` (self-review layer)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.validation.self_review import (
    Belief,
    DeviationLevel,
    Lesson,
    MetricSet,
    SelfReviewReport,
    StrategyReview,
    Tolerance,
    grade_deviation,
    grade_metric,
    render_html,
    review_strategy,
    update_belief,
)


# --- grade_metric ---------------------------------------------------------------

def test_grade_metric_within_tolerance():
    d = grade_metric("annualized_return", 0.15, 0.14)
    assert d.level is DeviationLevel.WITHIN_TOLERANCE
    assert d.rel_error == pytest.approx((0.15 - 0.14) / 0.15)


def test_grade_metric_watch_and_breach():
    tol = Tolerance(watch_rel=0.25, breach_rel=0.50)
    d_watch = grade_metric("annualized_return", 0.15, 0.15 * 0.7, tol)
    assert d_watch.level is DeviationLevel.WATCH
    d_breach = grade_metric("annualized_return", 0.15, 0.15 * 0.4, tol)
    assert d_breach.level is DeviationLevel.BREACH


def test_grade_metric_drawdown_worse_means_deeper():
    tol = Tolerance(watch_rel=0.25, breach_rel=0.50)
    # expected -20%, actual -35%: 75% deeper → breach.
    d = grade_metric("max_drawdown", -0.20, -0.35, tol)
    assert d.level is DeviationLevel.BREACH
    assert d.rel_error == pytest.approx(0.75)
    # actual shallower than expected → negative rel error (better).
    d2 = grade_metric("max_drawdown", -0.20, -0.10, tol)
    assert d2.rel_error == pytest.approx(-0.5)


def test_grade_metric_large_deviation_either_direction_flags():
    # A backtest that *under*-predicts by a wide margin is also miscalibrated.
    tol = Tolerance(watch_rel=0.25, breach_rel=0.50)
    d = grade_metric("calmar", 1.0, 2.0, tol)
    assert d.level is DeviationLevel.BREACH
    assert d.rel_error == pytest.approx(-1.0)


def test_grade_metric_near_zero_expected_uses_absolute_thresholds():
    tol = Tolerance(watch_abs=0.05, breach_abs=0.10, abs_floor=1e-3)
    d = grade_metric("annualized_return", 0.0, 0.06, tol)
    assert d.level is DeviationLevel.WATCH
    d2 = grade_metric("annualized_return", 0.0, 0.12, tol)
    assert d2.level is DeviationLevel.BREACH


# --- grade_deviation --------------------------------------------------------------

def test_grade_deviation_skips_unmeasured_and_takes_worst():
    expected = MetricSet(annualized_return=0.15, max_drawdown=-0.20, calmar=0.75)
    actual = MetricSet(annualized_return=0.148, max_drawdown=-0.40, calmar=None)
    a = grade_deviation(expected, actual)
    assert [d.metric for d in a.deviations] == ["annualized_return", "max_drawdown"]
    assert a.overall is DeviationLevel.BREACH  # from max_drawdown
    assert len(a.by_level(DeviationLevel.BREACH)) == 1


def test_grade_deviation_empty_when_nothing_measured():
    a = grade_deviation(MetricSet(), MetricSet(annualized_return=0.1))
    assert a.deviations == ()
    assert a.overall is DeviationLevel.WITHIN_TOLERANCE


# --- update_belief ------------------------------------------------------------------

def test_breach_downgrades_and_records_reason():
    b = Belief(confidence=0.9)
    b2 = update_belief(b, DeviationLevel.BREACH, "ann ret collapsed", "2026-08-02")
    assert b.confidence == 0.9          # input untouched (pure)
    assert b2.confidence == pytest.approx(0.6)
    assert len(b2.history) == 1
    u = b2.history[0]
    assert u.from_confidence == 0.9
    assert u.to_confidence == pytest.approx(0.6)
    assert u.trigger == "breach"
    assert u.reason == "ann ret collapsed"
    assert u.ts == "2026-08-02"


def test_watch_downgrades_less_than_breach():
    b = Belief(confidence=1.0)
    assert update_belief(b, DeviationLevel.WATCH, "r", "t").confidence == pytest.approx(0.9)
    assert update_belief(b, DeviationLevel.BREACH, "r", "t").confidence == pytest.approx(0.7)


def test_within_tolerance_leaves_belief_unchanged():
    b = Belief(confidence=0.5)
    b2 = update_belief(b, DeviationLevel.WITHIN_TOLERANCE, "", "t")
    assert b2 is b  # no change, no spurious history entry


def test_belief_floors_at_zero_and_history_appends():
    b = Belief(confidence=0.2)
    b2 = update_belief(b, DeviationLevel.BREACH, "first", "t1")
    assert b2.confidence == 0.0
    b3 = update_belief(b2, DeviationLevel.BREACH, "second", "t2")
    assert b3.confidence == 0.0
    assert [u.reason for u in b3.history] == ["first", "second"]


# --- review_strategy ------------------------------------------------------------------

def test_review_strategy_composes_reason_naming_offenders():
    sr = review_strategy(
        strategy_id="s1",
        lifecycle_state="paper",
        window="2026-07",
        expected=MetricSet(annualized_return=0.15, max_drawdown=-0.20),
        actual=MetricSet(annualized_return=0.05, max_drawdown=-0.21),
        belief=Belief(confidence=1.0),
        ts="2026-08-02",
    )
    assert sr.assessment.overall is DeviationLevel.BREACH
    assert sr.belief.confidence == pytest.approx(0.7)
    reason = sr.belief.history[0].reason
    assert "annualized_return" in reason
    assert "0.15" in reason and "0.05" in reason
    assert "[2026-07]" in reason


def test_review_strategy_within_tolerance_no_history():
    sr = review_strategy(
        strategy_id="s1",
        lifecycle_state="paper",
        window="w",
        expected=MetricSet(calmar=1.0),
        actual=MetricSet(calmar=0.98),
    )
    assert sr.belief.history == ()
    assert sr.belief.confidence == 1.0


# --- render_html ------------------------------------------------------------------

def _sample_report() -> SelfReviewReport:
    sr = review_strategy(
        strategy_id="cash_carry_combo_v1_20260731",
        lifecycle_state="paper",
        window="2026-07",
        expected=MetricSet(annualized_return=0.147, max_drawdown=-0.179, calmar=0.823),
        actual=MetricSet(annualized_return=0.06, max_drawdown=-0.30, calmar=0.20),
        belief=Belief(confidence=0.7),
        ts="2026-08-02",
        decay_status="alive",
        notes=("paper runner 974 天数据已跑通",),
    )
    lesson = Lesson(
        lesson_id="L1",
        date="2026-08-01",
        strategy_id="cash_carry_combo_v1_20260731",
        event="funding carry 前视偏差：A模型 +45.7bp PF2.04 vs B模型 -17.6bp PF0.76",
        lesson="双口径复算；口径剧烈分歧即前视嫌疑，悲观口径为先验",
    )
    return SelfReviewReport(
        title="自省报告 #1",
        generated_at="2026-08-02T00:00:00+00:00",
        window="2026-07",
        strategies=(sr,),
        lessons=(lesson,),
    )


def test_render_html_self_contained_and_complete():
    html = render_html(_sample_report())
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html and "http" not in html.split("<body>")[0].replace(
        "utf-8", ""
    )  # no external assets in head
    # strategy block
    assert "cash_carry_combo_v1_20260731" in html
    assert "paper" in html
    assert "14.70%" in html          # expected annualized formatted
    assert "6.00%" in html           # actual annualized formatted
    assert "越限" in html            # breach lamp label
    # belief trajectory with reason
    assert "信念变化轨迹" in html
    assert "annualized_return" in html
    # decay + notes
    assert "alive" in html
    assert "974 天" in html
    # lessons table
    assert "L1" in html
    assert "前视偏差" in html
    assert "PF0.76" in html


def test_render_html_escapes_injection():
    sr = StrategyReview(
        strategy_id="s<script>alert(1)</script>",
        lifecycle_state="paper",
        window="w",
        expected=MetricSet(),
        actual=MetricSet(),
        assessment=grade_deviation(MetricSet(), MetricSet()),
        belief=Belief(),
    )
    report = SelfReviewReport(
        title="t", generated_at="g", window="w", strategies=(sr,), lessons=()
    )
    html = render_html(report)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_html_empty_belief_history_shows_placeholder():
    sr = review_strategy(
        strategy_id="s",
        lifecycle_state="backtesting",
        window="w",
        expected=MetricSet(),
        actual=MetricSet(),
    )
    report = SelfReviewReport(
        title="t", generated_at="g", window="w", strategies=(sr,), lessons=()
    )
    assert "无信念变化记录" in render_html(report)
