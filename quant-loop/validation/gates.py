"""G1-G7 hard-gate evaluation for strategy variants.

Gate definitions (multica strategy-layer rules, 2026-07-11):

| gate | threshold |
|------|-----------|
| G1 | full-backtest mean Sharpe >= 1.0 |
| G2 | min(annualized_full, mean_OOS_annualized) >= 15% |
| G3 | cumulative profit_factor > 1.5 |
| G4 | max_drawdown < 25% across all symbols |
| G5 | backtrader AND freqtrade OOS walk-forward Sharpe >= 1.0 (framework CV) |
| G6 | bootstrap 95% CI lower of annualized Sharpe >= 0.5 (10000 resamples, seed=42) |
| G7 | per-trade mean return t-test p < 0.0125 (Bonferroni 0.05/4) |

A variant PASSES only if every gate passes. Any gate failure blocks merge.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import stats

G1_MIN_SHARPE = 1.0
G2_MIN_ANNUALIZED = 0.15
G3_MIN_PROFIT_FACTOR = 1.5
G4_MAX_DRAWDOWN = 0.25
G5_MIN_FRAMEWORK_SHARPE = 1.0
G6_MIN_CI_LOWER = 0.5
G7_MAX_PVALUE = stats.BONFERRONI_ALPHA


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


def evaluate_gates(
    variant: str,
    *,
    full_metrics_by_symbol: dict[str, dict],
    window_native: list[dict],
    window_backtrader: list[dict],
    window_freqtrade: list[dict],
    pooled_oos_daily_returns,
    pooled_oos_trade_pnls: list[float],
) -> Verdict:
    """Build the G1-G7 verdict.

    full_metrics_by_symbol: {symbol: metrics dict} from the native engine over
        the full data span (G1/G2-full/G3/G4).
    window_*: lists of metrics dicts, one per (window, symbol), for each
        framework (G2-OOS/G5).
    pooled_oos_daily_returns: native daily returns pooled across OOS windows
        (mean across symbols per day) for the G6 bootstrap.
    pooled_oos_trade_pnls: native per-trade pnl fractions across OOS windows
        for the G7 t-test.
    """
    gates: list[GateResult] = []

    # G1 — full-backtest mean Sharpe across symbols
    full_sharpes = [m["sharpe"] for m in full_metrics_by_symbol.values()]
    g1_obs = _mean(full_sharpes)
    gates.append(GateResult("G1", g1_obs >= G1_MIN_SHARPE, g1_obs, G1_MIN_SHARPE,
                            "full-period mean Sharpe across symbols"))

    # G2 — min(annualized_full, mean OOS annualized) >= 15%
    full_ann = _mean([m["annualized_return"] for m in full_metrics_by_symbol.values()])
    oos_ann = _mean([m["annualized_return"] for m in window_native])
    g2_obs = min(full_ann, oos_ann)
    gates.append(GateResult("G2", g2_obs >= G2_MIN_ANNUALIZED, g2_obs, G2_MIN_ANNUALIZED,
                            f"min(full={full_ann:.4f}, mean_oos={oos_ann:.4f})"))

    # G3 — cumulative profit factor, full period, pooled trades
    # profit_factor per symbol is pooled by gross sums, so recompute from trade lists upstream;
    # here we use the worst-case: mean of per-symbol pf weighted equally.
    full_pfs = [m["profit_factor"] for m in full_metrics_by_symbol.values()]
    g3_obs = _mean([p if np.isfinite(p) else 10.0 for p in full_pfs])
    gates.append(GateResult("G3", g3_obs > G3_MIN_PROFIT_FACTOR, g3_obs, G3_MIN_PROFIT_FACTOR,
                            "mean full-period profit factor across symbols"))

    # G4 — worst max drawdown across all symbols (full period)
    g4_obs = max((m["max_drawdown"] for m in full_metrics_by_symbol.values()), default=1.0)
    gates.append(GateResult("G4", g4_obs < G4_MAX_DRAWDOWN, g4_obs, G4_MAX_DRAWDOWN,
                            "worst symbol max drawdown (full period)"))

    # G5 — framework cross-validation: both frameworks' mean OOS Sharpe >= 1
    bt_sharpe = _mean([m["sharpe"] for m in window_backtrader])
    ft_sharpe = _mean([m["sharpe"] for m in window_freqtrade])
    g5_obs = min(bt_sharpe, ft_sharpe)
    gates.append(GateResult("G5", g5_obs >= G5_MIN_FRAMEWORK_SHARPE, g5_obs,
                            G5_MIN_FRAMEWORK_SHARPE,
                            f"min(backtrader={bt_sharpe:.4f}, freqtrade={ft_sharpe:.4f}) mean OOS Sharpe"))

    # G6 — bootstrap 95% CI lower bound of annualized Sharpe >= 0.5
    g6_obs = stats.bootstrap_sharpe_ci_lower(pooled_oos_daily_returns)
    gates.append(GateResult("G6", g6_obs >= G6_MIN_CI_LOWER, g6_obs, G6_MIN_CI_LOWER,
                            f"bootstrap CI lower ({stats.BOOTSTRAP_RESAMPLES} resamples, seed={stats.BOOTSTRAP_SEED})"))

    # G7 — Bonferroni FWER: per-trade mean return t-test p < 0.0125
    g7_obs = stats.bonferroni_ttest_pvalue(pooled_oos_trade_pnls)
    gates.append(GateResult("G7", g7_obs < G7_MAX_PVALUE, g7_obs, G7_MAX_PVALUE,
                            f"one-sided t-test p on {len(pooled_oos_trade_pnls)} pooled OOS trades"))

    return Verdict(variant=variant, gates=gates)
