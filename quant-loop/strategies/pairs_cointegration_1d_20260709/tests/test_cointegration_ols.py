"""Unit tests for the OLS hedge-ratio and spread primitives in `cointegration.py`.

These are the simplest, most-fundamental pieces of the B1 layer. They must
work in isolation because every higher-level signal (rolling hedge, EG
test, z-score) builds on them.
"""
from __future__ import annotations

import numpy as np
import pytest

from cointegration import HedgeRatio, compute_spread, ols_hedge_ratio

from ._synthetic import make_cointegrated_pair


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

    def test_returns_hedgeratio_dataclass(self):
        log_y, log_x = make_cointegrated_pair(n=200, seed=4)
        h = ols_hedge_ratio(log_y, log_x)
        assert isinstance(h, HedgeRatio)
        assert all(isinstance(getattr(h, f), float) for f in ("alpha", "beta", "r_squared"))
        assert isinstance(h.n_obs, int)

    def test_accepts_pandas_series(self):
        import pandas as pd
        log_y, log_x = make_cointegrated_pair(n=200, seed=5)
        h1 = ols_hedge_ratio(log_y, log_x)
        h2 = ols_hedge_ratio(pd.Series(log_y), pd.Series(log_x))
        # OLS is closed-form; same input -> same output regardless of container.
        assert h1.beta == pytest.approx(h2.beta, rel=1e-12)


class TestComputeSpread:
    def test_zero_when_perfect_fit(self):
        log_x = np.linspace(0.0, 1.0, 50)
        log_y = 0.5 + 2.0 * log_x  # exact linear relationship
        spread = compute_spread(log_y, log_x, beta=2.0, alpha=0.5)
        np.testing.assert_allclose(spread, 0.0, atol=1e-12)

    def test_residual_when_imperfect_fit(self):
        log_x = np.linspace(0.0, 1.0, 50)
        log_y = 0.5 + 2.0 * log_x + 0.1  # 0.1 constant offset
        spread = compute_spread(log_y, log_x, beta=2.0, alpha=0.5)
        np.testing.assert_allclose(spread, 0.1, atol=1e-12)

    def test_raises_on_mismatched_shapes(self):
        with pytest.raises(ValueError):
            compute_spread(np.zeros(10), np.zeros(11), beta=1.0, alpha=0.0)
