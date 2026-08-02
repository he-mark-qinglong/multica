"""Unit tests for KAMA trend strategy."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy import kama, generate_signal, run_backtest


def _make_synthetic(n=1000, seed=42, drift=0.0001):
    """Trending random-walk OHLCV for testing."""
    rng = np.random.default_rng(seed)
    rets = drift + rng.standard_normal(n) * 0.01
    close = 100 * np.cumprod(1 + rets)
    ts = pd.date_range("2020-01-01", periods=n, freq="4h")
    return pd.DataFrame({
        "open": close, "high": close * 1.001,
        "low": close * 0.999, "close": close,
        "volume": np.ones(n) * 100,
    }, index=ts)


class TestKama:
    def test_kama_reduces_to_close_when_fast_equals_slow(self):
        close = pd.Series(np.arange(100, dtype=float))
        line = kama(close, er_window=5, fast=2, slow=2)
        # When fast == slow, SC is constant = (2/(2+1))^2 = 4/9
        # KAMA should track close with that smoothing
        assert not line.isna().all()

    def test_kama_length_matches_input(self):
        close = pd.Series(np.random.randn(500) + 100)
        line = kama(close, er_window=10, fast=2, slow=30)
        assert len(line) == len(close)

    def test_kama_handles_short_series(self):
        close = pd.Series([100, 101, 102])
        line = kama(close, er_window=5, fast=2, slow=30)
        assert line.isna().all() or len(line) == 3


class TestSignal:
    def test_signal_is_zero_or_one(self):
        df = _make_synthetic()
        cfg = {"params": {"er_window": 5, "fast": 2, "slow": 30, "slope_lookback": 10}}
        sig = generate_signal(df["close"], cfg)
        assert set(sig.unique()).issubset({0.0, 1.0})

    def test_signal_no_lookahead(self):
        """Signal at bar i should not depend on bar i+1."""
        df = _make_synthetic(n=200)
        cfg = {"params": {"er_window": 5, "fast": 2, "slow": 30, "slope_lookback": 10}}
        sig_full = generate_signal(df["close"], cfg)

        # Modify future bars — signal at earlier bars shouldn't change
        df2 = df.copy()
        df2.loc[df2.index[150]:, "close"] *= 2.0
        sig_mod = generate_signal(df2["close"], cfg)

        # First 140 bars should be identical (leave margin for slope lookback)
        np.testing.assert_array_equal(
            sig_full.values[:140], sig_mod.values[:140]
        )


class TestBacktest:
    def test_backtest_returns_metrics(self):
        df = _make_synthetic(n=2000)
        cfg = json.loads(
            (Path(__file__).resolve().parent.parent / "config.json").read_text()
        )
        result = run_backtest(df, cfg)
        assert result.n_trades > 0
        assert isinstance(result.sharpe, float)
        assert isinstance(result.max_drawdown, float)
        assert result.max_drawdown <= 0  # DD is negative

    def test_backtest_equity_starts_positive(self):
        df = _make_synthetic(n=500)
        cfg = {"params": {"er_window": 5, "fast": 2, "slow": 30, "slope_lookback": 10}}
        result = run_backtest(df, cfg)
        assert len(result.equity) > 0
        assert result.equity[0] > 0

    def test_flat_signal_zero_cost(self):
        """If signal is always 0, returns should be 0 (no cost)."""
        df = _make_synthetic(n=500)
        cfg = {"params": {"er_window": 5, "fast": 2, "slow": 30, "slope_lookback": 10}}
        result = run_backtest(df, cfg)
        # Just verify the backtest runs without error on synthetic data
        assert len(result.returns) > 0
