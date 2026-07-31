"""Tests for fair_value.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.fair_value import (
    FairValue, MarketSnapshot,
    compute_fair_value, microprice, rolling_vwap, vpvr_fair_value,
)


# ---- microprice ----

def test_microprice_equal_volumes():
    assert microprice(100, 102, 5, 5) == pytest.approx(101.0)

def test_microprice_bid_dominates():
    # bid_vol >> ask_vol → shifts toward ask (102)
    mp = microprice(100, 102, 90, 10)
    assert mp > 101.0  # above mid

def test_microprice_zero_total_volume():
    assert microprice(100, 102, 0, 0) == pytest.approx(101.0)

def test_microprice_ask_dominates():
    # ask_vol >> bid_vol → shifts toward bid (100)
    mp = microprice(100, 102, 10, 90)
    assert mp < 101.0  # below mid


# ---- rolling_vwap ----

def test_vwap_empty():
    assert np.isnan(rolling_vwap(pd.DataFrame()))

def test_vwap_single_trade():
    df = pd.DataFrame({"price": [50000.0], "qty": [1.0]})
    assert rolling_vwap(df) == pytest.approx(50000.0)

def test_vwap_weighted():
    df = pd.DataFrame({"price": [100.0, 200.0], "qty": [3.0, 1.0]})
    # (100*3 + 200*1) / (3+1) = 500/4 = 125
    assert rolling_vwap(df) == pytest.approx(125.0)

def test_vwap_lookback():
    df = pd.DataFrame({"price": [100.0, 200.0, 300.0], "qty": [1.0, 1.0, 1.0]})
    # lookback=1 → only last trade
    assert rolling_vwap(df, lookback=1) == pytest.approx(300.0)


# ---- vpvr_fair_value ----

def test_vpvr_returns_finite():
    high = pd.Series([101, 102, 103, 102, 101])
    low = pd.Series([99, 100, 101, 100, 99])
    volume = pd.Series([10, 20, 50, 20, 10])
    val = vpvr_fair_value(high, low, volume)
    assert np.isfinite(val)

def test_vpvr_empty():
    assert np.isnan(vpvr_fair_value(pd.Series([]), pd.Series([]), pd.Series([])))


# ---- compute_fair_value ----

def _make_snapshot(bars=None):
    ts = pd.Timestamp("2026-04-20 12:00:00", tz="UTC")
    trades = pd.DataFrame({
        "ts": [ts] * 5,
        "price": [100.0, 101.0, 100.5, 100.2, 100.8],
        "qty": [1.0, 2.0, 1.5, 0.5, 1.0],
        "is_buyer_maker": [True, False, True, False, True],
    })
    if bars is None:
        bars = pd.DataFrame({
            "high": [101, 102, 103, 102, 101] * 5,
            "low": [99, 100, 101, 100, 99] * 5,
            "close": [100, 101, 102, 101, 100] * 5,
            "volume": [10, 20, 50, 20, 10] * 5,
        })
    return MarketSnapshot(
        timestamp=ts, bid_price=99.5, ask_price=100.5,
        bid_volume=5, ask_volume=5, last_price=100.0,
        recent_trades=trades, bars=bars,
    )

def test_composite_all_components():
    snap = _make_snapshot()
    fv = compute_fair_value(snap)
    assert isinstance(fv, FairValue)
    assert fv.mid == pytest.approx(100.0)
    assert np.isfinite(fv.composite)
    assert np.isfinite(fv.microprice)
    assert np.isfinite(fv.vwap)

def test_composite_fallback_no_bars():
    snap = _make_snapshot(bars=pd.DataFrame())
    fv = compute_fair_value(snap)
    # VPVR unavailable → composite from microprice + vwap
    assert np.isfinite(fv.composite)
    # vpvr_poc falls back to mid
    assert fv.vpvr_poc == pytest.approx(100.0)
