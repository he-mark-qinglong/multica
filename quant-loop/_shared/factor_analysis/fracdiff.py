"""Fractional differentiation — stationarity with memory preservation (A4).

Standard first-order differencing (``d = 1``) renders a price series
stationary but destroys all long-range memory — the differenced series no
longer knows *where* it is relative to history.  Fractional differencing
(``0 < d < 1``) applies a weighted lag polynomial whose weights decay
geometrically, achieving stationarity while retaining partial memory of past
levels.  This is critical for mean-reversion factors that depend on the
distance from a historical anchor.

Algorithm (López de Prado 2018, Ch. 4)
--------------------------------------
The fractional differencing operator is::

    ∇^d X_t = Σ_{k=0}^{∞} w_k · X_{t-k}

with weights given by the binomial-series expansion::

    w_0 = 1
    w_k = w_{k-1} · (k - 1 - d) / k     (k ≥ 1)

For ``0 < d < 1`` the weights alternate in sign and decay in magnitude, so a
fixed-width truncation at ``|w_k| < threshold`` (FFD) gives a practical
constant-window approximation.

Stationarity search
-------------------
:func:`optimal_d` searches over ``d ∈ [d_min, d_max]`` for the smallest value
that passes the Augmented Dickey-Fuller test at the chosen significance level,
using ``statsmodels.tsa.stattools.adfuller``.

References
----------
- López de Prado (2018) *Advances in Financial Machine Learning*, Ch. 4
- Hosking (1981) "Fractional differencing", *Biometrika* 68(1)
- Dickey & Fuller (1979) "Distribution of the Estimators for
  Autoregressive Time Series with a Unit Root", *JASA*
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "frac_diff_weights",
    "_compute_weights",
    "frac_diff",
    "frac_diff_ffd",
    "frac_diff_expanding",
    "optimal_d",
]


def frac_diff_weights(d: float, size: int) -> np.ndarray:
    """Binomial-series weights ``w_0 … w_{size-1}`` (NOT reversed).

    Recurrence: ``w_0 = 1``, ``w_k = w_{k-1} · (k-1-d) / k``.
    """
    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")
    w = np.empty(size, dtype=float)
    w[0] = 1.0
    for k in range(1, size):
        w[k] = w[k - 1] * (k - 1 - d) / k
    return w


# ---------------------------------------------------------------------------
# Weight computation
# ---------------------------------------------------------------------------

def _frac_diff_weights(d: float, threshold: float = 1e-5) -> np.ndarray:
    """Binomial-series weights for fractional differencing of order *d*.

    Computes ``w_0 … w_k`` until ``|w_k| < threshold``, then **reverses** the
    array so that the oldest weight is first (suitable for
    ``np.dot(weights, values[t-width+1:t+1])``).

    Parameters
    ----------
    d
        Differencing order (typically ``0 < d < 1``).
    threshold
        Weight-magnitude cutoff below which the series is truncated.

    Returns
    -------
    np.ndarray
        Weights ``[w_{k}, …, w_1, w_0]`` (oldest first).
    """
    w = [1.0]
    k = 1
    while True:
        w_k = w[-1] * (k - 1 - d) / k
        if abs(w_k) < threshold:
            break
        w.append(w_k)
        k += 1
    # Reverse so the oldest weight is first (for dot product with a window).
    return np.array(w[::-1])


# ---------------------------------------------------------------------------
# Fractional differencing
# ---------------------------------------------------------------------------

def frac_diff(
    series: pd.Series,
    d: float,
    threshold: float = 1e-5,
) -> pd.Series:
    """Fixed-Width Fractional Differencing (FFD).

    Truncates the weight sequence once ``|w_k| < threshold``, yielding a
    constant-width rolling weighted sum.

    Parameters
    ----------
    series
        Input series (e.g. log prices).
    d
        Differencing order.  ``d = 0`` returns the input unchanged;
        ``d = 1`` recovers standard first differencing; ``0 < d < 1``
        preserves partial memory.
    threshold
        Weight-magnitude cutoff below which weights are dropped.

    Returns
    -------
    pd.Series
        Fractionally differenced values, aligned to *series*.  NaN for the
        first ``width - 1`` bars (warmup).
    """
    if d < 0:
        raise ValueError(f"d must be >= 0, got {d}")

    name = series.name

    # d = 0 → identity
    if d == 0:
        return series.astype(float).copy()

    # Integer differencing → delegate to pandas
    if d == int(d) and d > 0:
        result = series.astype(float).diff(int(d))
        result.name = name
        return result

    # Fractional
    weights = _frac_diff_weights(d, threshold)
    width = len(weights)

    s = series.astype(float).dropna()
    n = len(s)
    if n == 0:
        return pd.Series(dtype=float, index=series.index, name=name)

    values = s.to_numpy()
    out = np.full(n, np.nan)

    # Cap width at series length (so short series still produce output).
    eff_width = min(width, n)
    eff_weights = weights[-eff_width:]  # take the most recent eff_width weights

    for t in range(eff_width - 1, n):
        out[t] = np.dot(eff_weights, values[t - eff_width + 1: t + 1])

    result = pd.Series(out, index=s.index, name=name)
    return result.reindex(series.index)


# Alias for API compatibility.
frac_diff_ffd = frac_diff


def _compute_weights(d: float, threshold: float = 1e-5) -> np.ndarray:
    """Alias returning weights in natural order ``w_0 … w_k`` (not reversed)."""
    w = [1.0]
    k = 1
    while True:
        w_k = w[-1] * (k - 1 - d) / k
        if abs(w_k) < threshold:
            break
        w.append(w_k)
        k += 1
    return np.array(w)


def frac_diff_expanding(
    series: pd.Series,
    d: float,
    threshold: float = 1e-5,
) -> pd.Series:
    """Expanding-window fractional differencing (lossless).

    Uses all available weights up to each point.  Converges to FFD in
    the tail once the expanding window exceeds the FFD width.
    """
    if d < 0:
        raise ValueError(f"d must be >= 0, got {d}")
    name = series.name
    if d == 0:
        return series.astype(float).copy()
    if d == int(d) and d > 0:
        result = series.astype(float).diff(int(d))
        result.name = name
        return result

    s = series.astype(float).dropna()
    n = len(s)
    if n == 0:
        return pd.Series(dtype=float, index=series.index, name=name)

    values = s.to_numpy()
    out = np.full(n, np.nan)

    for t in range(n):
        w = frac_diff_weights(d, t + 1)
        out[t] = np.dot(w, values[: t + 1])

    result = pd.Series(out, index=s.index, name=name)
    return result.reindex(series.index)


# ---------------------------------------------------------------------------
# Optimal d search (ADF stationarity test)
# ---------------------------------------------------------------------------

def optimal_d(
    series: pd.Series,
    d_range: tuple[float, float] = (0.0, 1.0),
    steps: int = 21,
    adfuller_p_threshold: float = 0.05,
) -> tuple[float, float]:
    """Find the minimum fractional *d* that achieves stationarity.

    Iterates over a grid of ``d`` values from ``d_range[0]`` to
    ``d_range[1]`` (``steps`` evenly-spaced points), fractionally differencing
    the series at each step and testing the ADF null hypothesis of a unit
    root.  Returns the first ``(d, p_value)`` where ``p_value < threshold``.

    Parameters
    ----------
    series
        Input (typically log-close or close).
    d_range
        ``(d_min, d_max)`` for the search grid.
    steps
        Number of candidate *d* values to try.
    adfuller_p_threshold
        Maximum ADF p-value to accept stationarity (default 0.05).

    Returns
    -------
    tuple[float, float]
        ``(d, p_value)`` — the smallest *d* that passes the ADF test, and its
        p-value.  If no *d* passes, returns the last *d* tried and its p-value.
    """
    from statsmodels.tsa.stattools import adfuller

    if isinstance(d_range, (list, np.ndarray)):
        d_values = sorted(d_range)
    else:
        d_values = np.linspace(d_range[0], d_range[1], steps)

    last_d = float(d_values[-1])
    last_p = 1.0

    for d in d_values:
        diffed = frac_diff(series, float(d)).dropna()
        if len(diffed) < 20:
            continue
        try:
            adf_result = adfuller(diffed, autolag="AIC")
            p_value = float(adf_result[1])
        except Exception:
            continue

        last_d = float(d)
        last_p = p_value

        if p_value < adfuller_p_threshold:
            return (float(d), p_value)

    return (last_d, last_p)
