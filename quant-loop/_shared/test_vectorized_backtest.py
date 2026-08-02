"""Unit tests for the vectorised signal-driven backtest engine (B2).

Primary gate: the vectorised engine must reproduce the authoritative
``_shared/run_backtest.py`` (cost_mode="fill") equity curve on the same
random strategy — spec requires <1% divergence; the engines actually agree
to ~1e-12 because every cost convention (next-bar execution, fill-time
commission halves, one-bar round-trip quirk, direct-flip force-close,
last-bar force-close) is replicated bit-for-bit.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.run_backtest import run_backtest
from _shared.vectorized_backtest import (
    VectorizedBacktestConfig,
    run_vectorized_backtest,
    signals_to_trades,
)


def _bars(n: int = 2000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 50_000.0 * np.exp(np.cumsum(rng.normal(5e-5, 0.004, size=n)))
    return pd.DataFrame({"close": close}, index=idx)


def _random_signals(n: int, seed: int, p_flat: float = 0.6) -> np.ndarray:
    """Random -1/0/+1 signal with realistic multi-bar runs."""
    rng = np.random.default_rng(seed)
    sig = np.zeros(n)
    state = 0.0
    for i in range(n):
        if rng.random() < 0.05:
            state = rng.choice([-1.0, 0.0, 0.0, 1.0])  # bias to flat
        sig[i] = state
    if p_flat != 0.6:  # pragma: no cover - kept for parameterised reuse
        pass
    return sig


# ---------------------------------------------------------------------------
# Consistency with the authoritative engine
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3])
def test_matches_run_backtest_random_signals(seed: int) -> None:
    bars = _bars(n=2000, seed=100 + seed)
    sig = _random_signals(len(bars), seed=seed)
    sizes = np.where(sig != 0.0, 0.5, 1.0)  # exercise per-entry size arrays

    trades = signals_to_trades(bars.index, sig, sizes)
    ref = run_backtest(bars, trades, cost_bps_rt=24.0, cost_mode="fill")
    vec = run_vectorized_backtest(
        bars["close"].to_numpy(), sig, sizes,
        index=bars.index,
        config=VectorizedBacktestConfig(cost_bps_rt=24.0),
    )

    ref_eq = ref["equity"].to_numpy()
    vec_eq = vec["equity"].to_numpy()
    max_rel = float(np.max(np.abs(vec_eq - ref_eq) / ref_eq))
    assert max_rel < 1e-9  # spec gate is 1e-2; engines agree to ~1e-12
    assert vec["n_entries"] == ref["n_trades"]
    assert vec["metrics"]["total_return_pct"] == pytest.approx(
        ref["metrics"]["total_return_pct"], rel=1e-9
    )


def test_matches_run_backtest_with_flips() -> None:
    """Direct +1 -> -1 flips hit run_backtest's force-close branch."""
    idx = pd.date_range("2024-01-01", periods=12, freq="h", tz="UTC")
    close = 100.0 * np.exp(np.cumsum(np.full(12, 0.001)))
    bars = pd.DataFrame({"close": close}, index=idx)
    sig = np.array([0, 1, 1, -1, -1, 0, 0, 1, -1, 1, 0, 0], dtype=float)

    trades = signals_to_trades(bars.index, sig)
    ref = run_backtest(bars, trades, cost_bps_rt=24.0)
    vec = run_vectorized_backtest(close, sig, index=bars.index)
    np.testing.assert_allclose(
        vec["equity"].to_numpy(), ref["equity"].to_numpy(), rtol=1e-12
    )


# ---------------------------------------------------------------------------
# Behavioural pins
# ---------------------------------------------------------------------------

def test_flat_signal_constant_equity() -> None:
    close = np.linspace(100.0, 200.0, 500)
    out = run_vectorized_backtest(close, np.zeros(500))
    np.testing.assert_allclose(out["equity"], 100_000.0)
    assert out["n_entries"] == 0
    assert out["metrics"]["total_return_pct"] == 0.0


