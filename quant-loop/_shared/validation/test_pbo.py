"""Tests for PBO (Probability of Backtest Overfitting)."""
import numpy as np
import pytest

from _shared.validation.pbo import compute_pbo, cscv_pbo, minimum_backtest_length, _rank_logit_transform


class TestRankLogitTransform:
    def test_identity_at_half(self):
        """λ(0.5) should be 0 (logit of 0.5)."""
        result = _rank_logit_transform(np.array([0.5]))
        assert abs(result[0]) < 1e-6

    def test_positive_above_half(self):
        result = _rank_logit_transform(np.array([0.75]))
        assert result[0] > 0

    def test_negative_below_half(self):
        result = _rank_logit_transform(np.array([0.25]))
        assert result[0] < 0

    def test_extreme_values_dont_crash(self):
        result = _rank_logit_transform(np.array([0.001, 0.999]))
        assert np.isfinite(result).all()


class TestComputePBO:
    def test_pbo_zero_for_perfect_consistency(self):
        """If IS-optimal is always OOS-optimal, PBO should be ~0."""
        # 3 strategies, IS and OOS always agree
        is_sharpes = np.array([[2.0, 1.0, 0.5]]).T  # 3x1
        oos_matrix = np.array([[2.0, 1.0, 0.5]]).T  # same
        result = compute_pbo(is_sharpes, oos_matrix)
        assert result["pbo"] == 0.0  # IS-optimal always wins OOS
        assert result["verdict"] == "GENUINE EDGE"

    def test_pbo_one_for_complete_overfit(self):
        """If IS-optimal is always OOS-worst, PBO should be ~1."""
        is_sharpes = np.array([[2.0, 1.0, 0.5]]).T  # IS: strat 0 best
        oos_matrix = np.array([[0.5, 1.0, 2.0]]).T  # OOS: strat 0 worst
        result = compute_pbo(is_sharpes, oos_matrix)
        assert result["pbo"] == 1.0
        assert result["verdict"] == "OVERFIT-DOMINATED"

    def test_returns_all_fields(self):
        is_sharpes = np.array([[1.5, 1.0, 0.5]]).T
        oos_matrix = np.array([[1.4, 0.9, 0.6]]).T
        result = compute_pbo(is_sharpes, oos_matrix)
        assert "pbo" in result
        assert "lambda_distribution" in result
        assert "mean_logit" in result
        assert "median_logit" in result
        assert "n_combinations" in result
        assert "verdict" in result

    def test_multiple_combinations(self):
        """Test with multiple IS/OOS splits."""
        n_strategies = 3
        n_combos = 10
        rng = np.random.default_rng(42)
        is_sharpes = rng.normal(1, 0.5, (n_strategies, n_combos))
        oos_matrix = rng.normal(0.5, 0.5, (n_strategies, n_combos))
        result = compute_pbo(is_sharpes, oos_matrix)
        assert 0 <= result["pbo"] <= 1
        assert result["n_combinations"] == n_combos
        assert len(result["lambda_distribution"]) == n_combos


class TestCSCVPBO:
    def test_runs_on_synthetic_data(self):
        """Run full CSCV on synthetic strategy returns."""
        rng = np.random.default_rng(42)
        # 500 bars, 5 strategies with slight edge
        returns = rng.normal(0.0002, 0.01, (500, 5))
        result = cscv_pbo(returns, n_blocks=8)
        assert 0 <= result["pbo"] <= 1
        assert result["n_combinations"] > 0

    def test_genuine_edge_low_pbo(self):
        """A consistently profitable strategy should have low PBO."""
        rng = np.random.default_rng(42)
        # Strategy 0 has strong edge, others don't
        n = 2000
        returns = np.zeros((n, 4))
        returns[:, 0] = rng.normal(0.002, 0.01, n)  # strong edge
        for i in range(1, 4):
            returns[:, i] = rng.normal(0, 0.01, n)  # no edge
        result = cscv_pbo(returns, n_blocks=8)
        # With strong real edge, PBO should be relatively low
        assert result["pbo"] < 0.7

    def test_overfit_high_pbo(self):
        """Random noise strategies should have PBO near 0.5."""
        rng = np.random.default_rng(42)
        returns = rng.normal(0, 0.01, (500, 10))  # all noise
        result = cscv_pbo(returns, n_blocks=8)
        # For pure noise, PBO should be near 0.5
        assert 0.2 < result["pbo"] < 0.8

    def test_small_data_uses_fewer_blocks(self):
        """Very small data should automatically reduce block count."""
        rng = np.random.default_rng(42)
        returns = rng.normal(0, 0.01, (50, 3))  # tiny
        result = cscv_pbo(returns, n_blocks=16)
        assert result["n_combinations"] > 0  # didn't crash


class TestMinimumBacktestLength:
    def test_returns_positive(self):
        mbl = minimum_backtest_length(n_trials=100, sharpe=1.5)
        assert mbl > 0

    def test_more_trials_need_more_data(self):
        mbl_10 = minimum_backtest_length(n_trials=10, sharpe=1.0)
        mbl_100 = minimum_backtest_length(n_trials=100, sharpe=1.0)
        assert mbl_100 > mbl_10  # more trials → need longer backtest

    def test_higher_sharpe_needs_less_data(self):
        mbl_low = minimum_backtest_length(n_trials=100, sharpe=0.5)
        mbl_high = minimum_backtest_length(n_trials=100, sharpe=2.0)
        assert mbl_high < mbl_low
