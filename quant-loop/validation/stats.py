"""Statistical gate helper: bootstrap confidence interval (G6).

G6 — bootstrap 95% CI lower bound of annualized Sharpe >= 0.5.
     Daily portfolio returns are resampled with replacement
     (10000 resamples, seed=42, per the strategy-layer gate spec).

The G7 Bonferroni t-test was retired 2026-07-24 (Phase B unification); G7 is
now the Deflated Sharpe Ratio in _shared/validation/cpcv.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 42

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
