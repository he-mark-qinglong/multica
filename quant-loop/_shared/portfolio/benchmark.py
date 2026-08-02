"""Benchmark construction and comparison (I17).

Benchmarks:
  * :func:`buy_and_hold` — single-asset buy & hold (e.g. BTC, ETH),
  * :func:`equal_weight` — daily-rebalanced equal-weight basket.

Comparison statistics via :func:`compare_to_benchmark`: annualized
alpha (Jensen), beta, information ratio, tracking error, correlation,
and up/down capture. Regression-free closed forms (alpha from mean
active return given beta); all statistics are pure functions of the two
return series.

References:
  - Jensen (1968), "The Performance of Mutual Funds in the Period
    1945-1964", Journal of Finance (alpha/beta).
  - Grinold & Kahn (2000), "Active Portfolio Management", Ch. 5
    (information ratio, tracking error).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def buy_and_hold(close: pd.Series) -> pd.Series:
    """Buy & hold equity curve from a close series, normalized to 1.0."""
    if close.empty:
        raise ValueError("close series is empty")
    if (close <= 0).any():
        raise ValueError("close prices must be positive")
    return close / close.iloc[0]


def equal_weight(prices: pd.DataFrame) -> pd.Series:
    """Equal-weight, per-bar-rebalanced basket equity, normalized to 1.0.

    The per-bar rebalanced equal-weight portfolio return is the mean of
    the per-asset bar returns.
    """
    if prices.empty or len(prices.columns) == 0:
        raise ValueError("prices frame is empty")
    rets = prices.pct_change().dropna(how="all").fillna(0.0)
    port = rets.mean(axis=1)
    return (1.0 + port).cumprod()


@dataclass(frozen=True)
class BenchmarkComparison:
    """Strategy-vs-benchmark statistics on aligned return series."""

    n_periods: int
    alpha: float               # annualized Jensen alpha
    beta: float
    correlation: float
    tracking_error: float      # annualized std of active returns
    information_ratio: float   # annualized mean active return / TE
    up_capture: float          # mean strat ret / mean bench ret when bench > 0
    down_capture: float        # same when bench < 0
    active_return: float       # annualized mean active return


def compare_to_benchmark(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    periods_per_year: int = 365,
) -> BenchmarkComparison:
    """Compare a strategy return stream against a benchmark stream.

    Series are aligned on their common index; NaNs dropped pairwise.
    """
    df = pd.concat(
        [strategy_returns.rename("s"), benchmark_returns.rename("b")],
        axis=1,
    ).dropna()
    if len(df) < 2:
        raise ValueError("need at least 2 aligned return observations")
    s, b = df["s"].to_numpy(), df["b"].to_numpy()

    var_b = float(np.var(b, ddof=1))
    beta = float(np.cov(s, b, ddof=1)[0, 1] / var_b) if var_b > 0 else 0.0
    alpha = float((s.mean() - beta * b.mean()) * periods_per_year)

    active = s - b
    te = float(active.std(ddof=1) * np.sqrt(periods_per_year))
    ann_active = float(active.mean() * periods_per_year)
    ir = ann_active / te if te > 0 else 0.0

    corr = float(np.corrcoef(s, b)[0, 1]) if s.std() > 0 and b.std() > 0 else 0.0

    up = b > 0
    down = b < 0
    up_cap = float(s[up].mean() / b[up].mean()) if up.any() and b[up].mean() != 0 else 0.0
    down_cap = float(s[down].mean() / b[down].mean()) if down.any() and b[down].mean() != 0 else 0.0

    return BenchmarkComparison(
        n_periods=len(df),
        alpha=alpha,
        beta=beta,
        correlation=corr,
        tracking_error=te,
        information_ratio=ir,
        up_capture=up_cap,
        down_capture=down_cap,
        active_return=ann_active,
    )
