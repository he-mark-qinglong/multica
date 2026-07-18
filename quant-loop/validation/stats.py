"""Statistical gates: bootstrap confidence interval (G6) and Bonferroni
family-wise significance (G7).

G6 — bootstrap 95% CI lower bound of annualized Sharpe >= 0.5.
     Daily portfolio returns are resampled with replacement
     (10000 resamples, seed=42, per the strategy-layer gate spec).
G7 — one-sample one-sided t-test of per-trade returns > 0 must reach
     p < 0.0125 (Bonferroni alpha = 0.05 / 4 family-wise correction).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 42
BONFERRONI_ALPHA = 0.0125

_TRADING_DAYS = 365


def bootstrap_sharpe_ci_lower(
    daily_ret: pd.Series,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> float:
    """Lower bound of the (1-alpha) bootstrap CI for annualized Sharpe."""
    r = np.asarray(daily_ret, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 10:
        return 0.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, r.size, size=(resamples, r.size))
    samples = r[idx]
    means = samples.mean(axis=1)
    stds = samples.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpes = np.where(stds > 0, means / stds * np.sqrt(_TRADING_DAYS), 0.0)
    return float(np.quantile(sharpes, alpha / 2.0))


def bonferroni_ttest_pvalue(trade_pnls: list[float]) -> float:
    """One-sided p-value of H1: mean per-trade return > 0 (scipy t-test)."""
    from scipy import stats as scipy_stats

    r = np.asarray(trade_pnls, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 3:
        return 1.0
    res = scipy_stats.ttest_1samp(r, popmean=0.0, alternative="greater")
    return float(res.pvalue)
