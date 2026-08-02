"""Tests for backtest_live_parity.py (B19 regression harness)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.backtest_live_parity import (
    Fill,
    ParityParams,
    compare_fills,
    infer_bar_seconds,
    run_backtest_path,
    run_paper_path,
    validate_parity,
)

PARAMS = ParityParams(initial_capital=100_000.0, cost_bps_rt=24.0)


def _bars(n: int = 300, seed: int = 7) -> pd.DataFrame:
    """Synthetic 1-minute bars with a gentle random walk + sinusoid."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    steps = rng.normal(0.0, 0.05, n) + 0.3 * np.sin(np.arange(n) / 15.0)
    close = 100.0 + np.cumsum(steps)
    close = np.maximum(close, 1.0)
    return pd.DataFrame({"close": close}, index=idx)


def _mr_strategy(ts, bar, position, bars):
    """Mean-reversion: long below sma20 - band, flat above sma20 + band."""
    j = bars.index.get_loc(ts)
    if j < 20:
        return 0
    sma = bars["close"].iloc[j - 20:j].mean()
    c = bar["close"]
    if c < sma * 0.998:
        return 1
    if c > sma * 1.002:
        return 0
    return position


def _flip_strategy(ts, bar, position, bars):
    """Trend flip: long above sma10, short below — forces same-bar flips."""
    j = bars.index.get_loc(ts)
    if j < 10:
        return 0
    sma = bars["close"].iloc[j - 10:j].mean()
    return 1 if bar["close"] > sma else -1


# ---- end-to-end parity (the pinned regression) ----

def test_parity_mean_reversion_strategy():
    bars = _bars()
    strat = lambda ts, bar, pos: _mr_strategy(ts, bar, pos, bars)
    report = validate_parity(bars, strat, PARAMS)
    assert report.n_backtest_fills > 0, "strategy must produce fills"
    assert report.n_backtest_fills == report.n_paper_fills
    assert report.ok, f"mismatches: {report.mismatches[:3]}"
    assert report.max_price_diff_bp < 1.0
    assert report.max_time_diff_bars < 1.0
    # two independent equity walks must agree to machine precision
    assert abs(report.equity_final_diff_pct) < 1e-9


def test_parity_direction_flip_strategy():
    bars = _bars(seed=11)
    strat = lambda ts, bar, pos: _flip_strategy(ts, bar, pos, bars)
    report = validate_parity(bars, strat, PARAMS)
    assert report.n_backtest_fills > 0
    assert report.ok, f"mismatches: {report.mismatches[:3]}"
    assert abs(report.equity_final_diff_pct) < 1e-9


def test_parity_flat_strategy_no_fills():
    bars = _bars()
    report = validate_parity(bars, lambda ts, bar, pos: 0, PARAMS)
    assert report.ok
    assert report.n_backtest_fills == 0 and report.n_paper_fills == 0
    assert report.metrics_backtest["final_equity"] == pytest.approx(100_000.0)
    assert report.metrics_paper["final_equity"] == pytest.approx(100_000.0)


def test_paths_apply_same_economics():
    # sanity: with a trading strategy the equity actually moves off capital
    bars = _bars()
    strat = lambda ts, bar, pos: _flip_strategy(ts, bar, pos, bars)
    bt = run_backtest_path(bars, strat, PARAMS)
    pp = run_paper_path(bars, strat, PARAMS)
    assert bt.metrics["final_equity"] != pytest.approx(100_000.0)
    assert pp.metrics["final_equity"] == pytest.approx(
        bt.metrics["final_equity"], rel=1e-12)


# ---- compare_fills detection logic ----

T0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")


def _fill(minutes, price, side="buy"):
    return Fill(T0 + pd.Timedelta(minutes=minutes), side, price, "entry")


def test_compare_detects_price_drift_above_1bp():
    a = [_fill(0, 100.0), _fill(5, 100.5)]
    b = [_fill(0, 100.0), _fill(5, 100.5 * 1.0002)]   # 2 bp away
    mism, max_bp, _ = compare_fills(a, b, 60.0, PARAMS)
    assert len(mism) == 1 and mism[0].index == 1
    assert max_bp == pytest.approx(2.0, abs=0.01)


def test_compare_allows_sub_1bp_drift():
    a = [_fill(0, 100.0)]
    b = [_fill(0, 100.0 * 1.00005)]                    # 0.5 bp
    mism, _, _ = compare_fills(a, b, 60.0, PARAMS)
    assert mism == ()


def test_compare_detects_time_drift_of_one_bar():
    a = [_fill(0, 100.0)]
    b = [_fill(1, 100.0)]                              # exactly 1 bar late
    mism, _, max_bars = compare_fills(a, b, 60.0, PARAMS)
    assert len(mism) == 1
    assert max_bars == pytest.approx(1.0)


def test_compare_detects_side_flip():
    a = [_fill(0, 100.0, side="buy")]
    b = [_fill(0, 100.0, side="sell")]
    mism, _, _ = compare_fills(a, b, 60.0, PARAMS)
    assert len(mism) == 1


def test_compare_detects_count_mismatch():
    a = [_fill(0, 100.0), _fill(5, 100.5)]
    b = [_fill(0, 100.0)]
    mism, _, _ = compare_fills(a, b, 60.0, PARAMS)
    assert len(mism) == 1
    assert mism[0].paper_fill is None


def test_infer_bar_seconds():
    bars = _bars()
    assert infer_bar_seconds(bars) == pytest.approx(60.0)
