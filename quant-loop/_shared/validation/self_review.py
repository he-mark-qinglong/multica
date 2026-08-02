"""Periodic self-review report generator (falsification layer).

Compares a strategy's *expected* (backtest) metrics against its *actual*
(paper / live) metrics over a review window, grades the deviation per
metric, and maintains a per-strategy **belief state** — confidence that
the backtest is a valid model of reality. Breaches downgrade the belief
and the reason is recorded; nothing is silently forgotten.

Deviation grading (per metric, relative error in the "worse" direction):

  * ``within_tolerance`` — |rel err| <= ``watch_rel``
  * ``watch``            — ``watch_rel`` < |rel err| <= ``breach_rel``
  * ``breach``           — |rel err| > ``breach_rel``

For ``max_drawdown`` (a negative fraction) "worse" means *deeper* than
expected; for return-like metrics "worse" means *lower* than expected.
Metrics that are ``None`` on either side are skipped ("not measured") —
an absent number is reported as absent, never as zero.

Belief dynamics (:func:`update_belief`):

  * ``breach`` → confidence − ``breach_penalty`` (default 0.30)
  * ``watch``  → confidence − ``watch_penalty``  (default 0.10)
  * ``within_tolerance`` → no change (consistency does not *restore*
    confidence — recovery must be earned by a deliberate re-validation,
    not accrued passively; this matches the workspace falsification norm)

All data objects are frozen dataclasses; the core is pure functions;
:func:`render_html` produces a self-contained HTML report (inline CSS,
no external assets).

References:
  - Bailey & López de Prado (2014), "The Deflated Sharpe Ratio" —
    backtest expectations are optimistic; live divergence is the norm,
    so it must be measured and graded, not assumed away.
  - Harvey & Liu (2015), "Backtesting", JPM 42(1) — monitoring realised
    performance against the backtested distribution.
  - Aronson (2006), "Evidence-Based Technical Analysis", Ch. 6 — belief
    in a rule must be revised on out-of-sample evidence.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

__all__ = [
    "DeviationLevel",
    "MetricSet",
    "Tolerance",
    "MetricDeviation",
    "DeviationAssessment",
    "BeliefUpdate",
    "Belief",
    "Lesson",
    "StrategyReview",
    "SelfReviewReport",
    "grade_metric",
    "grade_deviation",
    "update_belief",
    "review_strategy",
    "render_html",
]

# Metric keys compared between expected and actual MetricSets, in report
# order. ``max_drawdown`` is special-cased (worse = more negative).
_METRIC_KEYS = (
    "annualized_return",
    "max_drawdown",
    "calmar",
    "profit_factor",
    "fill_rate",
)

_METRIC_LABELS = {
    "annualized_return": "年化收益",
    "max_drawdown": "最大回撤",
    "calmar": "Calmar",
    "profit_factor": "盈亏比 PF",
    "fill_rate": "成交率",
}


class DeviationLevel(str, Enum):
    WITHIN_TOLERANCE = "within_tolerance"
    WATCH = "watch"
    BREACH = "breach"


@dataclass(frozen=True)
class MetricSet:
    """A bundle of strategy metrics; ``None`` = not measured.

    ``max_drawdown`` is a negative fraction (``-0.20`` = −20%).
    ``fill_rate`` is a fraction in [0, 1].
    """

    annualized_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    calmar: Optional[float] = None
    profit_factor: Optional[float] = None
    fill_rate: Optional[float] = None


@dataclass(frozen=True)
class Tolerance:
    """Grading thresholds on relative deviation in the worse direction.

    ``rel`` thresholds apply when ``|expected| >= abs_floor``; otherwise
    the absolute thresholds are used (relative error on a near-zero
    expectation is noise, not signal).
    """

    watch_rel: float = 0.25
    breach_rel: float = 0.50
    watch_abs: float = 0.05
    breach_abs: float = 0.10
    abs_floor: float = 1e-9


@dataclass(frozen=True)
class MetricDeviation:
    """Graded deviation of one metric.

    ``rel_error`` is signed: positive = actual is worse than expected.
    """

    metric: str
    expected: float
    actual: float
    rel_error: float
    level: DeviationLevel


@dataclass(frozen=True)
class DeviationAssessment:
    """Per-metric grades plus the overall (worst) level."""

    overall: DeviationLevel
    deviations: Tuple[MetricDeviation, ...] = field(default_factory=tuple)

    def by_level(self, level: DeviationLevel) -> Tuple[MetricDeviation, ...]:
        return tuple(d for d in self.deviations if d.level is level)


@dataclass(frozen=True)
class BeliefUpdate:
    """One recorded change of confidence, with its reason."""

    ts: str                      # ISO date/datetime of the review
    from_confidence: float
    to_confidence: float
    trigger: str                 # DeviationLevel value that caused it
    reason: str


@dataclass(frozen=True)
class Belief:
    """Confidence that the backtest is a valid model of reality.

    ``confidence`` ∈ [0, 1]; ``history`` is append-only — downgrades are
    never erased, only superseded by newer entries.
    """

    confidence: float = 1.0
    history: Tuple[BeliefUpdate, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Lesson:
    """A methodological lesson distilled from a falsified belief."""

    lesson_id: str
    date: str                    # ISO date of the event
    event: str                   # what happened (the falsification)
    lesson: str                  # the methodological takeaway
    strategy_id: str = ""        # "" = workspace-level lesson


@dataclass(frozen=True)
class StrategyReview:
    """One strategy's self-review block."""

    strategy_id: str
    lifecycle_state: str         # LifecycleState value at review time
    window: str                  # human-readable review window
    expected: MetricSet
    actual: MetricSet
    assessment: DeviationAssessment
    belief: Belief               # belief *after* this review's update
    decay_status: Optional[str] = None   # decay_monitor status, if run
    notes: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SelfReviewReport:
    """The full periodic self-review."""

    title: str
    generated_at: str            # ISO datetime
    window: str
    strategies: Tuple[StrategyReview, ...]
    lessons: Tuple[Lesson, ...]


