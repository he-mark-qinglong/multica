"""Tests for Avellaneda-Stoikov parameter calibration (maker_calibrator.py)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.execution.maker_calibrator import (
    CalibratedParams,
    calibrate,
    estimate_gamma_from_pnl,
    estimate_kappa,
    estimate_sigma,
    reservation_price,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_trades(n=1000, avg_interval_sec=5.0, seed=42):
    """Synthetic trade data with Poisson-like inter-arrival times."""
    rng = np.random.default_rng(seed)
    intervals = rng.exponential(scale=avg_interval_sec, size=n)
    timestamps = pd.to_datetime(
        np.cumsum(intervals) * 1e9, unit="ns", origin="unix"
    )
    return pd.DataFrame({
        "timestamp": timestamps,
        "price": 50000.0 + rng.normal(0, 10, n).cumsum(),
        "spread": 1.0,
    })


def _make_returns(n=5000, vol=0.001, seed=42):
    """Synthetic mid-price returns."""
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0, vol, n))


def _make_positions_pnl(n=1000, seed=42):
    """Synthetic positions and PnL for gamma estimation."""
    rng = np.random.default_rng(seed)
    positions = pd.Series(rng.choice([-1.0, 0.0, 1.0], n))
    pnl = pd.Series(positions * rng.normal(0.0001, 0.001, n))
    return positions, pnl


# ---------------------------------------------------------------------------
# estimate_kappa
# ---------------------------------------------------------------------------


class TestEstimateKappa:
    def test_recovers_known_rate(self):
        """For Poisson arrivals with mean interval 5s, kappa ≈ 1/5 = 0.2."""
        trades = _make_trades(n=5000, avg_interval_sec=5.0)
        kappa = estimate_kappa(trades)
        assert kappa == pytest.approx(0.2, rel=0.1)

    def test_single_trade_returns_zero(self):
        trades = pd.DataFrame({"timestamp": [pd.Timestamp("2024-01-01")]})
        assert estimate_kappa(trades) == 0.0

    def test_missing_timestamp_column_raises(self):
        bad = pd.DataFrame({"price": [100.0, 101.0]})
        with pytest.raises(ValueError, match="timestamp"):
            estimate_kappa(bad)

    def test_faster_arrival_higher_kappa(self):
        """More frequent trades → higher kappa."""
        fast = _make_trades(n=2000, avg_interval_sec=1.0, seed=1)
        slow = _make_trades(n=2000, avg_interval_sec=10.0, seed=2)
        k_fast = estimate_kappa(fast)
        k_slow = estimate_kappa(slow)
        assert k_fast > k_slow

    def test_unsorted_timestamps_handled(self):
        """estimate_kappa should sort internally."""
        trades = _make_trades(n=100)
        trades_shuffled = trades.sample(frac=1.0, random_state=99).reset_index(drop=True)
        k1 = estimate_kappa(trades)
        k2 = estimate_kappa(trades_shuffled)
        assert k1 == pytest.approx(k2, rel=0.01)


# ---------------------------------------------------------------------------
# estimate_sigma
# ---------------------------------------------------------------------------


class TestEstimateSigma:
    def test_recovers_known_vol(self):
        """Sigma should recover the input std."""
        returns = _make_returns(n=10000, vol=0.002)
        sigma = estimate_sigma(returns)
        assert sigma == pytest.approx(0.002, rel=0.05)

    def test_empty_returns_zero(self):
        assert estimate_sigma(pd.Series([], dtype=float)) == 0.0

    def test_constant_returns_zero(self):
        assert estimate_sigma(pd.Series([0.0] * 100)) == 0.0


# ---------------------------------------------------------------------------
# estimate_gamma_from_pnl
# ---------------------------------------------------------------------------


class TestEstimateGamma:
    def test_returns_positive_float(self):
        positions, pnl = _make_positions_pnl()
        gamma = estimate_gamma_from_pnl(positions, pnl)
        assert gamma > 0
        assert np.isfinite(gamma)

    def test_higher_target_sharpe_higher_gamma(self):
        positions, pnl = _make_positions_pnl()
        g_low = estimate_gamma_from_pnl(positions, pnl, target_sharpe=0.5)
        g_high = estimate_gamma_from_pnl(positions, pnl, target_sharpe=3.0)
        assert g_high > g_low

    def test_insufficient_data_returns_default(self):
        gamma = estimate_gamma_from_pnl(
            pd.Series([1.0]), pd.Series([0.01])
        )
        assert gamma == 1.0


# ---------------------------------------------------------------------------
# reservation_price
# ---------------------------------------------------------------------------


class TestReservationPrice:
    def test_zero_inventory_equals_mid(self):
        """q=0 → reservation price = mid."""
        r = reservation_price(mid=100.0, q=0.0, sigma=0.01, gamma=1.0)
        assert r == pytest.approx(100.0)

    def test_long_position_lowers_price(self):
        """q > 0 → reservation price < mid (encourages selling)."""
        r = reservation_price(mid=100.0, q=1.0, sigma=0.01, gamma=1.0)
        assert r < 100.0

    def test_short_position_raises_price(self):
        """q < 0 → reservation price > mid (encourages buying)."""
        r = reservation_price(mid=100.0, q=-1.0, sigma=0.01, gamma=1.0)
        assert r > 100.0

    def test_formula_correctness(self):
        """Verify exact formula: r = mid - q * gamma * sigma^2 * T."""
        mid, q, sigma, gamma, T = 50000.0, 2.0, 0.001, 0.5, 1.0
        expected = mid - q * gamma * sigma ** 2 * T
        r = reservation_price(mid, q, sigma, gamma, T)
        assert r == pytest.approx(expected)

    def test_higher_gamma_larger_adjustment(self):
        """More risk-averse → larger price adjustment."""
        r_low = reservation_price(100.0, 1.0, 0.01, 0.1)
        r_high = reservation_price(100.0, 1.0, 0.01, 10.0)
        assert abs(100.0 - r_high) > abs(100.0 - r_low)


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------


class TestCalibrate:
    def test_returns_calibrated_params(self):
        trades = _make_trades(n=1000)
        returns = _make_returns(n=5000)
        result = calibrate(trades, returns)
        assert isinstance(result, CalibratedParams)
        assert result.method == "historical"
        assert result.n_trades == 1000
        assert result.kappa > 0
        assert result.sigma > 0

    def test_calibrate_with_positions_pnl(self):
        trades = _make_trades(n=500)
        returns = _make_returns(n=2000)
        positions, pnl = _make_positions_pnl(n=2000)
        result = calibrate(trades, returns, positions, pnl)
        assert result.gamma > 0

    def test_calibrate_without_positions_pnl(self):
        """Without positions/pnl, gamma defaults to 1.0."""
        trades = _make_trades(n=500)
        returns = _make_returns(n=2000)
        result = calibrate(trades, returns)
        assert result.gamma == 1.0

    def test_calibrated_params_is_frozen(self):
        trades = _make_trades(n=10)
        returns = _make_returns(n=100)
        result = calibrate(trades, returns)
        with pytest.raises(Exception):
            result.gamma = 999.0  # type: ignore
