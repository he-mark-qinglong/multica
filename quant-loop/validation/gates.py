"""G1-G7 hard-gate evaluation for strategy variants.

Unified gate definitions (Phase B, 2026-07-24): the pass/fail decisions are
delegated to ``_shared/gates/enforce.py::certify_metrics`` — the single gate
enforcer. This module only aggregates the harness inputs into the unified
metrics dict and maps the enforcer's verdict back to per-gate GateResults.

| gate | threshold |
|------|-----------|
| G1 | full-backtest mean Sharpe >= 1.0 |
| G2 | min(annualized_full, mean_OOS_annualized) >= 15% |
| G3 | max_drawdown_pct > -0.25 across all symbols (negative convention) |
| G4 | cumulative profit_factor > 1.5 |
| G5 | framework CV (backtrader/freqtrade) mean OOS Sharpe >= 1.0 |
| G6 | bootstrap 95% CI lower of annualized Sharpe >= 0.5 (10000 resamples, seed=42) |
| G7 | Deflated Sharpe Ratio > 0 (Bailey-LdP 2014; replaces the retired Bonferroni t-test) |
| T1 | pooled OOS trades >= 30 |

A variant PASSES only if every gate passes. Any gate failure blocks merge.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from _shared.gates.enforce import GATES as ENFORCE_GATES
from _shared.gates.enforce import certify_metrics
from _shared.validation.cpcv import deflated_sharpe

from . import stats

G1_MIN_SHARPE = 1.0
G2_MIN_ANNUALIZED = 0.15
G3_MAX_DRAWDOWN = -0.25  # negative convention: pass when observed > threshold
G4_MIN_PROFIT_FACTOR = 1.5
G5_MIN_FRAMEWORK_SHARPE = 1.0
G6_MIN_CI_LOWER = 0.5
G7_MIN_DSR = 0.0
T1_MIN_TRADES = 30

# Family size for the DSR multiple-testing hurdle (campaigns typically try
# 100+ variants; matches _shared.gates.enforce.certify_strategy default).
DEFAULT_N_TRIALS = 100


@dataclass
class GateResult:
    gate: str
    passed: bool
    observed: float
    threshold: float
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return f"[{mark}] {self.gate}: observed={self.observed:.4f} threshold={self.threshold} {self.detail}".rstrip()


@dataclass
class Verdict:
    variant: str
    gates: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(g.passed for g in self.gates)

    def summary_lines(self) -> list[str]:
        head = f"OOS validation verdict for {self.variant}: {'PASS' if self.passed else 'FAIL'}"
        return [head] + [g.line() for g in self.gates]


def _mean(values: list[float]) -> float:
    vals = [v for v in values if np.isfinite(v)]
    return float(np.mean(vals)) if vals else 0.0


def _max_dd_negative(m: dict) -> float:
    """Normalize a metrics dict's drawdown to the negative convention.

    Accepts either key (max_drawdown_pct or legacy max_drawdown) and either
    sign; always returns <= 0.
    """
    dd = m.get("max_drawdown_pct", m.get("max_drawdown", 0.0))
    try:
        return -abs(float(dd))
    except (TypeError, ValueError):
        return 0.0


def evaluate_gates(
    variant: str,
    *,
    full_metrics_by_symbol: dict[str, dict],
    window_native: list[dict],
    window_backtrader: list[dict],
    window_freqtrade: list[dict],
    pooled_oos_daily_returns,
    pooled_oos_trade_pnls: list[float],
    n_trials: int = DEFAULT_N_TRIALS,
) -> Verdict:
    """Build the G1-G7 (+T1) verdict by delegating to certify_metrics.

    full_metrics_by_symbol: {symbol: metrics dict} from the native engine over
        the full data span (G1/G2-full/G3/G4).
    window_*: lists of metrics dicts, one per (window, symbol), for each
        framework (G2-OOS/G5/G7).
    pooled_oos_daily_returns: native daily returns pooled across OOS windows
        (mean across symbols per day) for the G6 bootstrap and G7 sample length.
    pooled_oos_trade_pnls: native per-trade pnl fractions across OOS windows
        (T1 trade floor).
    n_trials: family size for the G7 DSR multiple-testing hurdle.
    """
    # G1 — full-backtest mean Sharpe across symbols
    g1_obs = _mean([m["sharpe"] for m in full_metrics_by_symbol.values()])

    # G2 — min(annualized_full, mean OOS annualized)
    full_ann = _mean([m["annualized_return"] for m in full_metrics_by_symbol.values()])
    oos_ann = _mean([m["annualized_return"] for m in window_native])
    g2_obs = min(full_ann, oos_ann)

    # G3 — worst max drawdown across all symbols (full period), negative convention
    g3_obs = min((_max_dd_negative(m) for m in full_metrics_by_symbol.values()), default=-1.0)

    # G4 — mean full-period profit factor across symbols (inf capped at 10)
    full_pfs = [m["profit_factor"] for m in full_metrics_by_symbol.values()]
    g4_obs = _mean([p if np.isfinite(p) else 10.0 for p in full_pfs])

    # G5 — framework cross-validation: worst framework mean OOS Sharpe >= 1.
    # NaN when no framework windows were run -> enforce.py skips G5.
    framework_means = []
    if window_backtrader:
        framework_means.append(_mean([m["sharpe"] for m in window_backtrader]))
    if window_freqtrade:
        framework_means.append(_mean([m["sharpe"] for m in window_freqtrade]))
    g5_obs = min(framework_means) if framework_means else float("nan")

    # G6 — bootstrap 95% CI lower bound of annualized Sharpe
    g6_obs = stats.bootstrap_sharpe_ci_lower(pooled_oos_daily_returns)

    # G7 — Deflated Sharpe Ratio on the mean native OOS Sharpe.
    daily = np.asarray(list(pooled_oos_daily_returns), dtype=float)
    sample_len = int(np.isfinite(daily).sum())
    oos_native_sharpe = _mean([m["sharpe"] for m in window_native])
    if sample_len >= 2 and window_native:
        g7_obs = deflated_sharpe(oos_native_sharpe, n_trials, sample_len)
    else:
        g7_obs = float("-inf")

    # T1 — pooled OOS trade floor
    t1_obs = float(len(pooled_oos_trade_pnls))

    unified = {
        "sharpe_daily": g1_obs,
        "annualized_return": g2_obs,
        "max_drawdown_pct": g3_obs,
        "profit_factor": g4_obs,
        "cpcv_mean_oos_sharpe": g5_obs,
        "bootstrap_ci95_lower": g6_obs,
        "deflated_sharpe": g7_obs,
        "n_trades": t1_obs,
    }
    result = certify_metrics(unified, strict=False)

    observed = {
        "G1": (g1_obs, G1_MIN_SHARPE, "full-period mean Sharpe across symbols"),
        "G2": (g2_obs, G2_MIN_ANNUALIZED, f"min(full={full_ann:.4f}, mean_oos={oos_ann:.4f})"),
        "G3": (g3_obs, G3_MAX_DRAWDOWN, "worst symbol max drawdown (full period, negative convention)"),
        "G4": (g4_obs, G4_MIN_PROFIT_FACTOR, "mean full-period profit factor across symbols"),
        "G5": (g5_obs, G5_MIN_FRAMEWORK_SHARPE,
               "worst framework mean OOS Sharpe"
               + ("" if framework_means else " (no framework windows, gate skipped)")),
        "G6": (g6_obs, G6_MIN_CI_LOWER,
               f"bootstrap CI lower ({stats.BOOTSTRAP_RESAMPLES} resamples, seed={stats.BOOTSTRAP_SEED})"),
        "G7": (g7_obs, G7_MIN_DSR,
               f"Deflated Sharpe Ratio (Bailey-LdP 2014), n_trials={n_trials}, sample_len={sample_len}"),
        "T1": (t1_obs, T1_MIN_TRADES, "pooled OOS trades"),
    }

    gates: list[GateResult] = []
    for gid, _name, _fn, _desc in ENFORCE_GATES:
        obs, threshold, detail = observed[gid]
        gates.append(GateResult(gid, gid not in result.failed_gates, obs, threshold, detail))

    return Verdict(variant=variant, gates=gates)
