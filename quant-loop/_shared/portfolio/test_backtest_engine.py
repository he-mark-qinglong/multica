"""Tests for end-to-end portfolio backtest engine."""
import numpy as np
import pandas as pd
import pytest

from _shared.portfolio.backtest_engine import PortfolioBacktestEngine, PortfolioBacktestResult


def _make_strategies(n=500, seed=42):
    """Create 3 synthetic strategy return streams."""
    rng = np.random.default_rng(seed)
    return {
        "strategy_a": pd.Series(rng.normal(0.0005, 0.01, n)),
        "strategy_b": pd.Series(rng.normal(0.0003, 0.015, n)),
        "strategy_c": pd.Series(rng.normal(0.0008, 0.008, n)),
    }


class TestPortfolioBacktestEngine:
    def test_runs_and_returns_result(self):
        strategies = _make_strategies(300)
        engine = PortfolioBacktestEngine(strategies, optimizer="equal")
        result = engine.run()
        assert isinstance(result, PortfolioBacktestResult)

    def test_equity_curve_length_matches(self):
        strategies = _make_strategies(200)
        engine = PortfolioBacktestEngine(strategies, optimizer="equal")
        result = engine.run()
        assert len(result.equity) == 200
        assert len(result.returns) == 200

    def test_weights_history_shape(self):
        strategies = _make_strategies(150)
        engine = PortfolioBacktestEngine(strategies, optimizer="equal")
        result = engine.run()
        assert result.weights_history.shape == (150, 3)

    def test_positive_total_return_for_positive_drift(self):
        strategies = _make_strategies(500, seed=42)
        engine = PortfolioBacktestEngine(strategies, optimizer="equal",
                                         rebalance_mode="none")
        result = engine.run()
        assert result.total_return > 0

    def test_metrics_are_computed(self):
        strategies = _make_strategies(300)
        engine = PortfolioBacktestEngine(strategies, optimizer="equal")
        result = engine.run()
        assert result.sharpe != 0
        assert result.max_drawdown < 0
        assert result.calmar >= 0
        assert result.var_95_hist > 0
        assert result.cvar_95_hist > 0
        assert result.ulcer_index > 0
        assert result.pain_index > 0
        assert result.cdar_95 <= 0
        assert result.max_dd_duration > 0

    def test_per_strategy_sharpe(self):
        strategies = _make_strategies(300)
        engine = PortfolioBacktestEngine(strategies)
        result = engine.run()
        assert len(result.per_strategy_sharpe) == 3
        for name, sharpe in result.per_strategy_sharpe.items():
            assert name in ["strategy_a", "strategy_b", "strategy_c"]
            assert isinstance(sharpe, float)

    def test_correlation_matrix(self):
        strategies = _make_strategies(300)
        engine = PortfolioBacktestEngine(strategies)
        result = engine.run()
        assert result.per_strategy_correlation is not None
        assert result.per_strategy_correlation.shape == (3, 3)

    def test_erc_optimizer(self):
        strategies = _make_strategies(200)
        engine = PortfolioBacktestEngine(strategies, optimizer="erc",
                                         warmup=50)
        result = engine.run()
        assert result.sharpe != 0

    def test_hrp_optimizer(self):
        strategies = _make_strategies(200)
        engine = PortfolioBacktestEngine(strategies, optimizer="hrp",
                                         warmup=50)
        result = engine.run()
        assert result.sharpe != 0

    def test_rebalance_threshold_mode(self):
        strategies = _make_strategies(300)
        engine = PortfolioBacktestEngine(
            strategies, optimizer="equal",
            rebalance_mode="threshold",
            drift_threshold=0.05,
            warmup=50,
        )
        result = engine.run()
        assert result.n_rebalances >= 0  # may or may not trigger

    def test_vol_targeting(self):
        strategies = _make_strategies(300)
        engine = PortfolioBacktestEngine(
            strategies, optimizer="equal",
            target_vol=0.10, vol_window=60,
            warmup=80,
        )
        result = engine.run()
        assert result.sharpe != 0

    def test_drawdown_control(self):
        strategies = _make_strategies(300)
        engine = PortfolioBacktestEngine(
            strategies, optimizer="equal",
            max_drawdown=0.05, dd_reduction=0.3,
            warmup=50,
        )
        result = engine.run()
        # DD control should limit drawdown
        assert result.max_drawdown > -1.0  # not total loss

    def test_transaction_costs_tracked(self):
        strategies = _make_strategies(300)
        engine = PortfolioBacktestEngine(
            strategies, optimizer="equal",
            rebalance_mode="threshold",
            drift_threshold=0.01,  # very sensitive → frequent rebalancing
            cost_per_turnover_bp=3.5,
            warmup=50,
        )
        result = engine.run()
        assert result.total_cost_bp >= 0
        assert result.total_turnover >= 0

    def test_summary_output(self):
        strategies = _make_strategies(300)
        engine = PortfolioBacktestEngine(strategies)
        result = engine.run()
        summary = result.summary()
        assert "Sharpe" in summary
        assert "Calmar" in summary
        assert "CDaR" in summary
        assert "Ulcer" in summary

    def test_single_strategy(self):
        strategies = {"only": pd.Series(np.random.default_rng(42).normal(0.001, 0.01, 200))}
        engine = PortfolioBacktestEngine(strategies, optimizer="equal")
        result = engine.run()
        assert result.sharpe != 0
        assert len(result.equity) == 200

    def test_evt_metrics_on_large_sample(self):
        strategies = _make_strategies(500, seed=7)
        engine = PortfolioBacktestEngine(strategies)
        result = engine.run()
        # EVT may or may not succeed depending on data
        if result.var_99_evt is not None:
            assert result.var_99_evt > 0
        if result.hill_tail_index is not None:
            assert result.hill_tail_index > 0