# --- Pure grading core --------------------------------------------------------


def _rel_error(metric: str, expected: float, actual: float) -> float:
    """Signed relative error, positive = worse than expected. Pure."""
    if metric == "max_drawdown":
        # Both negative fractions; worse = deeper (larger magnitude).
        diff = abs(actual) - abs(expected)
    else:
        diff = expected - actual
    if abs(expected) >= 1e-12:
        return diff / abs(expected)
    return diff  # near-zero expectation: fall back to absolute difference


def grade_metric(
    metric: str,
    expected: float,
    actual: float,
    tol: Tolerance = Tolerance(),
) -> MetricDeviation:
    """Grade one expected-vs-actual metric pair. Pure."""
    rel = _rel_error(metric, expected, actual)
    worse = rel  # positive = worse
    if abs(expected) >= tol.abs_floor:
        watch, breach = tol.watch_rel, tol.breach_rel
        mag = abs(worse)
    else:
        watch, breach = tol.watch_abs, tol.breach_abs
        mag = abs(worse)
    if mag > breach:
        level = DeviationLevel.BREACH
    elif mag > watch:
        level = DeviationLevel.WATCH
    else:
        level = DeviationLevel.WITHIN_TOLERANCE
    return MetricDeviation(
        metric=metric,
        expected=expected,
        actual=actual,
        rel_error=rel,
        level=level,
    )


def grade_deviation(
    expected: MetricSet,
    actual: MetricSet,
    tol: Tolerance = Tolerance(),
) -> DeviationAssessment:
    """Grade every measured metric pair; overall = worst level. Pure."""
    deviations = []
    for key in _METRIC_KEYS:
        e = getattr(expected, key)
        a = getattr(actual, key)
        if e is None or a is None:
            continue
        deviations.append(grade_metric(key, e, a, tol))
    order = {
        DeviationLevel.WITHIN_TOLERANCE: 0,
        DeviationLevel.WATCH: 1,
        DeviationLevel.BREACH: 2,
    }
    overall = max(
        (d.level for d in deviations),
        key=lambda lv: order[lv],
        default=DeviationLevel.WITHIN_TOLERANCE,
    )
    return DeviationAssessment(overall=overall, deviations=tuple(deviations))


