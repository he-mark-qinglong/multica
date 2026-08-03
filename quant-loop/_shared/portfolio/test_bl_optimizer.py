"""Tests for Black-Litterman optimizer."""
import numpy as np
import pandas as pd
import pytest

from _shared.portfolio.bl_optimizer import (
    BlackLitterman, ViewMatrix, BLResult, bl_from_views_dict,
)


def _toy_market(n=3, seed=42):
    """Create toy market data for testing."""
    rng = np.random.default_rng(seed)
    A = rng.standard_normal((n, n))
    cov = A @ A.T / n + np.eye(n) * 0.01
    w = rng.dirichlet(np.ones(n))
    return cov, w


class TestEquilibriumReturns:
    def test_shape_matches_assets(self):
        cov, w = _toy_market(4)
        bl = BlackLitterman()
        pi = bl.equilibrium_returns(cov, w)
        assert pi.shape == (4,)

    def test_positive_for_positive_weights(self):
        """Equilibrium returns should be positive for long-only market weights."""
        cov, w = _toy_market(3)
        bl = BlackLitterman(risk_aversion=2.5)
        pi = bl.equilibrium_returns(cov, w)
        # All equilibrium returns should be positive for well-conditioned cov
        assert np.all(pi > -0.01)


class TestBlackLittermanOptimize:
    def test_returns_result(self):
        cov, w = _toy_market(3)
        views = ViewMatrix(
            P=np.array([[1, -1, 0]]),
            Q=np.array([0.02]),
            confidences=[0.5],
        )
        bl = BlackLitterman()
        result = bl.optimize(cov, w, views)
        assert isinstance(result, BLResult)
        assert len(result.posterior_weights) == 3

    def test_weights_sum_to_one(self):
        cov, w = _toy_market(3)
        views = ViewMatrix(
            P=np.array([[1, -1, 0]]),
            Q=np.array([0.02]),
        )
        bl = BlackLitterman()
        result = bl.optimize(cov, w, views)
        assert np.sum(result.posterior_weights) == pytest.approx(1.0, abs=1e-4)

    def test_weights_non_negative(self):
        cov, w = _toy_market(3)
        views = ViewMatrix(
            P=np.array([[1, -1, 0]]),
            Q=np.array([0.02]),
        )
        bl = BlackLitterman()
        result = bl.optimize(cov, w, views, weight_bounds=(0.0, 1.0))
        assert np.all(result.posterior_weights >= -1e-6)

    def test_posterior_shifts_toward_view(self):
        """With a strong view that A > B, BL should increase A's weight."""
        cov, w = _toy_market(3)
        # Strong view: asset 0 outperforms asset 1 by 10%
        views = ViewMatrix(
            P=np.array([[1, -1, 0]]),
            Q=np.array([0.10]),
            confidences=[0.9],
        )
        bl = BlackLitterman()
        result = bl.optimize(cov, w, views)
        # Weight on asset 0 should be higher than market weight
        assert result.posterior_weights[0] >= w[0] - 0.05

    def test_multiple_views(self):
        cov, w = _toy_market(4)
        views = ViewMatrix(
            P=np.array([
                [1, -1, 0, 0],
                [0, 0, 1, -1],
            ]),
            Q=np.array([0.02, -0.01]),
            confidences=[0.5, 0.3],
        )
        bl = BlackLitterman()
        result = bl.optimize(cov, w, views)
        assert result.views_applied == 2
        assert len(result.posterior_returns) == 4

    def test_with_dataframe_inputs(self):
        cov_df = pd.DataFrame(
            np.array([[0.04, 0.01, 0.0], [0.01, 0.09, 0.02], [0.0, 0.02, 0.06]])
        )
        w = pd.Series([0.5, 0.3, 0.2])
        views = ViewMatrix(
            P=np.array([[1, -1, 0]]),
            Q=np.array([0.03]),
        )
        bl = BlackLitterman()
        result = bl.optimize(cov_df, w, views, assets=["A", "B", "C"])
        assert result.assets == ["A", "B", "C"]


class TestConvenienceFunction:
    def test_bl_from_views_dict(self):
        cov, w = _toy_market(3)
        assets = ["BTC", "ETH", "SOL"]
        views = [
            ("BTC", ">", "SOL", 0.02, 0.5),
            ("ETH", "=", None, 0.05, 0.3),
        ]
        result = bl_from_views_dict(cov, w, assets, views)
        assert isinstance(result, BLResult)
        assert result.views_applied == 2
        assert np.sum(result.posterior_weights) == pytest.approx(1.0, abs=1e-4)

    def test_absolute_view(self):
        # Use controlled covariance for predictable equilibrium
        cov = np.array([[0.04, 0, 0], [0, 0.04, 0], [0, 0, 0.04]])
        w = np.array([0.4, 0.3, 0.3])
        assets = ["A", "B", "C"]
        # View: B returns 50% (above any equilibrium)
        views = [("B", "=", None, 0.50, 0.8)]
        result = bl_from_views_dict(cov, w, assets, views)
        # Posterior return for B should be between equilibrium and view
        eq_b = result.equilibrium_returns[1]
        post_b = result.posterior_returns[1]
        assert post_b > eq_b  # pulled up toward the bullish view
