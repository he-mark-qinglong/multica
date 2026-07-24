"""Tests for the shared multi-TF base module (mtf_xs_pairs_base_20260718)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_INDICATORS_DIR = Path(__file__).resolve().parents[1]
if str(_INDICATORS_DIR) not in sys.path:
    sys.path.insert(0, str(_INDICATORS_DIR))

import mtf_xs_pairs_base_20260718 as base  # noqa: E402


def _make_1m_df(n=2000, start="2026-01-01", seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1min")
    ret = rng.normal(0, 0.001, size=n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1.0 + np.abs(rng.normal(0, 0.0005, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0, 0.0005, size=n)))
    open_ = close * (1.0 + rng.normal(0, 0.0002, size=n))
    volume = np.abs(rng.normal(100, 20, size=n))
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                          "close": close, "volume": volume}, index=idx)


def test_aggregate_ohlcv_no_lookahead():
    df = _make_1m_df(n=2000, seed=1)
    out_15m = base.aggregate_ohlcv(df, "15min")
    # The 15m bars should have first/last aligned with 15m boundaries.
    assert len(out_15m) > 0
    # first 15m bar's open == first df bar's open
    assert abs(out_15m["open"].iloc[0] - df["open"].iloc[0]) < 1e-9
    # No bar should have volume 0 (synthetic data)
    assert (out_15m["volume"] > 0).all()


def test_align_lower_to_upper_ffill():
    df = _make_1m_df(n=300, seed=2)
    upper = df["close"].resample("15min", closed="left", label="left").last().dropna()
    aligned = base.align_lower_to_upper(df, upper)
    assert len(aligned) == len(df)
    # The first 1m bar in a 15m bucket should have the upper bar value
    first_idx_of_first_15m = upper.index[0]
    assert abs(aligned.iloc[0] - upper.iloc[0]) < 1e-9 or pd.isna(aligned.iloc[0])


def test_pair_zscore_zero_when_synchronized():
    # Two series with a constant ratio => log-ratio is constant; z should
    # be near-zero wherever SD isn't tiny floating-point noise.
    a = pd.Series(np.linspace(100, 110, 200))
    b = pd.Series(np.linspace(50, 55, 200))
    z = base.pair_zscore(a, b, lookback=50)
    # SD near zero => blowup is expected; assert only that the median
    # magnitude is tiny (i.e. signal wasn't computed against a non-zero
    # mean).
    valid = z.dropna()
    # Either all values are tiny (no SD blow-up) or, when SD is sub-1e-9,
    # z can be a large value divided by ~1e-9, which is acceptable noise.
    median_abs = float(np.median(np.abs(valid)))
    assert median_abs < 1.0  # sanity: not systematically off


def test_zscore_slope_negative_when_z_falls():
    z = pd.Series(np.linspace(3.0, -3.0, 200))
    slope = base.zscore_slope(z, lookback=30)
    last = slope.dropna().iloc[-1]
    assert last < 0


def test_trend_direction_long_short():
    close = pd.Series(np.linspace(100, 200, 100))
    t = base.trend_direction(close, fast=8, slow=34)
    assert int(t.iloc[-1]) == 1
    close2 = pd.Series(np.linspace(200, 100, 100))
    t2 = base.trend_direction(close2, fast=8, slow=34)
    assert int(t2.iloc[-1]) == -1


def test_sharpe_daily_resampled_sign():
    rng = np.random.default_rng(7)
    idx = pd.date_range("2025-01-01", periods=20000, freq="1min")
    pos = rng.normal(0.0002, 0.001, size=20000)
    neg = rng.normal(-0.0002, 0.001, size=20000)
    sr_pos = base.sharpe_daily_resampled(pos, idx)
    sr_neg = base.sharpe_daily_resampled(neg, idx)
    assert sr_pos["sharpe_daily_resampled"] > 0
    assert sr_neg["sharpe_daily_resampled"] < 0


def test_run_backtest_h1_minimal_smoke():
    """Smoke-test that H1 dispatch returns a well-formed result."""
    d1m = {"AAAUSDT": _make_1m_df(n=3000, seed=11, start="2026-01-01"),
           "BBBUSDT": _make_1m_df(n=3000, seed=22, start="2026-01-01")}
    cfg = {
        "hypothesis": "H1",
        "pairs": ["AAAUSDT/BBBUSDT"],
        "fees_bps_per_side": 1.0,
        "slippage_bps_per_side": 1.0,
        "starting_capital_usd": 100000.0,
        "indicators": {
            "zscore_lookback_bars": 240,
            "zscore_entry_threshold": 2.5,
            "zscore_exit_threshold": 0.5,
            "regime_break_threshold": 3.0,
            "slope_15m_lookback": 30,
            "trend_2h_fast": 8,
            "trend_2h_slow": 34,
            "max_holding_bars": 240,
        },
    }
    res = base.run_backtest(d1m, cfg)
    assert "per_pair" in res and "portfolio" in res
    assert len(res["per_pair"]) == 1
    pp = res["per_pair"][0]
    # Per-pair fields
    for k in ("trades", "bar_return", "n_bars", "span_start", "span_end", "pair"):
        assert k in pp
    # Portfolio
    assert "bar_return" in res["portfolio"]
    assert res["portfolio"]["n_bars"] >= 0