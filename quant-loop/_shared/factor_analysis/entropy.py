"""Entropy-based features for market microstructure and regime detection.

Entropy measures quantify the *predictability* (or lack thereof) of a
return sequence.  High entropy = near-random, unpredictable market —
typical of efficient regimes; low entropy = structured, exploitable
patterns.

Three entropy measures are provided, each as a pure function that can be
used standalone or wrapped into a rolling feature:

- :func:`shannon_entropy` — Shannon entropy of discretised returns.
- :func:`approximate_entropy` — Pincus (1991) ApEn, a regularity
  statistic based on template-vector matching.
- :func:`sample_entropy` — Richman & Moorman (2000) SampEn, a
  bias-corrected ApEn variant.

Rolling wrappers (:func:`rolling_entropy`) turn these into bar-level
features for use in factor pipelines.

References
----------
- Shannon (1948) "A Mathematical Theory of Communication", *Bell System
  Technical Journal*
- Pincus (1991) "Approximate entropy as a measure of system complexity",
  *PNAS*
- Richman & Moorman (2000) "Physiological time-series analysis using
  approximate entropy and sample entropy", *AJP — Heart and Circulatory*
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "shannon_entropy",
    "approximate_entropy",
    "sample_entropy",
    "rolling_entropy",
]

_EPS = 1e-12  # guard against log(0)


# ---------------------------------------------------------------------------
# Shannon entropy
# ---------------------------------------------------------------------------

def shannon_entropy(
    series: pd.Series | np.ndarray,
    n_bins: int = 10,
    base: float = 2.0,
) -> float:
    """Shannon entropy of the empirical distribution of ``series``.

    The series is discretised into ``n_bins`` equal-width bins;
    ``H = -Σ p_i log_base(p_i)`` over non-empty bins.

    Returns
    -------
    float
        Entropy in *bits* (``base=2``) or *nats* (``base=np.e``).
        Normalised to ``[0, log_base(n_bins)]``.
    """
    arr = np.asarray(series, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 2:
        return 0.0
    counts, _ = np.histogram(arr, bins=n_bins, density=False)
    total = counts.sum()
    if total == 0:
        return 0.0
    p = counts[counts > 0] / total
    return float(-np.sum(p * np.log(p) / np.log(base)))


# ---------------------------------------------------------------------------
# Approximate entropy (ApEn)
# ---------------------------------------------------------------------------

def _maxnorm_dist_matrix(embedded: np.ndarray) -> np.ndarray:
    """Chebyshev (max-norm) distance matrix of rows of ``embedded``."""
    diff = embedded[:, None, :] - embedded[None, :, :]
    return np.max(np.abs(diff), axis=2)


def approximate_entropy(
    series: pd.Series | np.ndarray,
    m: int = 2,
    r: float | None = None,
) -> float:
    """Approximate Entropy (ApEn) of order ``m``, tolerance ``r``.

    Parameters
    ----------
    series : array-like
        Input time series (NaNs are dropped).
    m : int
        Embedding dimension (template length).  Typical: 2.
    r : float or None
        Tolerance (filtering) threshold.  If ``None``, defaults to
        ``0.2 × std(series)`` per Pincus (1991).

    Returns
    -------
    float — ``ApEn(m, r) = Φ^m(r) - Φ^{m+1}(r)``.
    """
    arr = np.asarray(series, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 2 * m + 2:
        return np.nan
    if r is None:
        r = 0.2 * np.std(arr, ddof=1)
    if r is None or r <= 0:
        return np.nan

    def _phi(mm: int) -> float:
        embedded = np.array([arr[i : i + mm] for i in range(n - mm + 1)])
        dist = _maxnorm_dist_matrix(embedded)
        counts = np.sum(dist <= r, axis=1)  # includes self
        ratios = counts / (n - mm + 1)
        ratios = ratios[ratios > 0]
        return np.mean(np.log(ratios))

    return float(_phi(m) - _phi(m + 1))


# ---------------------------------------------------------------------------
# Sample entropy (SampEn)
# ---------------------------------------------------------------------------

def sample_entropy(
    series: pd.Series | np.ndarray,
    m: int = 2,
    r: float | None = None,
) -> float:
    """Sample Entropy (SampEn) — bias-corrected ApEn.

    Unlike ApEn, SampEn does not count self-matches, removing the bias
    that makes ApEn length-dependent.

    Returns
    -------
    float — ``SampEn = -ln(A/B)`` where ``A`` = matches of length ``m+1``
    and ``B`` = matches of length ``m``.  ``ln(0)`` guarded to return
    ``np.inf`` (perfectly regular signal).
    """
    arr = np.asarray(series, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n < 2 * m + 2:
        return np.nan
    if r is None:
        r = 0.2 * np.std(arr, ddof=1)
    if r is None or r <= 0:
        return np.nan

    def _count_matches(mm: int) -> int:
        embedded = np.array([arr[i : i + mm] for i in range(n - mm + 1)])
        dist = _maxnorm_dist_matrix(embedded)
        np.fill_diagonal(dist, np.inf)  # exclude self-matches
        return int(np.sum(dist <= r))

    b = _count_matches(m)
    a = _count_matches(m + 1)
    if b == 0:
        return np.inf
    if a == 0:
        return np.inf
    return float(-np.log(a / b))


# ---------------------------------------------------------------------------
# Rolling wrapper
# ---------------------------------------------------------------------------

_ENTROPY_FUNCS = {
    "shannon": shannon_entropy,
    "approximate": approximate_entropy,
    "sample": sample_entropy,
}


def rolling_entropy(
    series: pd.Series,
    window: int = 100,
    method: str = "shannon",
    **kwargs,
) -> pd.Series:
    """Rolling entropy feature.

    Parameters
    ----------
    series : pd.Series
        Input (typically returns or log-returns).
    window : int
        Rolling window length.
    method : str
        ``"shannon"``, ``"approximate"``, or ``"sample"``.
    **kwargs
        Passed to the entropy function (e.g. ``n_bins``, ``m``, ``r``).

    Returns
    -------
    pd.Series — entropy at each bar (NaN during warmup).
    """
    method = method.lower()
    if method not in _ENTROPY_FUNCS:
        raise ValueError(
            f"unknown method '{method}'; choose from {list(_ENTROPY_FUNCS)}")
    func = _ENTROPY_FUNCS[method]
    arr = series.astype(float).to_numpy()

    out = np.full(len(arr), np.nan)
    for i in range(window - 1, len(arr)):
        block = arr[i - window + 1 : i + 1]
        out[i] = func(block, **kwargs)
    return pd.Series(out, index=series.index)
