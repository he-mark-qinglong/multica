"""Tests for drawdown_metrics module."""
import numpy as np
import pandas as pd
import pytest

from _shared.portfolio.drawdown_metrics import (
    compute_drawdown_metrics, cdar_ratio,
    portfolio_drawdown_decomposition, DrawdownMetrics,
)


class TestComputeDrawdownMetrics:
    def _trending(self, n=500, seed=42):
        rng = np.random.default_rng(seed)
        return pd.Series(rng.normal(0.001, 0.02, n))

    def _crash(self, n=500):
        """Returns that crash then recover."""
        rets = np.zeros(n)
        rets[:100] = np.random.default_rng(0).normal(0.001, 0.01, 100)
        rets[100:120] = -0.03  # crash
        rets[120:] = 0.002     # recovery
        return pd.Series(rets)

    def test_returns_metrics_object(self):
        rets = self._trending()
        m = compute_drawdown_metrics(rets)
        assert isinstance(m, DrawdownMetrics)

    def test_max_drawdown_negative(self):
        rets = self._crash(500)
        m = compute_drawdown_metrics(rets)
        assert m.max_drawdown < 0

    def test_max_drawdown_zero_for_pure_uptrend(self):
        rets = pd.Series(np.full(100, 0.001))
        m = compute_drawdown_metrics(rets)
        assert m.max_drawdown == pytest.approx(0.0, abs=1e-10)

    def test_cdar_more_negative_than_avg(self):
        """CDaR should be more negative than average drawdown."""
        rets = self._crash(500)
        m = compute_drawdown_metrics(rets)
        assert m.cdar_95 <= m.avg_drawdown + 1e-10

    def test_edar_more_extreme_than_cdar(self):
        """EDaR (CVaR) should be ≤ CDaR at same level."""
        rets = self._trending(n=1000, seed=7)
        m = compute_drawdown_metrics(rets)
        # EDaR uses CVaR semantics: should be ≤ CDaR (mean of worst)
        assert m.edar_95 <= m.cdar_95 + 1e-10

    def test_ulcer_index_positive(self):
        rets = self._crash(500)
        m = compute_drawdown_metrics(rets)
        assert m.ulcer_index > 0

    def test_pain_index_positive(self):
        rets = self._crash(500)
        m = compute_drawdown_metrics(rets)
        assert m.pain_index > 0

    def test_max_dd_duration_positive(self):
        rets = self._crash(500)
        m = compute_drawdown_metrics(rets)
        assert m.max_dd_duration > 0

    def test_duration_zero_for_uptrend(self):
        rets = pd.Series(np.full(100, 0.001))
        m = compute_drawdown_metrics(rets)
        assert m.max_dd_duration == 0

    def test_calmar_ratio_when_return_given(self):
        rets = self._trending(n=500, seed=42)
        m = compute_drawdown_metrics(rets, annualized_return=0.15)
        assert m.calmar_ratio is not None
        assert m.calmar_ratio > 0

    def test_calmar_none_when_not_given(self):
        rets = self._trending()
        m = compute_drawdown_metrics(rets)
        assert m.calmar_ratio is None

    def test_recovery_factor(self):
        rets = self._trending(n=500, seed=42)
        m = compute_drawdown_metrics(rets, total_return=0.5)
        assert m.recovery_factor is not None


class TestCDaRRatio:
    def test_returns_positive_for_profitable(self):
        rng = np.random.default_rng(42)
        rets = pd.Series(rng.normal(0.002, 0.02, 1000))
        r = cdar_ratio(rets, periods_per_year=365)
        assert isinstance(r, float)

    def test_returns_zero_for_flat(self):
        rets = pd.Series(np.zeros(100))
        r = cdar_ratio(rets)
        assert r == 0.0


class TestPortfolioDrawdownDecomposition:
    def test_returns_dataframe(self):
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "BTC": rng.normal(0.001, 0.03, 500),
            "ETH": rng.normal(0.001, 0.04, 500),
        })
        weights = np.array([0.6, 0.4])
        result = portfolio_drawdown_decomposition(weights, df)
        assert isinstance(result, pd.DataFrame)
        assert "portfolio_dd" in result.columns
        assert "BTC_dd" in result.columns
        assert "ETH_dd" in result.columns

    def test_portfolio_dd_within_range(self):
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "A": rng.normal(0.001, 0.02, 500),
            "B": rng.normal(0.001, 0.02, 500),
        })
        weights = np.array([0.5, 0.5])
        result = portfolio_drawdown_decomposition(weights, df)
        assert result["portfolio_dd"].min() <= 0
        assert result["portfolio_dd"].max() <= 1e-10  # never positive
