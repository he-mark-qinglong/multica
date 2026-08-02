"""Unit tests for Multi-TF KAMA strategy."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy import kama, generate_signal, run_backtest


def _make_synthetic(n=2000, seed=42, drift=0.0001, freq="4h"):
    rng = np.random.default_rng(seed)
    rets = drift + rng.standard_normal(n) * 0.01
    close = 100 * np.cumprod(1 + rets)
    ts = pd.date_range("2020-01-01", periods=n, freq=freq)
    return pd.DataFrame({
        "open": close, "high": close * 1.001,
        "low": close * 0.999, "close": close,
        "volume": np.ones(n) * 100,
    }, index=ts)


CFG = {
    "params": {
        "tf_4h": {"er_window": 5, "fast": 2, "slow": 30, "slope_lookback": 10},
        "tf_1d": {"er_window": 10, "fast": 3, "slow": 30, "slope_lookback": 3},
    }
}


class TestKama:
    def test_length_matches_input(self):
        close = pd.Series(np.random.randn(500) + 100)
        line = kama(close, er_window=10, fast=2, slow=30)
        assert len(line) == len(close)

    def test_handles_short_series(self):
        close = pd.Series([100, 101, 102])
        line = kama(close, er_window=5, fast=2, slow=30)
        assert len(line) == 3


class TestSignal:
    def test_signal_is_zero_or_one(self):
        df_4h = _make_synthetic(2000)
        df_1d = _make_synthetic(500, freq="1D")
        sig = generate_signal(df_4h, df_1d, CFG)
        assert set(sig.unique()).issubset({0.0, 1.0})

    def test_no_lookahead(self):
        df_4h = _make_synthetic(2000)
        df_1d = _make_synthetic(500, freq="1D")
        sig_full = generate_signal(df_4h, df_1d, CFG)

        # Modify future 4h bars
        df_4h2 = df_4h.copy()
        df_4h2.loc[df_4h2.index[1500]:, "close"] *= 2.0
        sig_mod = generate_signal(df_4h2, df_1d, CFG)
        np.testing.assert_array_equal(
            sig_full.values[:1400], sig_mod.values[:1400]
        )

    def test_and_gate_logic(self):
        """Signal should be 1 only when both 4h and 1d agree."""
        df_4h = _make_synthetic(2000)
        df_1d = _make_synthetic(500, freq="1D")
        sig = generate_signal(df_4h, df_1d, CFG)
        # AND gate means combined signal ≤ 4h signal at every point
        from strategy import _kama_slope_signal
        sig_4h = _kama_slope_signal(df_4h["close"], CFG["params"]["tf_4h"])
        assert (sig <= sig_4h + 1e-9).all()


class TestBacktest:
    def test_returns_metrics(self):
        df_4h = _make_synthetic(2000)
        df_1d = _make_synthetic(500, freq="1D")
        result = run_backtest(df_4h, df_1d, CFG)
        assert result.n_trades >= 0
        assert isinstance(result.sharpe, float)
        assert result.max_drawdown <= 0

    def test_equity_positive(self):
        df_4h = _make_synthetic(1000)
        df_1d = _make_synthetic(300, freq="1D")
        result = run_backtest(df_4h, df_1d, CFG)
        assert len(result.equity) > 0
        assert result.equity[0] > 0
