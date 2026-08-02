"""Tests for research/oi_funding_squeeze/squeeze.py (pure core)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from research.oi_funding_squeeze.squeeze import (
    baseline_table,
    event_table,
    forward_returns,
    funding_to_daily,
    oi_change_z,
    oi_to_daily,
    price_to_daily,
    rolling_z,
    squeeze_score,
    summarize,
)


def _ms(dt: str) -> int:
    return int(pd.Timestamp(dt, tz="UTC").timestamp() * 1000)


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------

def test_oi_to_daily_takes_last_of_day():
    oi = pd.DataFrame({
        "timestamp": [_ms("2026-07-12 01:00"), _ms("2026-07-12 23:00"),
                      _ms("2026-07-13 01:00")],
        "open_interest_value": [100.0, 110.0, 130.0],
    })
    d = oi_to_daily(oi)
    assert len(d) == 2
    assert d.iloc[0] == 110.0  # last of day, not mean
    assert d.iloc[1] == 130.0


def test_funding_to_daily_mean_of_8h():
    fu = pd.DataFrame({
        "ts": [_ms("2026-07-12 00:00"), _ms("2026-07-12 08:00"),
               _ms("2026-07-12 16:00"), _ms("2026-07-13 00:00")],
        "fundingRate": [0.001, 0.003, -0.001, 0.01],
    })
    d = funding_to_daily(fu)
    assert d.iloc[0] == pytest.approx(0.001)
    assert d.iloc[1] == pytest.approx(0.01)


def test_price_to_daily_last_close():
    px = pd.DataFrame({
        "open_time": [_ms("2026-07-12 00:00"), _ms("2026-07-12 23:00")],
        "close": [50.0, 55.0],
    })
    d = price_to_daily(px)
    assert d.iloc[0] == 55.0


# ---------------------------------------------------------------------------
# factor math
# ---------------------------------------------------------------------------

def test_rolling_z_needs_full_window():
    x = pd.Series(np.arange(1.0, 11.0))
    z = rolling_z(x, window=5)
    assert z.iloc[:4].isna().all()
    assert z.iloc[4:].notna().all()


def test_rolling_z_constant_series_is_nan_not_inf():
    x = pd.Series([7.0] * 10)
    z = rolling_z(x, window=5)
    assert z.isna().all()  # std == 0 -> NaN, never inf


def test_oi_change_z_spike_is_positive():
    idx = pd.date_range("2026-07-01", periods=30, freq="1D", tz="UTC")
    oi = pd.Series(np.linspace(100, 110, 30), index=idx)
    oi.iloc[-1] = 200.0  # OI spike on last day
    z = oi_change_z(oi, window=20)
    assert z.iloc[-1] > 2.0


def test_squeeze_score_sign_logic():
    idx = pd.date_range("2026-07-01", periods=3, freq="1D", tz="UTC")
    oi_z = pd.Series([2.5, 2.5, -1.0], index=idx)
    fund = pd.Series([0.001, -0.001, 0.001], index=idx)
    s = squeeze_score(oi_z, fund)
    assert s.iloc[0] == pytest.approx(2.5)   # OI spike + pos funding = crowded long
    assert s.iloc[1] == pytest.approx(-2.5)  # OI spike + neg funding = crowded short
    assert s.iloc[2] == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# event study
# ---------------------------------------------------------------------------

def test_forward_returns_tail_nan():
    idx = pd.date_range("2026-07-01", periods=5, freq="1D", tz="UTC")
    close = pd.Series([100, 110, 99, 120, 115], index=idx, dtype=float)
    fwd = forward_returns(close, [1, 3])
    assert fwd[1].iloc[0] == pytest.approx(0.10)
    assert fwd[1].iloc[-1] != fwd[1].iloc[-1]  # NaN
    assert fwd[3].iloc[0] == pytest.approx(0.20)  # 100 -> 120
    assert fwd[3].iloc[1] == pytest.approx(115 / 110 - 1)  # day1 -> day4
    assert fwd[3].iloc[2:].isna().all()


def test_event_table_direction_adjusted():
    idx = pd.date_range("2026-07-01", periods=4, freq="1D", tz="UTC")
    score = pd.Series([2.5, -2.5, 0.5, 3.0], index=idx)
    close = pd.Series([100.0, 100.0, 100.0, 100.0], index=idx)
    fwd = forward_returns(close, [1])
    fwd[1] = [0.10, -0.10, 0.05, 0.02]  # raw forward returns
    ev = event_table(score, fwd, threshold=2.0)
    assert len(ev) == 3  # 0.5 filtered out
    # crowded long (score>0) -> short: raw +10% -> direction-adj -10%
    assert ev["ret_1"].iloc[0] == pytest.approx(-0.10)
    # crowded short (score<0) -> long: raw -10% -> direction-adj -10%
    assert ev["ret_1"].iloc[1] == pytest.approx(-0.10)
    assert (ev["direction"] == [-1.0, 1.0, -1.0]).all()


def test_baseline_covers_all_valid_days():
    idx = pd.date_range("2026-07-01", periods=3, freq="1D", tz="UTC")
    score = pd.Series([0.1, -0.2, np.nan], index=idx)
    fwd = pd.DataFrame({1: [0.01, 0.02, 0.03]}, index=idx)
    base = baseline_table(score, fwd)
    assert len(base) == 2  # NaN score day dropped


def test_summarize_t_and_winrate():
    rets = pd.DataFrame({"ret_1": [0.01, 0.02, 0.03, -0.01, np.nan]})
    s = summarize(rets)
    row = s.loc["ret_1"]
    assert row["n"] == 4
    assert row["win"] == pytest.approx(0.75)
    assert row["t"] > 0
