"""Shared synthetic-data generators for the B1/B2 test suite.

Single import point for `make_cointegrated_pair`, `make_independent_pair`,
`make_cointegrated_prices`, and `make_coint_break_prices`. Keeping the
generators in one place keeps the per-class test files clean and avoids
divergent random seeds.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def make_cointegrated_pair(
    n: int = 1000,
    true_beta: float = 1.5,
    true_alpha: float = 0.0,
    seed: int = 0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Synthesize (log_y, log_x) that share a cointegrating vector.

    Construction: simulate a stationary AR(1) process `s_t`, then build
        log_x_t = random walk (cumsum of N(0, sigma_x^2))
        log_y_t = true_alpha + true_beta * log_x_t + s_t

    Parameters tuned so OLS recovers `true_beta` within a few percent on 1000 obs:
    sigma_x = 0.05 -> log_x spans a few units; AR(1) noise is tight (std ~0.07).
    """
    rng = np.random.default_rng(seed)
    sigma_x = 0.05
    log_x = np.cumsum(rng.normal(0.0, sigma_x, size=n))
    phi = 0.7
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = phi * s[i - 1] + rng.normal(0.0, 0.05)
    log_y = true_alpha + true_beta * log_x + s
    return log_y, log_x


def make_independent_pair(n: int = 500, seed: int = 1) -> Tuple[np.ndarray, np.ndarray]:
    """Two independent random walks; should NOT be cointegrated."""
    rng = np.random.default_rng(seed)
    log_x = np.cumsum(rng.normal(0.0, 0.02, size=n))
    log_y = np.cumsum(rng.normal(0.0, 0.02, size=n))
    return log_y, log_x


def make_cointegrated_prices(
    n: int = 400,
    true_beta: float = 1.5,
    seed: int = 100,
    ar1_phi: float = 0.5,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Two OHLCV-ish DataFrames whose log-prices are cointegrated.

    We only need `close` columns for the strategy. Index is daily bars.
    """
    rng = np.random.default_rng(seed)
    log_x = np.cumsum(rng.normal(0.0, 0.05, size=n))
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = ar1_phi * s[i - 1] + rng.normal(0.0, 0.05)
    log_y = true_beta * log_x + s

    idx = pd.date_range("2024-01-01", periods=n, freq="1D")
    px_a = np.exp(log_y)
    px_b = np.exp(log_x)
    df_a = pd.DataFrame(
        {"open": px_a, "high": px_a, "low": px_a, "close": px_a, "volume": np.ones(n)},
        index=idx,
    )
    df_b = pd.DataFrame(
        {"open": px_b, "high": px_b, "low": px_b, "close": px_b, "volume": np.ones(n)},
        index=idx,
    )
    return df_a, df_b


def make_coint_break_prices(
    n: int = 400,
    break_at: int = 200,
    true_beta: float = 1.5,
    seed: int = 200,
) -> Tuple[pd.DataFrame, pd.DataFrame, int]:
    """Cointegrated pair with a structural break inserted at `break_at`.

    Returns (df_a, df_b, break_index). At `break_at`, the spread gets a
    permanent +0.5 shock that pushes the z-score above 4σ.
    """
    rng = np.random.default_rng(seed)
    log_x = np.cumsum(rng.normal(0.0, 0.05, size=n))
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = 0.5 * s[i - 1] + rng.normal(0.0, 0.05)
    log_y = true_beta * log_x + s
    log_y[break_at:] += 0.5

    idx = pd.date_range("2024-01-01", periods=n, freq="1D")
    px_a = np.exp(log_y)
    px_b = np.exp(log_x)
    df_a = pd.DataFrame(
        {"open": px_a, "high": px_a, "low": px_a, "close": px_a, "volume": np.ones(n)},
        index=idx,
    )
    df_b = pd.DataFrame(
        {"open": px_b, "high": px_b, "low": px_b, "close": px_b, "volume": np.ones(n)},
        index=idx,
    )
    return df_a, df_b, break_at


__all__ = [
    "make_coint_break_prices",
    "make_cointegrated_pair",
    "make_cointegrated_prices",
    "make_independent_pair",
]
