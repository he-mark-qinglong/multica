"""Unit tests for `cointegration.py` (B1 deliverable).

These tests are deliberately self-contained — they synthesize their own price
series so they don't depend on cached parquet data or network access. The
synthetic generators are documented in each test (see e.g. `make_cointegrated_pair`).

Conventions:
    - All tests are deterministic (fixed `seed` per test).
    - Each test asserts one observable property (single-idea per the testing rules).
    - We tolerate Monte-Carlo noise with conservative thresholds (e.g. p<0.05 on a
      truly cointegrated pair; p>0.10 on truly independent random walks).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cointegration import (
    HedgeRatio,
    EGTestResult,
    compute_spread,
    engle_granger_test,
    half_life,
    ols_hedge_ratio,
    rolling_hedge_ratio,
    rolling_zscore,
)


# ---------------------------------------------------------------------------
# Synthetic data generators
# ---------------------------------------------------------------------------
def make_cointegrated_pair(
    n: int = 1000,
    true_beta: float = 1.5,
    true_alpha: float = 0.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
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
    # Stationary AR(1): s_t = phi * s_{t-1} + noise, phi < 1.
    phi = 0.7
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = phi * s[i - 1] + rng.normal(0.0, 0.05)
    log_y = true_alpha + true_beta * log_x + s
    return log_y, log_x


def make_independent_pair(n: int = 500, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Synthesize two independent random walks — should NOT be cointegrated."""
    rng = np.random.default_rng(seed)
    log_x = np.cumsum(rng.normal(0.0, 0.02, size=n))
    log_y = np.cumsum(rng.normal(0.0, 0.02, size=n))
    return log_y, log_x


# ---------------------------------------------------------------------------
# OLS hedge ratio
# ---------------------------------------------------------------------------
class TestOLSHedgeRatio:
    def test_recovers_true_beta_on_cointegrated_pair(self):
        log_y, log_x = make_cointegrated_pair(n=1000, true_beta=1.5, seed=0)
        hedge = ols_hedge_ratio(log_y, log_x)
        # 1000-obs clean synthetic should recover beta to within 5%.
        assert abs(hedge.beta - 1.5) < 0.075
        assert hedge.r_squared > 0.9
        assert hedge.n_obs == 1000

    def test_returns_zero_on_constant_x(self):
        rng = np.random.default_rng(2)
        log_y = np.cumsum(rng.normal(0.0, 0.02, 200))
        log_x = np.full(200, 4.2)  # constant -> OLS is rank-deficient
        hedge = ols_hedge_ratio(log_y, log_x)
        # We surface this gracefully: zero beta, zero r_squared, zero alpha.
        assert hedge.beta == 0.0
        assert hedge.alpha == 0.0
        assert hedge.r_squared == 0.0

    def test_no_intercept_mode(self):
        # With true_alpha=0 the no-intercept OLS is unbiased; verify.
        log_y, log_x = make_cointegrated_pair(
            n=1000, true_beta=0.8, true_alpha=0.0, seed=3
        )
        hedge = ols_hedge_ratio(log_y, log_x, add_intercept=False)
        # No intercept -> alpha must be exactly 0 by construction.
        assert hedge.alpha == 0.0
        # Beta should be close to truth (no intercept bias when true_alpha=0).
        assert abs(hedge.beta - 0.8) < 0.05

    def test_raises_on_mismatched_shapes(self):
        with pytest.raises(ValueError):
            ols_hedge_ratio(np.zeros(10), np.zeros(11))

    def test_raises_on_too_short(self):
        with pytest.raises(ValueError):
            ols_hedge_ratio(np.array([1.0]), np.array([2.0]))


# ---------------------------------------------------------------------------
# Spread construction
# ---------------------------------------------------------------------------
class TestComputeSpread:
    def test_zero_when_perfect_fit(self):
        log_x = np.linspace(0.0, 1.0, 50)
        log_y = 0.5 + 2.0 * log_x  # exact linear relationship
        spread = compute_spread(log_y, log_x, beta=2.0, alpha=0.5)
        np.testing.assert_allclose(spread, 0.0, atol=1e-12)


# ---------------------------------------------------------------------------
# Engle-Granger test
# ---------------------------------------------------------------------------
class TestEngleGrangerTest:
    def test_cointegrated_pair_has_low_p_value(self):
        log_y, log_x = make_cointegrated_pair(n=1000, true_beta=1.5, seed=0)
        result = engle_granger_test(log_y, log_x, maxlag=1)
        assert isinstance(result, EGTestResult)
        # EG should reject the null (no cointegration) at 5%.
        assert result.p_value < 0.05
        assert result.is_cointegrated()
        assert result.test_stat < 0.0  # cointegrated => negative ADF stat

    def test_independent_pair_fails_to_reject(self):
        log_y, log_x = make_independent_pair(n=500, seed=1)
        result = engle_granger_test(log_y, log_x, maxlag=1)
        # Two independent random walks: EG should NOT reject at 5%.
        # We give 10% slack to absorb finite-sample noise.
        assert result.p_value > 0.10

    def test_reports_consistent_hedge_ratio(self):
        log_y, log_x = make_cointegrated_pair(n=1000, true_beta=1.5, seed=0)
        result = engle_granger_test(log_y, log_x, maxlag=1)
        # EG's step-1 OLS beta should match a direct ols_hedge_ratio call.
        direct = ols_hedge_ratio(log_y, log_x)
        assert result.hedge_ratio.beta == pytest.approx(direct.beta, rel=1e-9)

    def test_adf_lag_is_respected(self):
        log_y, log_x = make_cointegrated_pair(n=1000, seed=4)
        result = engle_granger_test(log_y, log_x, maxlag=2)
        assert result.adf_lags <= 2

    def test_no_constant_regression_when_requested(self):
        log_y, log_x = make_cointegrated_pair(n=1000, seed=5)
        result_c = engle_granger_test(log_y, log_x, regression="c")
        result_nc = engle_granger_test(log_y, log_x, regression="nc")
        # The two specs produce different test statistics; we just check they
        # both run and produce finite numbers.
        assert np.isfinite(result_c.test_stat)
        assert np.isfinite(result_nc.test_stat)


