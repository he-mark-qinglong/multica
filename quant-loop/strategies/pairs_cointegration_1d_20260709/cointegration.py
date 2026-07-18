"""Engle-Granger cointegration + OLS hedge ratio primitives (B1 deliverable).

Scope (deliberately narrow — B1 only):
    - `ols_hedge_ratio(y, x)`            : OLS of y on x (and constant) on log-prices.
    - `compute_spread(y, x, beta, alpha)`: spread = log(y) - (alpha + beta * log(x)).
    - `engle_granger_test(y, x, maxlag)` : 2-step EG test (OLS residuals -> ADF).
    - `rolling_hedge_ratio(...)`         : rolling-OLS hedge ratio over a window.
    - `rolling_zscore(spread, window)`   : rolling z-score (mean / std).
    - `half_life(spread)`                : Ornstein-Uhlenbeck mean-reversion half-life.

Design choices:
    - All inputs are 1-D numpy arrays or pd.Series; we coerce to np.ndarray internally
      so the helpers are pure functions usable by the strategy, by tests, and by
      notebook exploration alike.
    - No look-ahead: OLS / EG / z-score are all windowed. Caller passes the slice.
    - We rely on `statsmodels.tsa.stattools.adfuller` for the second step of EG.
      statsmodels gives the MacKinnon p-value for the ADF regression with the chosen
      regression/trend specification — that p-value IS the EG p-value.
    - OLS is hand-rolled (closed form) so we don't pull in a heavy regression object
      just to extract (alpha, beta). It is numerically equivalent to numpy.linalg.lstsq
      and ~10x faster on the rolling window sizes we care about (<= 500 rows).

Naming follows the B2/B3 strategy spec: `pairs_cointegration_1d_20260709`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd

# statsmodels is the only non-stdlib dep beyond numpy/pandas. Imported lazily inside
# `engle_granger_test` so importing this module never requires statsmodels (useful
# when callers only need OLS / z-score).


ArrayLike = Union[np.ndarray, pd.Series]


def _to_1d_float(values: ArrayLike, name: str = "values") -> np.ndarray:
    """Coerce an array-like into a 1-D float64 numpy array; NaN-strip is caller's job."""
    if isinstance(values, pd.Series):
        arr = values.to_numpy(dtype=float)
    else:
        arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name!r} must be 1-D; got shape {arr.shape}")
    if arr.size < 2:
        raise ValueError(f"{name!r} must have at least 2 observations; got {arr.size}")
    return arr


