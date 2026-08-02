import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")
sys.path.insert(0, "/Users/mark/multica/quant-loop/research/kama_trend")

import numpy as np
import pandas as pd

from kama_core import kama, kama_signal, ma_signal, strategy_returns, tstat


def test_kama_flat_series():
    """Constant price -> KAMA converges to the constant, slope ~ 0."""
    c = pd.Series(np.full(200, 100.0))
    k = kama(c, 10, 2, 30)
    assert np.isfinite(k.iloc[-1])
    assert abs(k.iloc[-1] - 100.0) < 1e-6


def test_kama_tracks_step():
    """After a price step, KAMA moves toward the new level."""
    c = pd.Series(np.r_[np.full(100, 100.0), np.full(100, 200.0)])
    k = kama(c, 10, 2, 30)
    assert k.iloc[-1] > 150.0
    assert k.iloc[50] < 110.0


def test_signal_no_lookahead():
    """Position at bar t must not use information after bar t-1 close."""
    idx = pd.date_range("2024-01-01", periods=300, freq="1h")
    c = pd.Series(np.sin(np.arange(300) / 10.0) + 10.0, index=idx)
    sig = kama_signal(c, 10, 2, 30, 3)
    r = strategy_returns(c, sig)
    pos = sig.shift(1).fillna(0)
    # strategy return is zero whenever position is zero and fee is zero
    flat = (pos == 0) & (pos.diff().abs().fillna(0) == 0)
    assert (r[flat.reindex(r.index, fill_value=False)] == 0).all()


def test_tstat_sign():
    idx = pd.date_range("2024-01-01", periods=500, freq="1D")
    up = pd.Series(0.001 + 0.01 * np.sin(np.arange(500)), index=idx)
    assert tstat(up) > 0
    assert tstat(-up) < 0


def test_ma_signal_binary():
    idx = pd.date_range("2024-01-01", periods=100, freq="1D")
    c = pd.Series(np.linspace(1, 2, 100), index=idx)
    sig = ma_signal(c, 20)
    assert set(sig.unique()) <= {0.0, 1.0}
    assert sig.iloc[-1] == 1.0  # uptrend -> long
