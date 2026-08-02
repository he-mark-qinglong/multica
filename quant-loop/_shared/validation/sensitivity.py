"""Parameter sensitivity analysis for strategies (G18).

One-at-a-time (OAT) parameter perturbation around a baseline parameter
set: each numeric parameter is moved by ``pct_moves`` (default ±10% and
±25%) while all others are held at baseline, and the resulting change in
two metrics — Sharpe and PnL — is converted into an *elasticity*::

    elasticity = (Δmetric / metric_base) / (Δparam / param_base)

taken as the worst-case (largest-|elasticity|) of the two one-sided
estimates, so V-shaped cliffs — where BOTH directions degrade the metric
and a central difference would cancel to zero — are still caught. An
elasticity of 1.0 means the metric moves proportionally with the
parameter; an absolute elasticity above :data:`CLIFF_THRESHOLD` (2.0)
marks a **parameter cliff** — the strategy's edge is concentrated in a
narrow region of parameter space and is likely over-fit rather than
structural.

The strategy under test is any callable::

    strategy(params: Mapping[str, float], data) -> Mapping[str, float]

returning at least ``"sharpe"`` and ``"pnl"`` (extra keys are ignored).
``data`` is forwarded verbatim, so the caller controls look-ahead
hygiene (pass an out-of-sample slice here to measure OOS sensitivity).

Core logic is the pure function :func:`compute_sensitivity`;
:func:`sensitivity_table` renders the ranking as a plain-text table for
reports. All results are frozen dataclasses.

References:
  - Morris (1991), "Factorial Sampling Plans for Preliminary
    Computational Experiments", Technometrics 33(2) — OAT elementary
    effects as a screening measure of parameter influence.
  - Saltelli et al. (2008), "Global Sensitivity Analysis: The Primer",
    Ch. 1 — elasticity/normalised derivative as local sensitivity index.
  - Bailey & López de Prado (2014), "The Deflated Sharpe Ratio", JPM
    40(5) — parameter cliffs as evidence of selection bias / over-fit.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

__all__ = [
    "CLIFF_THRESHOLD",
    "DEFAULT_PCT_MOVES",
    "ParamSensitivity",
    "SensitivityReport",
    "compute_sensitivity",
    "sensitivity_table",
]

CLIFF_THRESHOLD = 2.0
"""|elasticity| above this marks a parameter cliff (over-sensitive)."""

DEFAULT_PCT_MOVES: tuple[float, ...] = (0.10, 0.25)
"""Symmetric relative perturbations applied one parameter at a time."""

_EPS = 1e-12


@dataclass(frozen=True)
class ParamSensitivity:
    """Sensitivity of one metric to one parameter."""

    param: str
    metric: str
    base_value: float            # baseline parameter value
    base_metric: float           # metric at baseline
    elasticity: float            # worst-case one-sided normalised derivative
    is_cliff: bool               # |elasticity| > CLIFF_THRESHOLD
    metric_at_moves: Mapping[float, tuple[float, float]] = field(default_factory=dict)
    """{pct_move: (metric_at_down, metric_at_up)} — audit trail."""


@dataclass(frozen=True)
class SensitivityReport:
    """Full OAT sweep result, ranked by |elasticity| descending."""

    base_params: Mapping[str, float]
    base_metrics: Mapping[str, float]
    pct_moves: tuple[float, ...]
    sensitivities: tuple[ParamSensitivity, ...]   # ranked, most sensitive first

    @property
    def cliffs(self) -> tuple[ParamSensitivity, ...]:
        """Parameter/metric pairs flagged as parameter cliffs."""
        return tuple(s for s in self.sensitivities if s.is_cliff)


def _elasticity(
    metric_base: float,
    metric_down: float,
    metric_up: float,
    pct: float,
) -> float:
    """Worst-case one-sided normalised derivative for one move size.

    One-sided elasticities are

        e_up   = ((metric_up   - metric_base) / metric_base) / (+pct)
        e_down = ((metric_down - metric_base) / metric_base) / (-pct)

    and the larger-|e| of the two is returned. A central difference
    ((up - down) / 2) would cancel exactly for V-shaped parameter cliffs
    (both directions degrade the metric by the same amount), which is the
    failure mode this module exists to detect — hence one-sided.
    Zero-baseline parameters are perturbed by ±pct in absolute terms, so
    the normaliser is still pct.
    """
    if abs(metric_base) < _EPS:
        # Baseline metric ~0: relative change undefined. Report 0 when the
        # metric did not move, else ±inf (sign of the largest deviation)
        # to flag extreme sensitivity.
        if max(abs(metric_up), abs(metric_down)) < _EPS:
            return 0.0
        bigger = metric_up if abs(metric_up) >= abs(metric_down) else metric_down
        return math.copysign(float("inf"), bigger)
    e_up = ((metric_up - metric_base) / metric_base) / pct
    e_down = ((metric_down - metric_base) / metric_base) / (-pct)
    return e_up if abs(e_up) >= abs(e_down) else e_down


def compute_sensitivity(
    strategy: Callable[[Mapping[str, float], object], Mapping[str, float]],
    base_params: Mapping[str, float],
    data: object = None,
    pct_moves: Sequence[float] = DEFAULT_PCT_MOVES,
    metrics: Sequence[str] = ("sharpe", "pnl"),
) -> SensitivityReport:
    """Run the OAT sensitivity sweep. Pure apart from ``strategy`` itself.

    Args:
        strategy: callable ``(params, data) -> mapping`` returning at least
            every name in ``metrics``.
        base_params: baseline parameter set; non-float values are ignored.
        data: opaque payload forwarded to ``strategy`` unchanged.
        pct_moves: relative perturbation sizes (each applied ±).
        metrics: metric keys to measure.

    Returns:
        :class:`SensitivityReport` with sensitivities ranked by
        |elasticity| descending (largest move first, across all pct_moves).
    """
    base_metrics_raw = strategy(dict(base_params), data)
    base_metrics = {m: float(base_metrics_raw[m]) for m in metrics}

    out = []
    for param, base_value in base_params.items():
        if not isinstance(base_value, (int, float)) or isinstance(base_value, bool):
            continue
        base_value = float(base_value)
        # Evaluate each move once per direction, reuse for all metrics.
        evaluated: dict[float, tuple[Mapping[str, float], Mapping[str, float]]] = {}
        for pct in pct_moves:
            down = dict(base_params)
            up = dict(base_params)
            if abs(base_value) < _EPS:
                down[param], up[param] = -pct, pct
            else:
                down[param] = base_value * (1.0 - pct)
                up[param] = base_value * (1.0 + pct)
            evaluated[pct] = (strategy(down, data), strategy(up, data))
        for metric in metrics:
            elasticities = []
            metric_at_moves: dict[float, tuple[float, float]] = {}
            for pct in pct_moves:
                res_down, res_up = evaluated[pct]
                m_down, m_up = float(res_down[metric]), float(res_up[metric])
                metric_at_moves[pct] = (m_down, m_up)
                elasticities.append(
                    _elasticity(base_metrics[metric], m_down, m_up, pct)
                )
            # Report the worst-case (max |elasticity|) across move sizes —
            # a cliff at ±25% matters even if ±10% looks benign.
            elasticity = max(elasticities, key=abs)
            out.append(
                ParamSensitivity(
                    param=param,
                    metric=metric,
                    base_value=base_value,
                    base_metric=base_metrics[metric],
                    elasticity=elasticity,
                    is_cliff=abs(elasticity) > CLIFF_THRESHOLD,
                    metric_at_moves=metric_at_moves,
                )
            )
    out.sort(key=lambda s: -abs(s.elasticity))
    return SensitivityReport(
        base_params=dict(base_params),
        base_metrics=base_metrics,
        pct_moves=tuple(pct_moves),
        sensitivities=tuple(out),
    )


def sensitivity_table(report: SensitivityReport) -> str:
    """Render the sensitivity ranking as a plain-text table.

    Rows are sorted most-sensitive-first (same order as the report).
    Cliff rows are suffixed with ``*CLIFF*``.
    """
    lines = [
        f"{'param':<24} {'metric':<8} {'base':>12} {'elasticity':>12}  flag",
        "-" * 66,
    ]
    for s in report.sensitivities:
        flag = "*CLIFF*" if s.is_cliff else ""
        elast = f"{s.elasticity:>12.3f}" if abs(s.elasticity) != float("inf") else f"{'inf':>12}"
        lines.append(f"{s.param:<24} {s.metric:<8} {s.base_value:>12.4g} {elast}  {flag}")
    lines.append("-" * 66)
    lines.append(
        "baseline metrics: "
        + ", ".join(f"{k}={v:.4g}" for k, v in report.base_metrics.items())
    )
    lines.append(
        f"{len(report.cliffs)} cliff(s) (|elasticity| > {CLIFF_THRESHOLD})"
    )
    return "\n".join(lines)