# --- Belief dynamics -----------------------------------------------------------


def update_belief(
    belief: Belief,
    level: DeviationLevel,
    reason: str,
    ts: str,
    watch_penalty: float = 0.10,
    breach_penalty: float = 0.30,
) -> Belief:
    """Apply one review outcome to a belief state. Pure — returns a new
    :class:`Belief`; the input is untouched.

    ``within_tolerance`` changes nothing (consistency is not evidence of
    validity, only absence of falsification). ``watch``/``breach``
    downgrade and append a :class:`BeliefUpdate` with the reason.
    """
    if level is DeviationLevel.WITHIN_TOLERANCE:
        return belief
    penalty = watch_penalty if level is DeviationLevel.WATCH else breach_penalty
    new_conf = max(0.0, belief.confidence - penalty)
    upd = BeliefUpdate(
        ts=ts,
        from_confidence=belief.confidence,
        to_confidence=new_conf,
        trigger=level.value,
        reason=reason,
    )
    return Belief(confidence=new_conf, history=belief.history + (upd,))


def review_strategy(
    strategy_id: str,
    lifecycle_state: str,
    window: str,
    expected: MetricSet,
    actual: MetricSet,
    belief: Belief = Belief(),
    tol: Tolerance = Tolerance(),
    ts: str = "",
    decay_status: Optional[str] = None,
    notes: Tuple[str, ...] = (),
) -> StrategyReview:
    """Grade one strategy and update its belief. Pure.

    The downgrade reason is auto-composed from the breaching/watching
    metrics so the audit trail always names the offending numbers.
    """
    assessment = grade_deviation(expected, actual, tol)
    if assessment.overall is not DeviationLevel.WITHIN_TOLERANCE:
        offenders = "; ".join(
            f"{d.metric}: expected {d.expected:.4g} vs actual {d.actual:.4g} "
            f"(rel err {d.rel_error:+.1%}, {d.level.value})"
            for d in assessment.deviations
            if d.level is assessment.overall
        )
        reason = f"[{window}] {assessment.overall.value}: {offenders}"
    else:
        reason = ""
    new_belief = update_belief(belief, assessment.overall, reason, ts)
    return StrategyReview(
        strategy_id=strategy_id,
        lifecycle_state=lifecycle_state,
        window=window,
        expected=expected,
        actual=actual,
        assessment=assessment,
        belief=new_belief,
        decay_status=decay_status,
        notes=tuple(notes),
    )


# --- HTML rendering ------------------------------------------------------------

_LEVEL_COLOR = {
    DeviationLevel.WITHIN_TOLERANCE: "#1a7f37",
    DeviationLevel.WATCH: "#b07d00",
    DeviationLevel.BREACH: "#c0392b",
}
_LEVEL_LABEL = {
    DeviationLevel.WITHIN_TOLERANCE: "容差内",
    DeviationLevel.WATCH: "观察",
    DeviationLevel.BREACH: "越限",
}


def _fmt(metric: str, v: Optional[float]) -> str:
    if v is None:
        return "—"
    if metric in ("annualized_return", "max_drawdown", "fill_rate"):
        return f"{v:.2%}"
    return f"{v:.3f}"


