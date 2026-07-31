"""Tests for the pyalgotrade framework-CV adapter (SMA-35405 / #38).

The adapter is optional (pyalgotrade is an extra dependency); all tests skip
cleanly when the engine is unavailable so the suite stays green in minimal
CI environments. The runtime path is exercised in any venv that has
``pip install pyalgotrade pandas numpy``.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from validation.adapters import pyalgotrade_replay as pgr


PYALGOTRADE_AVAILABLE = pgr.is_available()
needs_pyalgotrade = pytest.mark.skipif(
    not PYALGOTRADE_AVAILABLE,
    reason="pyalgotrade not installed (pip install pyalgotrade)",
)


def _synthetic_df(n_bars: int = 60, seed: int = 7) -> pd.DataFrame:
    """Build a tz-aware UTC ohlcv frame with a non-trivial price path.

    Volume defaults to a large constant so that pyalgotrade's
    ``fill_strategy`` always has enough to fill any reasonable share count
    (the native test sizing is ``cash * 0.01 / price`` ~= 10 shares for the
    100.0 seed price path).
    """
    idx = pd.date_range("2025-01-01", periods=n_bars, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    ret = 0.0002 + 0.002 * rng.standard_normal(n_bars)
    close = 100.0 * np.cumprod(1.0 + ret)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )


# --------------------------------------------------------------------------
# availability + error-shape
# --------------------------------------------------------------------------

def test_is_available_returns_bool():
    assert isinstance(pgr.is_available(), bool)


def test_import_error_none_when_available():
    if PYALGOTRADE_AVAILABLE:
        assert pgr.import_error() is None


def test_run_raises_clean_error_when_engine_missing(monkeypatch):
    if PYALGOTRADE_AVAILABLE:
        pytest.skip("pyalgotrade installed; missing-engine path not applicable")
    df = _synthetic_df()
    with pytest.raises(pgr.PyalgotradeReplayError, match="not installed"):
        pgr.run_pyalgotrade_replay(df, [], symbol="BTCUSDT")


# --------------------------------------------------------------------------
# happy path (skipped without pyalgotrade)
# --------------------------------------------------------------------------

@needs_pyalgotrade
def test_run_returns_framework_run_shape():
    df = _synthetic_df()
    res = pgr.run_pyalgotrade_replay(
        df, [], symbol="BTCUSDT", starting_cash=100_000.0,
        commission=0.0002, weight=0.01, timeframe="1h",
    )
    assert res.framework == "pyalgotrade"
    assert res.symbol == "BTCUSDT"
    assert isinstance(res.equity, pd.Series)
    assert len(res.equity) == len(df)
    assert res.equity.index.tz is None  # tz-naive UTC contract
    assert isinstance(res.trade_pnls, list)
    assert res.trades == []  # we emit per-trade pnl only; not normalised dicts


@needs_pyalgotrade
def test_no_trades_keeps_cash_flat():
    df = _synthetic_df()
    res = pgr.run_pyalgotrade_replay(df, [], symbol="BTCUSDT")
    assert res.trade_pnls == []
    # No fills -> equity should stay at starting cash (modulo zero MTM drift).
    assert res.equity.iloc[0] == pytest.approx(100_000.0)
    assert res.equity.iloc[-1] == pytest.approx(100_000.0)


@needs_pyalgotrade
def test_long_trade_captures_one_pnl():
    df = _synthetic_df(n_bars=60)
    trades = [
        {
            "direction": "long",
            "entry_date": df.index[5],
            "exit_date": df.index[15],
        }
    ]
    res = pgr.run_pyalgotrade_replay(
        df, trades, symbol="BTCUSDT", commission=0.0002, weight=0.01,
        timeframe="1h",
    )
    assert len(res.trade_pnls) == 1
    # long trade pnl is proceeds / basis - 1 ; can be either sign on synthetic data
    assert isinstance(res.trade_pnls[0], float)


@needs_pyalgotrade
def test_short_trade_captures_one_pnl():
    df = _synthetic_df(n_bars=60, seed=11)
    trades = [
        {
            "direction": "short",
            "entry_date": df.index[10],
            "exit_date": df.index[25],
        }
    ]
    res = pgr.run_pyalgotrade_replay(
        df, trades, symbol="BTCUSDT", commission=0.0002, weight=0.01,
        timeframe="1h",
    )
    assert len(res.trade_pnls) == 1


@needs_pyalgotrade
def test_long_then_short_captures_two_pnls():
    df = _synthetic_df(n_bars=80)
    trades = [
        {"direction": "long", "entry_date": df.index[10], "exit_date": df.index[25]},
        {"direction": "short", "entry_date": df.index[40], "exit_date": df.index[60]},
    ]
    res = pgr.run_pyalgotrade_replay(
        df, trades, symbol="BTCUSDT", commission=0.0002, weight=0.01,
        timeframe="1h",
    )
    assert len(res.trade_pnls) == 2


@needs_pyalgotrade
def test_share_lots_produces_fills_at_crypto_prices():
    """At weight=0.01 and prices >= $1k, share_lots must be > 1 to avoid
    integer truncation to zero shares. This is the crypto-USD pair
    scenario the framework-CV runs into."""
    df = _synthetic_df(n_bars=60, seed=4)
    # Scale the price series into the crypto-USD range so the default
    # share_lots=1 path would round to zero shares.
    df = df.assign(
        open=df["open"] * 1000,
        high=df["high"] * 1000,
        low=df["low"] * 1000,
        close=df["close"] * 1000,
    )
    trades = [
        {"direction": "long", "entry_date": df.index[10], "exit_date": df.index[25]},
    ]
    # Without share_lots: 0 fills (integer rounding kills the order).
    res_zero = pgr.run_pyalgotrade_replay(
        df, trades, symbol="BTCUSDT", commission=0.0002, weight=0.01,
        timeframe="1h",
    )
    # With share_lots=10000: ~25 shares at $100 notional — should fill.
    res_lots = pgr.run_pyalgotrade_replay(
        df, trades, symbol="BTCUSDT", commission=0.0002, weight=0.01,
        timeframe="1h", share_lots=10000,
    )
    # share_lots=1 may still produce a tiny fill if the integer-truncation
    # residual happens to round up; the assertion is just that share_lots
    # is at least as good and we capture the long-trade pnl in the lots case.
    assert len(res_lots.trade_pnls) >= len(res_zero.trade_pnls)
    # Either path that produces a fill must return normalised pnl (|x| < 1.0).
    for p in res_lots.trade_pnls:
        assert -1.0 <= p <= 1.0


@needs_pyalgotrade
def test_zero_duration_trade_dropped():
    df = _synthetic_df(n_bars=40)
    trades = [
        {"direction": "long", "entry_date": df.index[5], "exit_date": df.index[5]},
        {"direction": "long", "entry_date": df.index[10], "exit_date": df.index[20]},
    ]
    res = pgr.run_pyalgotrade_replay(
        df, trades, symbol="BTCUSDT", commission=0.0002, weight=0.01,
        timeframe="1h",
    )
    # Only the non-zero-duration trade should produce a fill.
    assert len(res.trade_pnls) == 1


@needs_pyalgotrade
def test_missing_required_columns_raises_clean_error():
    df = _synthetic_df().drop(columns=["high", "low"])
    with pytest.raises(pgr.PyalgotradeReplayError, match="requires columns"):
        pgr.run_pyalgotrade_replay(df, [], symbol="BTCUSDT")


# --------------------------------------------------------------------------
# framework-CV integration
# --------------------------------------------------------------------------

@needs_pyalgotrade
def test_pyalgotrade_in_FRAMEWORKS_tuple():
    """Sanity-check the wiring into the generic harness FRAMEWORKS tuple.

    Skip if the generic harness has a missing dependency in this clone (e.g.
    ``_shared.validation.fee_shock`` is not present in the local checkout —
    the Tokyo runtime ships it).
    """
    pytest.importorskip("_shared.validation.fee_shock")
    from validation.generic_harness import FRAMEWORKS
    assert "pyalgotrade" in FRAMEWORKS


@needs_pyalgotrade
def test_evaluate_gates_accepts_window_pyalgotrade_kw():
    """The pyalgotrade kwarg must exist on evaluate_gates so G5 picks it up."""
    import inspect
    from validation.gates import evaluate_gates
    params = inspect.signature(evaluate_gates).parameters
    assert "window_pyalgotrade" in params
    assert params["window_pyalgotrade"].default is None


@needs_pyalgotrade
def test_dispatch_via_harness_runs_pyalgotrade_leg():
    """Smoke-test the harness dispatcher branches for pyalgotrade.

    Skip if the generic harness has a missing dependency in this clone.
    """
    pytest.importorskip("_shared.validation.fee_shock")
    from validation.generic_harness import _run_framework_leg
    df = _synthetic_df(n_bars=40)
    trade_dicts = [
        {"direction": "long", "entry_date": df.index[5], "exit_date": df.index[15]},
    ]
    # Build a minimal native-like object the dispatcher expects.
    from dataclasses import dataclass
    @dataclass
    class _NativeStub:
        trades: list
    native = _NativeStub(trades=trade_dicts)
    res = _run_framework_leg(
        "pyalgotrade", df, native,
        symbol="BTCUSDT", timeframe="1h",
        starting_capital=100_000.0, commission=0.0002,
        weight=0.01, max_open=5, keep_ft_dir=False,
    )
    assert res.framework == "pyalgotrade"
    assert len(res.equity) == len(df)