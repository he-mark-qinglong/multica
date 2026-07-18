"""Unit tests for the Engle-Granger 2-step cointegration test in `cointegration.py`.

The EG test is the gate that decides which pairs the strategy trades; it
must reliably reject independent random walks and accept truly
cointegrated pairs. These tests use synthetic data so they're deterministic.
"""
from __future__ import annotations

import numpy as np
import pytest

from cointegration import EGTestResult, engle_granger_test

from ._synthetic import make_cointegrated_pair, make_independent_pair


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
        from cointegration import ols_hedge_ratio
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

    def test_is_cointegrated_helper(self):
        log_y, log_x = make_cointegrated_pair(n=1000, seed=6)
        r = engle_granger_test(log_y, log_x, maxlag=1)
        # Helper with the default 5% threshold matches the dataclass p_value.
        assert r.is_cointegrated(0.05) == (r.p_value < 0.05)
        # A more aggressive threshold (1%) should be more selective.
        if r.p_value < 0.01:
            assert r.is_cointegrated(0.01) is True
        else:
            assert r.is_cointegrated(0.01) is False

    def test_n_obs_matches_input_length(self):
        log_y, log_x = make_cointegrated_pair(n=400, seed=7)
        r = engle_granger_test(log_y, log_x, maxlag=1)
        # The ADF regression uses n - maxlag - 1 (one lag + constant) effective obs.
        # The n_obs on the EGTestResult is the ADF-side sample size.
        assert r.n_obs < 400
        assert r.n_obs > 380  # lag=1 -> at most 3 obs lost.