def _lamp(level: DeviationLevel) -> str:
    c = _LEVEL_COLOR[level]
    label = _LEVEL_LABEL[level]
    return (
        f'<span style="display:inline-block;width:0.7em;height:0.7em;'
        f'border-radius:50%;background:{c};margin-right:0.35em"></span>{label}'
    )


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _render_strategy(sr: StrategyReview) -> str:
    rows = []
    measured = {d.metric: d for d in sr.assessment.deviations}
    for key in _METRIC_KEYS:
        e = getattr(sr.expected, key)
        a = getattr(sr.actual, key)
        if e is None and a is None:
            continue
        d = measured.get(key)
        lamp = _lamp(d.level) if d is not None else "—"
        rel = f"{d.rel_error:+.1%}" if d is not None else "—"
        rows.append(
            "<tr>"
            f"<td>{_esc(_METRIC_LABELS[key])}</td>"
            f"<td class='num'>{_fmt(key, e)}</td>"
            f"<td class='num'>{_fmt(key, a)}</td>"
            f"<td class='num'>{rel}</td>"
            f"<td>{lamp}</td>"
            "</tr>"
        )
    belief_rows = "".join(
        "<tr>"
        f"<td>{_esc(u.ts)}</td>"
        f"<td class='num'>{u.from_confidence:.2f} → {u.to_confidence:.2f}</td>"
        f"<td>{_esc(_LEVEL_LABEL.get(DeviationLevel(u.trigger), u.trigger))}</td>"
        f"<td>{_esc(u.reason)}</td>"
        "</tr>"
        for u in sr.belief.history
    ) or "<tr><td colspan='4' class='muted'>无信念变化记录</td></tr>"
    notes = "".join(f"<li>{_esc(n)}</li>" for n in sr.notes)
    notes_html = f"<ul class='notes'>{notes}</ul>" if notes else ""
    decay = (
        f"<p><strong>信号衰减检查：</strong>{_esc(sr.decay_status)}</p>"
        if sr.decay_status
        else ""
    )
    return f"""
<section class="strategy">
  <h2>{_esc(sr.strategy_id)}
    <span class="state">{_esc(sr.lifecycle_state)}</span>
    <span class="overall">{_lamp(sr.assessment.overall)}</span>
  </h2>
  <table>
    <thead><tr><th>指标</th><th>预期（回测）</th><th>实际</th>
    <th>相对偏差</th><th>灯号</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p><strong>回测有效性信念（belief）：</strong>{sr.belief.confidence:.2f} / 1.00</p>
  {decay}
  <details><summary>信念变化轨迹（{len(sr.belief.history)} 条）</summary>
    <table>
      <thead><tr><th>时间</th><th>置信度变化</th><th>触发</th><th>原因</th></tr></thead>
      <tbody>{belief_rows}</tbody>
    </table>
  </details>
  {notes_html}
</section>"""


def render_html(report: SelfReviewReport) -> str:
    """Render a :class:`SelfReviewReport` as a self-contained HTML page.
    Pure — no external assets, no network, inline CSS only.
    """
    strategies = "".join(_render_strategy(sr) for sr in report.strategies)
    lessons = "".join(
        "<tr>"
        f"<td>{_esc(ls.lesson_id)}</td>"
        f"<td>{_esc(ls.date)}</td>"
        f"<td>{_esc(ls.strategy_id or '（工作区级）')}</td>"
        f"<td>{_esc(ls.event)}</td>"
        f"<td>{_esc(ls.lesson)}</td>"
        "</tr>"
        for ls in report.lessons
    )
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>{_esc(report.title)}</title>
<style>
  body {{ font-family: -apple-system, "Helvetica Neue", "PingFang SC", sans-serif;
         max-width: 1080px; margin: 2em auto; padding: 0 1em; color: #222; }}
  h1 {{ border-bottom: 2px solid #444; padding-bottom: 0.3em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.6em 0 1.2em; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left;
            vertical-align: top; }}
  th {{ background: #f2f2f2; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .state {{ font-size: 0.6em; background: #eef; border: 1px solid #99c;
            border-radius: 4px; padding: 2px 8px; margin-left: 0.6em;
            vertical-align: middle; }}
  .overall {{ font-size: 0.6em; margin-left: 0.8em; vertical-align: middle; }}
  .muted {{ color: #888; }}
  .notes {{ background: #fffbe6; border: 1px solid #e5d89b; padding: 0.8em 2em; }}
  header p {{ color: #555; }}
  section.strategy {{ margin-bottom: 2.5em; }}
</style>
</head>
<body>
<header>
  <h1>{_esc(report.title)}</h1>
  <p>生成时间：{_esc(report.generated_at)}　·　复盘窗口：{_esc(report.window)}</p>
</header>
{strategies}
<section class="lessons">
  <h2>教训记录（lessons）</h2>
  <table>
    <thead><tr><th>编号</th><th>日期</th><th>策略</th><th>事件</th>
    <th>方法论教训</th></tr></thead>
    <tbody>{lessons}</tbody>
  </table>
</section>
</body>
</html>
"""