# ---------------------------------------------------------------------------
# Rolling hedge ratio
# ---------------------------------------------------------------------------
class TestRollingHedgeRatio:
    def test_warmup_yields_nans_then_fills(self):
        log_y, log_x = make_cointegrated_pair(n=200, seed=6)
        df = rolling_hedge_ratio(log_y, log_x, window=30)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["alpha", "beta", "r_squared"]
        # First 29 rows must be NaN (window=30 needs indices 30..n-1 filled).
        assert df["beta"].iloc[:29].isna().all()
        # After warmup, beta should be close to truth (mean over the post-warmup
        # window — 170 obs on a stable synthetic should converge tightly).
        valid = df["beta"].iloc[29:].dropna()
        assert len(valid) > 100
        assert abs(valid.mean() - 1.5) < 0.15

    def test_handles_pandas_series_input(self):
        log_y, log_x = make_cointegrated_pair(n=80, seed=7)
        ys = pd.Series(log_y)
        xs = pd.Series(log_x, index=ys.index)
        df = rolling_hedge_ratio(ys, xs, window=30)
        # Index is preserved.
        pd.testing.assert_index_equal(df.index, ys.index)

    def test_min_periods_shortens_warmup(self):
        log_y, log_x = make_cointegrated_pair(n=80, seed=8)
        # With window=30, min_periods=30 (default): beta is NaN through row 29.
        df_default = rolling_hedge_ratio(log_y, log_x, window=30)
        # With min_periods=10: beta appears earlier.
        df_early = rolling_hedge_ratio(log_y, log_x, window=30, min_periods=10)
        # The early variant should have strictly more non-NaN betas overall
        # (it starts computing at row 10; the default starts at row 30).
        assert df_early["beta"].notna().sum() > df_default["beta"].notna().sum()


# ---------------------------------------------------------------------------
# Rolling z-score
# ---------------------------------------------------------------------------
class TestRollingZscore:
    def test_basic_shape(self):
        spread = np.cumsum(np.random.default_rng(9).normal(0.0, 0.01, 100))
        df = rolling_zscore(spread, window=20)
        assert list(df.columns) == ["mean", "std", "zscore"]
        assert len(df) == 100
        # First 19 rows are NaN due to rolling warmup.
        assert df["zscore"].iloc[:19].isna().all()

    def test_constant_spread_yields_nan_zscore(self):
        spread = np.full(50, 1.5)
        df = rolling_zscore(spread, window=10)
        # std == 0 -> zscore is NaN (we replace divide-by-zero).
        assert df["zscore"].iloc[9:].isna().all()

    def test_zscore_mean_reverts_around_zero(self):
        # Stationary AR(1) spread -> z-score should oscillate around 0.
        rng = np.random.default_rng(11)
        s = np.zeros(500)
        for i in range(1, 500):
            s[i] = 0.6 * s[i - 1] + rng.normal(0.0, 0.1)
        df = rolling_zscore(s, window=50)
        z = df["zscore"].dropna()
        # Mean of z-score should be near zero (within sampling noise).
        assert abs(z.mean()) < 0.5
        # Standard deviation should be near 1 (definition of z-score).
        assert 0.5 < z.std() < 1.5


# ---------------------------------------------------------------------------
# Half-life of mean reversion
# ---------------------------------------------------------------------------
class TestHalfLife:
    def test_short_half_life_for_strongly_mean_reverting(self):
        # AR(1) with phi=0.5 -> half-life = log(2)/log(1/0.5) = 1.0 bar.
        s = np.zeros(500)
        rng = np.random.default_rng(12)
        for i in range(1, 500):
            s[i] = 0.5 * s[i - 1] + rng.normal(0.0, 0.05)
        hl = half_life(s)
        assert 0.5 < hl < 2.0

    def test_returns_inf_for_random_walk(self):
        # Random walk has b ≈ 0 (sampling noise); our near-unit-root guard
        # treats anything with |b| < 1e-3 as non-stationary.
        s = np.cumsum(np.random.default_rng(13).normal(0.0, 0.01, 300))
        hl = half_life(s)
        assert np.isinf(hl)

    def test_returns_nan_for_constant(self):
        # Constant series -> lstsq returns zero slope, then NaN.
        s = np.full(50, 3.14)
        hl = half_life(s)
        # Either NaN (degenerate) or inf (b=0) is acceptable.
        assert np.isnan(hl) or np.isinf(hl)