def _align(y: np.ndarray, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Truncate to the shared length and drop rows with NaN in either series.

    EG / OLS both require complete cases. Returning aligned copies keeps callers
    from accidentally mutating their input arrays.
    """
    if y.shape != x.shape:
        raise ValueError(f"y/x shape mismatch: y={y.shape}, x={x.shape}")
    mask = np.isfinite(y) & np.isfinite(x)
    if mask.sum() < 2:
        # Not enough finite observations to fit anything meaningful.
        raise ValueError("fewer than 2 finite observations after alignment")
    return y[mask], x[mask]


# ---------------------------------------------------------------------------
# OLS hedge ratio
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HedgeRatio:
    """OLS fit of y on (1, x). All fields are plain floats for downstream arithmetic."""

    alpha: float   # intercept on log-prices
    beta: float    # slope (units of log(y) per unit of log(x))
    r_squared: float
    n_obs: int


def ols_hedge_ratio(
    y: ArrayLike,
    x: ArrayLike,
    *,
    add_intercept: bool = True,
) -> HedgeRatio:
    """Closed-form OLS of `y` on `x` (with optional intercept).

    Typical use: pass log-prices. With `add_intercept=True` (default), the result is
        log(y_t) = alpha + beta * log(x_t) + epsilon_t
    so spread = log(y) - alpha - beta * log(x).

    Without the intercept (`add_intercept=False`), we fit `log(y) = beta * log(x) + eps`
    — useful when the user wants a pure proportional relationship (e.g. known share
    count ratio between two perpetuals).

    Returns a `HedgeRatio` dataclass; on degenerate input (all-x-equal) we return
    zeros with `r_squared=0.0` rather than raising — the strategy needs to handle
    these gracefully when a constituent drops out of the universe.
    """
    y_arr = _to_1d_float(y, "y")
    x_arr = _to_1d_float(x, "x")
    y_arr, x_arr = _align(y_arr, x_arr)

    if add_intercept:
        X = np.column_stack([np.ones_like(x_arr), x_arr])
    else:
        X = x_arr.reshape(-1, 1)

    # lstsq is the canonical stable solver; we don't actually need the SVD machinery
    # but its singular-value guarding handles the rank-deficient (constant x) edge.
    coef, *_ = np.linalg.lstsq(X, y_arr, rcond=None)

    y_hat = X @ coef
    resid = y_arr - y_hat
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y_arr - y_arr.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    if add_intercept:
        alpha, beta = float(coef[0]), float(coef[1])
    else:
        alpha, beta = 0.0, float(coef[0])

    # Catch the rank-deficient case: np.linalg.lstsq returns 0 coefficients rather
    # than NaN. We surface that as r_squared == 0.0 AND zero out the coefficients
    # so downstream consumers (compute_spread, EG) get a benign "no relationship"
    # answer instead of a tiny floating-point drift.
    if not np.isfinite(alpha) or not np.isfinite(beta) or r2 == 0.0:
        return HedgeRatio(alpha=0.0, beta=0.0, r_squared=0.0, n_obs=int(y_arr.size))

    return HedgeRatio(alpha=alpha, beta=beta, r_squared=float(r2), n_obs=int(y_arr.size))


# ---------------------------------------------------------------------------
# Spread construction
# ---------------------------------------------------------------------------
def compute_spread(
    y: ArrayLike,
    x: ArrayLike,
    beta: float,
    alpha: float = 0.0,
) -> np.ndarray:
    """spread_t = y_t - alpha - beta * x_t (operates element-wise on raw input).

    Caller passes the *fitted* alpha/beta (typically from `ols_hedge_ratio` on a
    lookback window) and applies them to the current bar's prices. We DO NOT refit
    inside this function — that's the caller's responsibility (see rolling helpers).

    For log-price pairs trading, both `y` and `x` should be log(prices); for raw-
    price pairs trading (rare, fragile), pass prices directly.
    """
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    if y_arr.shape != x_arr.shape:
        raise ValueError(f"y/x shape mismatch: y={y_arr.shape}, x={x_arr.shape}")
    return y_arr - float(alpha) - float(beta) * x_arr


# ---------------------------------------------------------------------------
# Engle-Granger 2-step cointegration test
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EGTestResult:
    """Result of the Engle-Granger 2-step cointegration test."""

    p_value: float          # MacKinnon p-value for ADF on OLS residuals
    test_stat: float        # ADF t-statistic (the "tau" statistic)
    hedge_ratio: HedgeRatio # OLS hedge ratio from step 1
    adf_lags: int           # number of lagged-difference terms included in ADF
    n_obs: int              # effective sample size used by the ADF regression

    def is_cointegrated(self, alpha: float = 0.05) -> bool:
        return bool(self.p_value < alpha)


def engle_granger_test(
    y: ArrayLike,
    x: ArrayLike,
    *,
    maxlag: int = 1,
    regression: str = "c",
) -> EGTestResult:
    """Engle-Granger 2-step cointegration test on log-prices.

    Step 1: OLS `y = alpha + beta * x + eps` (in log space); collect residuals.
    Step 2: ADF on the residuals with the chosen `regression` spec and `maxlag`.

    `regression` follows statsmodels conventions:
        "c"  : constant only       (most common; matches the OLS intercept we fit)
        "n"  : no constant/trend   (use when the OLS in step 1 was run without intercept)
        "ct" : constant + trend    (rare for spreads; spreads should be stationary)

    Note: `nc` is accepted as an alias for `n` for backward compatibility with
    earlier statsmodels versions and to match the OLS-side naming convention.

    `maxlag` is the ADF augmentation lag. Pass `0` for the simplest regression
    (just `delta e_t = rho * e_{t-1} + u_t`), which is the canonical EG specification.
    For noisy crypto spreads, lag 1-2 often helps; we leave the choice to the caller.

    Returns `EGTestResult` with the MacKinnon p-value, ADF t-stat, and the OLS
    hedge ratio. Caller decides the significance threshold via `.is_cointegrated()`.
    """
    # Lazy import so callers who only need OLS don't pay statsmodels' load time.
    from statsmodels.tsa.stattools import adfuller

    y_arr = _to_1d_float(y, "y")
    x_arr = _to_1d_float(x, "x")
    y_arr, x_arr = _align(y_arr, x_arr)

    # Map our public "nc" alias to the statsmodels-canonical "n". This keeps
    # callers free to use either spelling; adfuller itself only accepts "n".
    adf_regression = "n" if regression == "nc" else regression
    hedge = ols_hedge_ratio(y_arr, x_arr, add_intercept=(adf_regression != "n"))
    spread = compute_spread(y_arr, x_arr, beta=hedge.beta, alpha=hedge.alpha)

    # statsmodels' adfuller returns: (adf_stat, p_value, usedlag, nobs, crit_values)
    # in this version. Older versions also returned a 6th `icbest` field — we
    # unpack positionally to remain robust to the local statsmodels build.
    adf_out = adfuller(
        spread,
        maxlag=int(maxlag),
        autolag=None,
        regression=adf_regression,
    )
    adf_stat = adf_out[0]
    p_value = adf_out[1]
    usedlag = adf_out[2]
    nobs = adf_out[3]

    return EGTestResult(
        p_value=float(p_value),
        test_stat=float(adf_stat),
        hedge_ratio=hedge,
        adf_lags=int(usedlag),
        n_obs=int(nobs),
    )


# ---------------------------------------------------------------------------
# Rolling helpers
# ---------------------------------------------------------------------------
def rolling_hedge_ratio(
    y: ArrayLike,
    x: ArrayLike,
    *,
    window: int,
    min_periods: Optional[int] = None,
) -> pd.DataFrame:
    """Rolling-OLS hedge ratio (alpha, beta) on a fixed lookback window.

    Returns a DataFrame indexed the same as `y` (Series or index-bearing array) with
    columns: `alpha`, `beta`, `r_squared`. Rows with insufficient history are NaN;
    rows whose OLS degenerated (R^2 == 0) are also NaN so downstream consumers
    can `.dropna()` cleanly.
    """
    y_arr = _to_1d_float(y, "y")
    x_arr = _to_1d_float(x, "x")
    if y_arr.shape != x_arr.shape:
        raise ValueError(f"y/x shape mismatch: y={y_arr.shape}, x={x_arr.shape}")

    if min_periods is None:
        min_periods = window

    n = y_arr.size
    alphas = np.full(n, np.nan)
    betas = np.full(n, np.nan)
    r2s = np.full(n, np.nan)

    # We could vectorize OLS over the rolling windows, but at typical sizes
    # (90d window, 1d bars -> 90 obs) a Python loop is fast enough and easier
    # to audit. Profile if this becomes a hot spot.
    #
    # `min_periods` follows the pandas convention: once `min_periods` finite
    # observations are available, emit a fit; the fit window grows up to `window`.
    # So we start the loop at `min_periods` and slice `max(0, end - window) : end`.
    for end in range(min_periods, n + 1):
        lo = max(0, end - window)
        sl = slice(lo, end)
        ys = y_arr[sl]
        xs = x_arr[sl]
        if (
            np.isfinite(ys).sum() < min_periods
            or np.isfinite(xs).sum() < min_periods
        ):
            continue
        try:
            hedge = ols_hedge_ratio(ys, xs, add_intercept=True)
        except ValueError:
            continue
        if hedge.r_squared == 0.0:
            # Degenerate (constant input) — don't propagate a zero beta forward.
            continue
        alphas[end - 1] = hedge.alpha
        betas[end - 1] = hedge.beta
        r2s[end - 1] = hedge.r_squared

    index = y.index if isinstance(y, pd.Series) else pd.RangeIndex(n)
    return pd.DataFrame(
        {"alpha": alphas, "beta": betas, "r_squared": r2s},
        index=index,
    )


def rolling_zscore(
    spread: ArrayLike,
    *,
    window: int,
    min_periods: Optional[int] = None,
) -> pd.DataFrame:
    """Rolling z-score of a spread series: (spread - rolling_mean) / rolling_std.

    Returns a DataFrame with columns: `mean`, `std`, `zscore`. We expose all three
    because the strategy may want to inspect std-drift separately from zscore
    (e.g. for the 4-sigma cointegration-break check).

    Index preservation: when `spread` is a pandas Series (or any object with a
    non-default index), the returned DataFrame inherits that index. Plain
    numpy arrays get a RangeIndex(0..n-1).
    """
    if isinstance(spread, pd.Series):
        index = spread.index
        arr = spread.to_numpy(dtype=float)
    else:
        index = None
        arr = np.asarray(spread, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"spread must be 1-D; got shape {arr.shape}")
    if min_periods is None:
        min_periods = window

    s = pd.Series(arr, index=index)
    mean = s.rolling(window=window, min_periods=min_periods).mean()
    std = s.rolling(window=window, min_periods=min_periods).std(ddof=0)
    # ddof=0 mirrors population-std convention used by the original VPVR strategy.
    # Switching ddof would change every downstream threshold; don't do it without
    # re-running the WF analysis.

    zscore = (s - mean) / std.replace(0.0, np.nan)
    out = pd.DataFrame({"mean": mean, "std": std, "zscore": zscore})
    return out


# ---------------------------------------------------------------------------
# Half-life of mean reversion (Ornstein-Uhlenbeck diagnostic)
# ---------------------------------------------------------------------------
def half_life(spread: ArrayLike) -> float:
    """Estimate the OU mean-reversion half-life from the spread's AR(1) fit.

    Regress `delta s_t = a + b * s_{t-1} + u_t` by OLS; the half-life is
    `-log(2) / log(1 + b)`. `b` is negative for a mean-reverting series and the
    resulting half-life is positive.

    Returns `np.inf` when the series is non-stationary (`1 + b >= 1`, including the
    near-unit-root case `|b| < 1e-6` where finite-sample OLS noise on a random walk
    gives `1 + b` fractionally above 1) and `np.nan` when the input is degenerate
    (constant or too short).

    This is purely a diagnostic — it does NOT feed the B1 deliverables directly,
    but B2 will want it to size the entry/exit z-score thresholds and to detect
    cointegration breaks in backtests.
    """
    arr = _to_1d_float(spread, "spread")
    if arr.size < 3:
        return float("nan")
    s_lag = arr[:-1]
    s_diff = np.diff(arr)
    # OLS: s_diff = a + b * s_lag
    X = np.column_stack([np.ones_like(s_lag), s_lag])
    coef, *_ = np.linalg.lstsq(X, s_diff, rcond=None)
    b = float(coef[1])
    if not np.isfinite(b) or (1.0 + b) <= 0.0 or (1.0 + b) >= 1.0:
        return float("inf")
    # Near-unit-root guard: finite-sample OLS on a true random walk returns b ≈ 0
    # with sampling noise on the order of 1/sqrt(n). For n=300 sigma=0.01 the
    # sampling std of b is ~0.03; a threshold of 0.05 cleanly separates the
    # AR(1) phi<1 case from the unit-root case. Below the threshold we report
    # inf rather than a meaningless half-life of millions of bars.
    if abs(b) < 0.05:
        return float("inf")
    return float(-np.log(2.0) / np.log(1.0 + b))