"""Unit tests for universe.py liquidity filter and eligible_symbols_on."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from universe import (
    UniverseConfig,
    daily_usd_volume,
    eligible_symbols_on,
    liquidity_filter,
    load_universe_config,
    trailing_bar_count,
)


def _df(prices, volumes, start="2025-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="1D", tz="UTC")
    return pd.DataFrame({"close": prices, "volume": volumes}, index=idx)


def test_daily_usd_volume_uses_close_times_volume():
    df = _df([100.0, 200.0, 50.0], [10.0, 20.0, 30.0])
    out = daily_usd_volume(df)
    assert out.iloc[0] == pytest.approx(1000.0)
    assert out.iloc[1] == pytest.approx(4000.0)
    assert out.iloc[2] == pytest.approx(1500.0)


def test_trailing_bar_count_zero_for_all_nan_bar():
    df = _df([np.nan] * 10, [0.0] * 10)
    out = trailing_bar_count(df)
    assert (out == 0).all()


def test_trailing_bar_count_rolls_over_window():
    df = _df([100.0] * 10, [1000.0] * 10)
    out = trailing_bar_count(df, lookback_days=7)
    # Bar 0: count=1; ...; bar 6: count=7; bar 7: count=7 (window 7 days); etc.
    assert out.iloc[0] == 1
    assert out.iloc[6] == 7
    assert out.iloc[7] == 7
    assert out.iloc[-1] == 7


def test_liquidity_filter_passes_with_volume_and_history():
    # 15 bars, all close=100, volume=1_000_000 -> USD vol = 1e8 daily, passes.
    # The filter requires `min_bars_in_last_7d` non-NaN bars in the trailing
    # 7d window, so the first 4 bars are warm-up (False) and bars 4..14 pass.
    df = _df([100.0] * 15, [1_000_000.0] * 15)
    out = liquidity_filter(df, min_bars_in_last_7d=5, min_usd_volume_per_day=1_000_000.0)
    # First 4 bars are warm-up (rolling 7-day count < 5).
    assert not out.iloc[:4].any()
    # From bar 4 onward the filter must pass.
    assert out.iloc[4:].all()


def test_liquidity_filter_fails_when_history_too_thin():
    # 7 bars, 4 of them NaN. The trailing 7-day window therefore has at most
    # 3 valid bars < min_bars_in_last_7d=5 -> filter is False on every bar.
    df = _df([100.0] * 7, [1_000_000.0] * 7)
    df.iloc[:4, df.columns.get_loc("close")] = np.nan
    out = liquidity_filter(df, min_bars_in_last_7d=5, min_usd_volume_per_day=1_000_000.0)
    assert not out.any()


def test_liquidity_filter_fails_when_volume_too_low():
    df = _df([100.0] * 7, [1_000.0] * 7)   # only $100k daily USD vol
    out = liquidity_filter(df, min_bars_in_last_7d=5, min_usd_volume_per_day=1_000_000.0)
    assert not out.any()


def test_eligible_symbols_on_filters_by_liquidity():
    asof = pd.Timestamp("2025-01-10", tz="UTC")
    good_idx = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    good = pd.DataFrame(
        {"close": [100.0] * 10, "volume": [1_000_000.0] * 10},
        index=good_idx,
    )
    bad_idx = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    bad = pd.DataFrame(
        {"close": [100.0] * 10, "volume": [100.0] * 10},
        index=bad_idx,
    )
    per = {"GOOD": good, "BAD": bad}
    cfg = UniverseConfig(target=("GOOD", "BAD"), active=("GOOD", "BAD"),
                         min_bars_in_last_7d=5, min_usd_volume_per_day=1_000_000.0)
    out = eligible_symbols_on(per, asof, cfg)
    assert out == ["GOOD"]


def test_load_universe_config_reads_expected_keys(tmp_path: Path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"target_universe":["A","B"], "active_universe":["A"], '
        '"universe_filter": {"min_bars_in_last_7d": 4, "min_usd_volume_per_day": 100.0}}'
    )
    cfg = load_universe_config(cfg_path)
    assert cfg.target == ("A", "B")
    assert cfg.active == ("A",)
    assert cfg.min_bars_in_last_7d == 4
    assert cfg.min_usd_volume_per_day == 100.0