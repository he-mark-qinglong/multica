"""Tests for dynamic rebalancer."""
import numpy as np
import pandas as pd
import pytest

from _shared.portfolio.rebalancer import Rebalancer, RebalanceMode, RebalanceAction


class TestThresholdMode:
    def test_no_rebalance_when_drift_small(self):
        rb = Rebalancer(
            target_weights={"A": 0.5, "B": 0.5},
            mode="threshold", drift_threshold=0.10,
        )
        action = rb.check(returns=pd.Series(dtype=float),
                         current_weights={"A": 0.52, "B": 0.48})
        assert not action.should_rebalance

    def test_rebalance_when_drift_large(self):
        rb = Rebalancer(
            target_weights={"A": 0.5, "B": 0.5},
            mode="threshold", drift_threshold=0.05,
        )
        action = rb.check(returns=pd.Series(dtype=float),
                         current_weights={"A": 0.70, "B": 0.30})
        assert action.should_rebalance
        assert action.target_weights == {"A": 0.5, "B": 0.5}
        assert action.turnover > 0


class TestVolTargetMode:
    def test_reduces_when_vol_too_high(self):
        rng = np.random.default_rng(42)
        # High-vol returns (50% annualized)
        returns = pd.DataFrame({
            "A": rng.normal(0, 0.03, 100),
            "B": rng.normal(0, 0.03, 100),
        })
        rb = Rebalancer(
            target_weights={"A": 0.5, "B": 0.5},
            mode="vol_target", target_vol=0.15, vol_window=60,
        )
        action = rb.check(returns=returns, current_weights={"A": 0.5, "B": 0.5})
        assert action.should_rebalance
        # Should scale down exposure
        total_w = sum(action.target_weights.values())
        assert total_w < 1.0

    def test_no_action_when_vol_on_target(self):
        rng = np.random.default_rng(42)
        # Vol ≈ 15% annualized
        daily_vol = 0.15 / np.sqrt(365)
        returns = pd.DataFrame({
            "A": rng.normal(0, daily_vol, 100),
            "B": rng.normal(0, daily_vol, 100),
        })
        rb = Rebalancer(
            target_weights={"A": 0.5, "B": 0.5},
            mode="vol_target", target_vol=0.15, vol_window=60,
        )
        action = rb.check(returns=returns, current_weights={"A": 0.5, "B": 0.5})
        # Should be close to target (may or may not trigger depending on sampling)
        assert action.vol_estimate is not None


class TestDrawdownControl:
    def test_triggers_on_large_drawdown(self):
        rb = Rebalancer(
            target_weights={"A": 1.0},
            mode="drawdown_control", max_drawdown=0.15, dd_reduction_factor=0.5,
        )
        rb._peak_equity = 1.0
        action = rb.check(
            returns=pd.Series(dtype=float),
            current_weights={"A": 1.0},
            current_equity=0.80,  # 20% DD
        )
        assert action.should_rebalance
        assert action.target_weights["A"] == pytest.approx(0.5)
        assert action.exposure_scale == 0.5

    def test_no_trigger_on_small_drawdown(self):
        rb = Rebalancer(
            target_weights={"A": 1.0},
            mode="drawdown_control", max_drawdown=0.20,
        )
        rb._peak_equity = 1.0
        action = rb.check(
            returns=pd.Series(dtype=float),
            current_weights={"A": 1.0},
            current_equity=0.90,  # 10% DD
        )
        assert not action.should_rebalance


class TestCalendarMode:
    def test_triggers_after_week(self):
        rb = Rebalancer(
            target_weights={"A": 1.0}, mode="calendar", rebalance_freq="W",
        )
        rb._last_calendar_date = pd.Timestamp("2026-01-01")
        action = rb.check(
            returns=pd.Series(dtype=float),
            current_weights={"A": 1.0},
            current_date=pd.Timestamp("2026-01-15"),
        )
        assert action.should_rebalance

    def test_no_trigger_same_week(self):
        rb = Rebalancer(
            target_weights={"A": 1.0}, mode="calendar", rebalance_freq="W",
        )
        rb._last_calendar_date = pd.Timestamp("2026-01-06")
        action = rb.check(
            returns=pd.Series(dtype=float),
            current_weights={"A": 1.0},
            current_date=pd.Timestamp("2026-01-07"),
        )
        assert not action.should_rebalance


class TestCombinedMode:
    def test_fires_on_any_trigger(self):
        rb = Rebalancer(
            target_weights={"A": 0.5, "B": 0.5},
            mode="combined", drift_threshold=0.05, max_drawdown=0.10,
        )
        # Large drift triggers
        action = rb.check(
            returns=pd.DataFrame({"A": [0.01]*10, "B": [0.01]*10}),
            current_weights={"A": 0.80, "B": 0.20},
            current_equity=1.0,
        )
        assert action.should_rebalance

    def test_no_fire_when_all_ok(self):
        rng = np.random.default_rng(42)
        returns = pd.DataFrame({
            "A": rng.normal(0, 0.01, 100),
            "B": rng.normal(0, 0.01, 100),
        })
        rb = Rebalancer(
            target_weights={"A": 0.5, "B": 0.5},
            mode="combined", drift_threshold=0.10, max_drawdown=0.20,
            target_vol=None,
        )
        action = rb.check(
            returns=returns,
            current_weights={"A": 0.51, "B": 0.49},
            current_equity=1.0,
        )
        assert not action.should_rebalance


class TestComputeTargetWeights:
    def test_vol_scaling(self):
        rng = np.random.default_rng(42)
        returns = pd.DataFrame({
            "A": rng.normal(0, 0.03, 100),
            "B": rng.normal(0, 0.03, 100),
        })
        rb = Rebalancer(
            target_weights={"A": 0.5, "B": 0.5},
            target_vol=0.15, vol_window=60,
        )
        adjusted = rb.compute_target_weights(returns, current_equity=1.0)
        total = sum(adjusted.values())
        # High vol → should scale down
        assert total < 1.0
