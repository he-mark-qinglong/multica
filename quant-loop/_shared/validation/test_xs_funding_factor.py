"""Unit tests for scripts/xs_funding_factor.py (work package XS).

Synthetic-data tests for the pure core: funding diff, expanding-quantile
event detection (no-lookahead), positionally-aligned signed forward returns,
signed baseline, and cell statistics. Real-data results live in
``research/xs_funding/REPORT.md``.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd

from scripts.xs_funding_factor import (
    MIN_HISTORY_BARS,
    detect_events,
    funding_diff,
    signed_baseline,
    signed_forward_returns,
    summarize_cell,
)


def _series(values, start="2024-01-01", freq="8h"):
    idx = pd.date_range(start, periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def test_funding_diff_inner_join():
    a = _series([1.0, 2.0, 3.0, 4.0])
    b = _series([0.5, 1.5, 9.9], start="2024-01-01 08:00")  # offset by one bar
    diff = funding_diff(a, b)
    assert len(diff) == 3  # only the shared grid points survive
    assert diff.iloc[0] == 2.0 - 0.5


def test_detect_events_respects_warmup_and_no_lookahead():
    # Constant small diffs, then a single huge spike at the very end.
    values = [0.001] * (MIN_HISTORY_BARS + 1) + [10.0]
    diff = _series(values)
    events = detect_events(diff)
    # Only the final spike qualifies; nothing before the warm-up can fire.
    assert list(events.index) == [diff.index[-1]]
    assert events.iloc[0] == 10.0


def test_detect_events_threshold_uses_past_only():
    # A spike must not raise the threshold used to judge itself: with the
    # spike at position MIN_HISTORY_BARS the expanding window sees only the
    # flat prefix, so the spike fires.
    values = [0.001] * MIN_HISTORY_BARS + [0.01]
    diff = _series(values)
    events = detect_events(diff)
    assert diff.index[-1] in events.index


def test_signed_forward_returns_direction_and_alignment():
    # Two events on the 8h grid: diff +2 (crowded longs -> short) and -3
    # (crowded shorts -> long). Prices rise 100 -> 110 -> 120.
    events = _series([2.0, -3.0])
    close = _series([100.0, 110.0, 120.0])
    sig = signed_forward_returns(events, close, horizon_h=8)
    # Event 1: direction -1, fwd +10% -> -10%. Event 2: direction +1 on
    # 110 -> 120 -> +9.09%.
    assert len(sig) == 2
    assert np.isclose(sig.iloc[0], -0.10)
    assert np.isclose(sig.iloc[1], 120.0 / 110.0 - 1.0)


def test_signed_forward_returns_drops_events_beyond_price_coverage():
    events = _series([1.0, 1.0])
    close = _series([100.0, 101.0])  # only two bars: second event has no t+8h
    sig = signed_forward_returns(events, close, horizon_h=8)
    assert len(sig) == 1


def test_signed_forward_returns_consecutive_events_not_zeroed():
    # Regression: consecutive events share grid labels across p0/p1; a
    # label-aligned division used to collapse these to a spurious exact 0.
    events = _series([1.0, 1.0, 1.0])  # three consecutive 8h events
    close = _series([100.0, 105.0, 110.0, 115.0])
    sig = signed_forward_returns(events, close, horizon_h=8)
    assert len(sig) == 3
    assert np.isclose(sig.iloc[0], -0.05)
    assert not (sig == 0.0).any()


def test_signed_baseline_covers_all_grid_points():
    diff = _series([1.0, -1.0, 1.0, -1.0])
    close = _series([100.0, 101.0, 102.0, 103.0, 104.0])
    base = signed_baseline(diff, close, horizon_h=8)
    assert len(base) == 4  # last close bar drops out (no t+8h price)


def test_summarize_cell_stats():
    signal = pd.Series([0.01, 0.02, 0.03, -0.01])
    baseline = pd.Series([0.0, 0.001, -0.001])
    cell = summarize_cell("BTC", "binance-bybit", 8, signal, baseline)
    assert cell.n_events == 4
    assert np.isclose(cell.mean_signal_ret, 0.0125)
    assert np.isclose(cell.win_rate, 0.75)
    assert cell.t_stat > 0
    assert np.isclose(cell.excess_vs_baseline, 0.0125 - 0.0)


def test_summarize_cell_empty_signal():
    cell = summarize_cell("BTC", "binance-bybit", 8, pd.Series(dtype=float), pd.Series(dtype=float))
    assert cell.n_events == 0
    assert np.isnan(cell.t_stat)
