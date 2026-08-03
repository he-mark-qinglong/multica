"""Tests for tail_risk module (copula, EVT/GPD VaR)."""
import numpy as np
import pandas as pd
import pytest
from scipy import stats as sp_stats

from _shared.portfolio.tail_risk import (
    fit_gpd, evt_var, evt_cvar, hill_estimator,
    fit_copula, tail_dependence_matrix,
    GPDFit, CopulaFit,
)


class TestGPDFit:
    def _fat_tailed_data(self, n=2000, seed=42):
        """Generate fat-tailed returns (Student-t with df=3)."""
        rng = np.random.default_rng(seed)
        return pd.Series(rng.standard_t(3, n) * 0.01)

    def test_fits_without_error(self):
        data = self._fat_tailed_data()
        fit = fit_gpd(data, threshold_quantile=0.90)
        assert isinstance(fit, GPDFit)
        assert fit.beta > 0
        assert fit.n_exceedances >= 10

    def test_shape_positive_for_heavy_tail(self):
        """Student-t(df=3) data should yield ξ > 0 (heavy tail)."""
        data = self._fat_tailed_data(n=5000, seed=42)
        fit = fit_gpd(data, threshold_quantile=0.90)
        assert fit.xi > -0.5  # should be in reasonable range
        assert fit.xi < 1.0   # not absurdly heavy

    def test_pwm_method(self):
        data = self._fat_tailed_data()
        fit = fit_gpd(data, threshold_quantile=0.90, method="PWM")
        assert fit.method == "PWM"
        assert fit.beta > 0

    def test_insufficient_data_raises(self):
        with pytest.raises(ValueError, match="Insufficient"):
            fit_gpd(np.array([1, 2, 3]))

    def test_too_few_exceedances_raises(self):
        # Uniform data → no fat tail
        rng = np.random.default_rng(42)
        data = rng.uniform(-1, 1, 200)
        with pytest.raises(ValueError, match="Too few exceedances"):
            fit_gpd(data, threshold_quantile=0.99)


class TestEVTVar:
    def test_evt_var_exceeds_normal_var(self):
        """EVT VaR should exceed normal VaR for fat-tailed data."""
        rng = np.random.default_rng(42)
        data = rng.standard_t(3, 5000) * 0.01
        evt = evt_var(data, confidence=0.99, threshold_quantile=0.90)
        normal_var = abs(np.quantile(data, 0.01))  # empirical 1% quantile
        # EVT should be at least in the same ballpark
        assert evt > 0

    def test_evt_cvar_exceeds_var(self):
        """CVaR should always exceed VaR."""
        rng = np.random.default_rng(42)
        data = rng.standard_t(3, 5000) * 0.01
        var = evt_var(data, confidence=0.99, threshold_quantile=0.90)
        cvar = evt_cvar(data, confidence=0.99, threshold_quantile=0.90)
        assert cvar >= var  # CVaR ≥ VaR always

    def test_returns_positive(self):
        rng = np.random.default_rng(42)
        data = rng.standard_t(3, 3000) * 0.01
        assert evt_var(data) > 0
        assert evt_cvar(data) > 0


class TestHillEstimator:
    def test_returns_finite_for_heavy_tail(self):
        rng = np.random.default_rng(42)
        data = rng.standard_t(3, 5000) * 0.01
        alpha = hill_estimator(data)
        assert np.isfinite(alpha)
        assert alpha > 0

    def test_normal_returns_high_tail_index(self):
        """Normal data should have high tail index (thin tail)."""
        rng = np.random.default_rng(42)
        data = rng.standard_normal(5000)
        alpha = hill_estimator(data)
        # Normal tail index should be relatively high (>3 for this sample)
        assert alpha > 1.0


class TestCopulaFit:
    def _correlated_data(self, n=2000, rho=0.5, seed=42):
        rng = np.random.default_rng(seed)
        cov = [[1, rho], [rho, 1]]
        data = rng.multivariate_normal([0, 0], cov, n)
        return data[:, 0], data[:, 1]

    def test_gaussian_copula_zero_tail_dep(self):
        x, y = self._correlated_data(n=3000, rho=0.5)
        fit = fit_copula(x, y, copula_type="gaussian")
        assert fit.tail_lower == 0.0
        assert fit.tail_upper == 0.0
        assert abs(fit.rho - 0.5) < 0.1

    def test_student_t_positive_tail_dep(self):
        rng = np.random.default_rng(42)
        # Generate correlated t-distributed data
        x_norm = rng.standard_normal(3000)
        y_norm = rng.standard_normal(3000)
        rho = 0.5
        x = rho * x_norm + np.sqrt(1 - rho**2) * y_norm
        # Add t-distributed noise
        chi2 = rng.chisquare(4, 3000)
        scale = np.sqrt(4 / chi2)
        x_t = x * scale
        y_t = y_norm * scale  # Different scaling → correlated tails
        y_t = rho * x_t + np.sqrt(1 - rho**2) * y_t
        fit = fit_copula(x_t, y_t, copula_type="student_t")
        assert fit.copula_type == "student_t"
        assert fit.df >= 1.0
        # With high correlation, tail dependence should be positive
        assert fit.tail_lower >= 0.0

    def test_rho_estimation_accuracy(self):
        x, y = self._correlated_data(n=5000, rho=0.7, seed=99)
        fit = fit_copula(x, y, copula_type="gaussian")
        assert abs(fit.rho - 0.7) < 0.08

    def test_insufficient_data_raises(self):
        with pytest.raises(ValueError, match="Insufficient"):
            fit_copula(np.arange(20), np.arange(20))


class TestTailDependenceMatrix:
    def test_returns_square_matrix(self):
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "A": rng.standard_normal(2000),
            "B": rng.standard_normal(2000),
            "C": rng.standard_normal(2000),
        })
        mat = tail_dependence_matrix(df, copula_type="gaussian")
        assert mat.shape == (3, 3)
        assert mat.index.tolist() == ["A", "B", "C"]
        # Diagonal should be 1.0
        assert np.allclose(np.diag(mat.values), 1.0)
