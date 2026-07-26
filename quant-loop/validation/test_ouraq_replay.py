"""Unit tests for the ouraq replay adapter (SMA-35406).

Covers the happy-path replay, the ``OuraqReplayError`` validation
surface, the no-trade edge case, and the vol-targeting scaling — the
single piece of behaviour that distinguishes ouraq from the other
replay adapters.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation.adapters.native_engine import FrameworkRun  # noqa: E402
from validation.adapters.ouraq_replay import (  # noqa: E402
    OuraqReplayError,
    run_ouraq_replay,
)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _synthetic_bars(n_bars: int = 200, *, drift: float = 0.0005,
                    vol: float = 0.01, seed: int = 7) -> pd.DataFrame:
    """200-bar deterministic synthetic 1h bars (no tz to match the
    freqtrade fixture convention)."""
    idx = pd.date_range("2026-01-01", periods=n_bars, freq="1h")
    rng = np.random.default_rng(seed)
    ret = drift + vol * rng.standard_normal(n_bars)
    close = 100.0 * np.cumprod(1.0 + ret)
    return pd.DataFrame({"close": close}, index=idx)


def _long_trade(bar: pd.DataFrame, i: int, hold: int = 10):
    return {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_date": bar.index[i],
        "entry_price": float(bar["close"].iloc[i]),
        "exit_date": bar.index[i + hold],
        "exit_price": float(bar["close"].iloc[i + hold]),
        "pnl_pct": 0.0,  # ignored by the adapter
    }


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------


def test_returns_framework_run_with_correct_framework_tag():
    df = _synthetic_bars()
    trades = [_long_trade(df, 30), _long_trade(df, 80)]
    out = run_ouraq_replay(df, trades, symbol="BTCUSDT")
    assert isinstance(out, FrameworkRun)
    assert out.framework == "ouraq"
    assert out.symbol == "BTCUSDT"
    assert out.trades == []  # replay adapters don't echo native trade dicts


def test_equity_series_is_dense_and_matches_bar_index():
    df = _synthetic_bars()
    trades = [_long_trade(df, 30, hold=5)]
    out = run_ouraq_replay(df, trades, symbol="BTCUSDT")
    # equity is a pandas Series on the same DatetimeIndex as the input.
    assert isinstance(out.equity, pd.Series)
    assert isinstance(out.equity.index, pd.DatetimeIndex)
    assert out.equity.index.is_monotonic_increasing
    # First and last equity values are finite numbers around starting_cash.
    assert math.isfinite(float(out.equity.iloc[0]))
    assert math.isfinite(float(out.equity.iloc[-1]))
    assert 50_000 < float(out.equity.iloc[0]) < 200_000


def test_no_trades_produces_flat_equity_series():
    df = _synthetic_bars(50)
    out = run_ouraq_replay(df, [], symbol="BTCUSDT")
    assert len(out.trade_pnls) == 0
    # Every bar is at starting_cash (no trade ever opened).
    assert float(out.equity.iloc[0]) == pytest.approx(100_000.0)
    assert float(out.equity.iloc[-1]) == pytest.approx(100_000.0)


def test_short_trade_symmetric_within_floating_tolerance():
    df = _synthetic_bars(120)
    # long and short on the same bar path should produce the same equity
    # to within floating-point noise (the vol-scaler at entry is the
    # same, so the only difference is the sign of the per-bar return).
    long_trade = _long_trade(df, 40, hold=20)
    short_trade = dict(long_trade, direction="short")
    long_run = run_ouraq_replay(df, [long_trade], symbol="BTCUSDT")
    short_run = run_ouraq_replay(df, [short_trade], symbol="BTCUSDT")
    assert long_run.trade_pnls and short_run.trade_pnls
    # cash *= (1 + trade_ret) is direction-asymmetric to floating-point
    # noise on the order of |trade_ret| * |cash - 1|, so 1e-5 is the
    # right tolerance for a 1%-notional trade with 0.0002 fee.
    assert long_run.trade_pnls[0] == pytest.approx(-short_run.trade_pnls[0], abs=1e-5)


def test_one_position_at_a_time_force_closes_overlap():
    """Two back-to-back trades on the same entry bar must close trade #1
    at the bar's close before opening trade #2 (matches native engine
    semantics, see _shared/run_backtest.py:46-49)."""
    df = _synthetic_bars(120)
    a = _long_trade(df, 40, hold=10)
    b = _long_trade(df, 40, hold=20)  # overlaps; entry same, exit later
    out = run_ouraq_replay(df, [a, b], symbol="BTCUSDT")
    # Exactly two closed-trade pnls (one for the force-close of a, one
    # for the normal exit of b).
    assert len(out.trade_pnls) == 2


# --------------------------------------------------------------------------
# validation errors
# --------------------------------------------------------------------------


def test_missing_close_column_raises_clean_error():
    df = pd.DataFrame({"open": [1.0, 2.0]}, index=pd.date_range("2026-01-01", periods=2, freq="1h"))
    with pytest.raises(OuraqReplayError, match="close"):
        run_ouraq_replay(df, [], symbol="BTCUSDT")


@pytest.mark.parametrize("bad_cash", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_invalid_starting_cash_rejected(bad_cash):
    df = _synthetic_bars(60)
    with pytest.raises(OuraqReplayError, match="starting_cash"):
        run_ouraq_replay(df, [], symbol="BTCUSDT", starting_cash=bad_cash)


@pytest.mark.parametrize("bad_weight", [0.0, -0.01, math.nan])
def test_invalid_base_weight_rejected(bad_weight):
    df = _synthetic_bars(60)
    with pytest.raises(OuraqReplayError, match="base_weight"):
        run_ouraq_replay(df, [], symbol="BTCUSDT", base_weight=bad_weight)


@pytest.mark.parametrize("bad_fee", [-0.0001, math.nan, math.inf])
def test_invalid_fee_rejected(bad_fee):
    df = _synthetic_bars(60)
    with pytest.raises(OuraqReplayError, match="fee"):
        run_ouraq_replay(df, [], symbol="BTCUSDT", fee=bad_fee)


@pytest.mark.parametrize("bad_window", [0, 1])
def test_invalid_vol_window_rejected(bad_window):
    df = _synthetic_bars(60)
    with pytest.raises(OuraqReplayError, match="vol_window"):
        run_ouraq_replay(df, [], symbol="BTCUSDT", vol_window=bad_window)


def test_short_bars_raises_clean_error():
    df = _synthetic_bars(10)  # less than vol_window+1 = 21
    with pytest.raises(OuraqReplayError, match="vol_window"):
        run_ouraq_replay(df, [], symbol="BTCUSDT", vol_window=20)


def test_off_bar_entry_date_rejected():
    df = _synthetic_bars(60)
    bogus = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_date": pd.Timestamp("2099-01-01"),  # not on df.index
        "entry_price": 100.0,
        "exit_date": df.index[30],
        "exit_price": 100.0,
        "pnl_pct": 0.0,
    }
    with pytest.raises(OuraqReplayError, match="entry_date"):
        run_ouraq_replay(df, [bogus], symbol="BTCUSDT")


def test_off_bar_exit_date_rejected():
    df = _synthetic_bars(60)
    bogus = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_date": df.index[10],
        "entry_price": 100.0,
        "exit_date": pd.Timestamp("2099-01-01"),
        "exit_price": 100.0,
        "pnl_pct": 0.0,
    }
    with pytest.raises(OuraqReplayError, match="exit_date"):
        run_ouraq_replay(df, [bogus], symbol="BTCUSDT")


def test_exit_before_entry_rejected():
    df = _synthetic_bars(60)
    bad = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_date": df.index[20],
        "entry_price": 100.0,
        "exit_date": df.index[10],  # before entry
        "exit_price": 100.0,
        "pnl_pct": 0.0,
    }
    with pytest.raises(OuraqReplayError, match="strictly after"):
        run_ouraq_replay(df, [bad], symbol="BTCUSDT")


def test_unknown_direction_rejected():
    df = _synthetic_bars(60)
    bad = {
        "symbol": "BTCUSDT",
        "direction": "sideways",
        "entry_date": df.index[10],
        "entry_price": 100.0,
        "exit_date": df.index[20],
        "exit_price": 100.0,
        "pnl_pct": 0.0,
    }
    with pytest.raises(OuraqReplayError, match="direction"):
        run_ouraq_replay(df, [bad], symbol="BTCUSDT")


def test_invalid_size_bounds_rejected():
    df = _synthetic_bars(60)
    with pytest.raises(OuraqReplayError, match="size_floor"):
        run_ouraq_replay(df, [], symbol="BTCUSDT", size_floor=-0.1, size_cap=1.0)
    with pytest.raises(OuraqReplayError, match="size_floor"):
        run_ouraq_replay(df, [], symbol="BTCUSDT", size_floor=0.5, size_cap=0.1)


# --------------------------------------------------------------------------
# vol-targeting: the one piece of behaviour ouraq owns
# --------------------------------------------------------------------------


def test_vol_scaler_keeps_size_finite_under_zero_realised_vol():
    """A perfectly flat price series produces zero realised vol; the
    adapter must still emit a finite (clipped) scaler rather than
    blowing up to +inf or NaN."""
    idx = pd.date_range("2026-01-01", periods=80, freq="1h")
    df = pd.DataFrame({"close": np.full(80, 100.0)}, index=idx)
    out = run_ouraq_replay(df, [], symbol="BTCUSDT", vol_window=20, target_vol=0.01)
    assert np.all(np.isfinite(out.equity.to_numpy()))


def test_vol_scaler_scales_position_inversely_with_realised_vol():
    """High realised vol -> smaller scaler; low realised vol -> larger
    scaler. We assert the property directly on the scaler, not on the
    downstream pnl (which is masked by the gross-return random walk)."""
    from validation.adapters.ouraq_replay import _realised_vol

    n_bars = 400
    idx = pd.date_range("2026-01-01", periods=n_bars, freq="1h")
    rng = np.random.default_rng(11)

    # high-vol regime: ~3% per-bar stdev
    hi_ret = 0.03 * rng.standard_normal(n_bars)
    hi_close = pd.Series(100.0 * np.cumprod(1.0 + hi_ret), index=idx)
    # low-vol regime: ~0.3% per-bar stdev (10x quieter)
    lo_ret = 0.003 * rng.standard_normal(n_bars)
    lo_close = pd.Series(100.0 * np.cumprod(1.0 + lo_ret), index=idx)

    hi_scaler = _realised_vol(hi_close, window=20, target=0.01)
    lo_scaler = _realised_vol(lo_close, window=20, target=0.01)

    # Skip the warmup (first vol_window bars have NaN/bfill).
    hi_post = float(hi_scaler.iloc[40:].median())
    lo_post = float(lo_scaler.iloc[40:].median())
    # low-vol series should produce a scaler an order of magnitude
    # larger than the high-vol series.
    assert lo_post > hi_post * 5
    # and both must stay finite.
    assert math.isfinite(hi_post) and math.isfinite(lo_post)


def test_size_cap_clips_volatility_targeting():
    """If ``target_vol / realised_vol`` is huge, the resulting size must
    still be clipped to ``base_weight * size_cap`` (i.e. never exceed
    the cap regardless of how quiet the market is)."""
    # super-quiet series → tiny std → huge target/sd → must clip
    n_bars = 80
    idx = pd.date_range("2026-01-01", periods=n_bars, freq="1h")
    rng = np.random.default_rng(13)
    close = 100.0 + 1e-6 * np.cumsum(rng.standard_normal(n_bars))
    df = pd.DataFrame({"close": close}, index=idx)
    trade = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_date": idx[40],
        "entry_price": float(close[40]),
        "exit_date": idx[50],
        "exit_price": float(close[50]),
        "pnl_pct": 0.0,
    }
    # size_cap = 1.0 means the scaler can't push base_weight above 0.01;
    # the resulting per-trade notional must therefore never exceed
    # base_weight * starting_cash = 0.01 * 100_000 = 1_000.
    out = run_ouraq_replay(
        df, [trade], symbol="BTCUSDT",
        base_weight=0.01, vol_window=20, target_vol=0.01, size_cap=1.0,
    )
    assert out.trade_pnls
    # pnl = size_fraction * gross_ret - size_fraction * fee; with size
    # capped at 0.01 and gross_ret < 0.01 the absolute pnl < 0.0001.
    assert abs(out.trade_pnls[0]) < 0.001