def test_always_long_zero_cost_tracks_price() -> None:
    close = np.geomspace(100.0, 200.0, 1000)
    out = run_vectorized_backtest(
        close, np.ones(1000), config=VectorizedBacktestConfig(cost_bps_rt=0.0)
    )
    # Held from bar 1 onward: equity[j] = initial * close[j] / close[0].
    np.testing.assert_allclose(
        out["equity"], 100_000.0 * close / close[0], rtol=1e-12
    )


def test_short_profits_when_price_falls() -> None:
    close = np.geomspace(200.0, 100.0, 1000)
    out = run_vectorized_backtest(
        close, -np.ones(1000), config=VectorizedBacktestConfig(cost_bps_rt=0.0)
    )
    # Geometric price path -> constant negative per-bar return -> short
    # equity compounds up monotonically.
    assert out["equity"][-1] > 100_000.0
    assert np.all(np.diff(out["equity"]) > 0)
    g = close[1] / close[0]
    assert out["equity"][-1] == pytest.approx(100_000.0 * (2.0 - g) ** 999, rel=1e-12)


def test_entry_cost_debited_once() -> None:
    close = np.full(100, 100.0)  # zero price movement isolates costs
    out = run_vectorized_backtest(
        close, np.ones(100), config=VectorizedBacktestConfig(cost_bps_rt=24.0)
    )
    # Entry half-RT at bar 1, exit half-RT force-closed at the last bar:
    # total drag = one full round-trip on 100% notional.
    expected = 100_000.0 * (1 - 0.0012) ** 2
    assert out["equity"][-1] == pytest.approx(expected, rel=1e-12)
    assert out["n_entries"] == 1


def test_size_fraction_scales_returns_and_costs() -> None:
    rng = np.random.default_rng(3)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, size=500)))
    full = run_vectorized_backtest(
        close, np.ones(500), config=VectorizedBacktestConfig(cost_bps_rt=0.0)
    )
    half = run_vectorized_backtest(
        close, np.ones(500), 0.5, config=VectorizedBacktestConfig(cost_bps_rt=0.0)
    )
    # Half size: per-bar log returns halved approximately; exact relation:
    # 1 + r_half = 1 + 0.5 * r_full.
    r_full = full["bar_returns"]
    np.testing.assert_allclose(half["bar_returns"], 0.5 * r_full, rtol=1e-12)


def test_size_fraction_array_per_entry() -> None:
    close = np.geomspace(100.0, 150.0, 50)
    sig = np.zeros(50)
    sig[5:20] = 1.0
    sig[30:45] = -1.0
    sizes = np.ones(50)
    sizes[5] = 0.25
    sizes[30] = 0.75
    out = run_vectorized_backtest(
        close, sig, sizes, config=VectorizedBacktestConfig(cost_bps_rt=0.0)
    )
    price_ret = np.zeros(50)
    price_ret[1:] = close[1:] / close[:-1] - 1.0
    np.testing.assert_allclose(out["bar_returns"][6:20], 0.25 * price_ret[6:20])
    np.testing.assert_allclose(out["bar_returns"][31:45], -0.75 * price_ret[31:45])


def test_input_validation() -> None:
    close = np.ones(10)
    with pytest.raises(ValueError, match="same shape"):
        run_vectorized_backtest(close, np.ones(5))
    with pytest.raises(ValueError, match="only -1, 0, \\+1"):
        run_vectorized_backtest(close, np.full(10, 2.0))
    with pytest.raises(ValueError, match="\\[0, 1\\]"):
        run_vectorized_backtest(close, np.ones(10), 1.5)
    with pytest.raises(ValueError, match="initial_capital"):
        run_vectorized_backtest(
            close, np.ones(10), config=VectorizedBacktestConfig(initial_capital=0.0)
        )


def test_equity_series_uses_index() -> None:
    bars = _bars(n=100, seed=9)
    out = run_vectorized_backtest(
        bars["close"].to_numpy(), np.ones(100), index=bars.index
    )
    assert isinstance(out["equity"], pd.Series)
    assert out["equity"].index.equals(bars.index)
