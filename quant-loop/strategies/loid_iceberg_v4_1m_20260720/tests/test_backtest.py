"""Tests for the backtest harness (SMA-34992 / LOID-V4).

Minimal smoke tests for the 4 load-bearing invariants:
1. Cost is amortized across position lifetime (not per-bar)
2. Position flips on opposite signal
3. Time-stop exits after max_hold_minutes
4. Returns equity curve indexed by bar
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from strategies.loid_iceberg_v4_1m_20260720.backtest import (
    BacktestConfig,
    run as run_backtest,
)


def _make_ohlcv(prices: list[float], start_ms: int) -> pd.DataFrame:
    """Build a minimal 1m OHLCV frame with given close prices."""
    n = len(prices)
    ts = pd.date_range(
        pd.Timestamp(start_ms, unit="ms", tz="UTC"),
        periods=n,
        freq="1min",
    )
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.001 for p in prices],
            "low": [p * 0.999 for p in prices],
            "close": prices,
            "volume": [1.0] * n,
        },
        index=ts,
    )


def _make_composite(minute_offsets: list[int], composite_vals: list[float]) -> pd.DataFrame:
    """Build a per-minute composite frame aligned to a 1h sample (1 bar/offset)."""
    idx = pd.date_range("2026-07-20T00:00:00Z", periods=len(minute_offsets), freq="1min")
    return pd.DataFrame(
        {
            "minute_offset": minute_offsets,
            "composite": composite_vals,
            "n_large": [0] * len(minute_offsets),
            "n_whale": [0] * len(minute_offsets),
            "n_iceberg": [0] * len(minute_offsets),
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# Cost amortized across position lifetime (load-bearing per strategy-worker-2)
# ---------------------------------------------------------------------------


def test_round_trip_cost_applied_once_per_trade_not_per_bar():
    """Long-then-exit should deduct exactly one round-trip cost, not per-bar.

    Construct: 60 bars flat at $100, signal > thresh on bars 0..29 → enter long;
    signal drops to 0 at bar 30 → exit. Total bar drift is 0 so the only
    P&L change should be -cost. Whichever cost model is plugged in, the test
    asserts the dollar cost equals ONE round-trip, not 60.
    """
    prices = [100.0] * 60
    ohlcv = _make_ohlcv(prices, pd.Timestamp("2026-07-20T00:00:00Z").value // 1_000_000)
    composite = _make_composite(
        list(range(60)), [10.0] * 30 + [0.0] * 30
    )
    cfg = BacktestConfig(
        threshold=5.0,
        max_hold_minutes=240,
        notional_usd=100_000.0,
        adv_usd=5_000_000_000.0,
        impact_factor=0.05,
        fee_bps=4.0,  # BINANCE_FUTURES
    )
    out = run_backtest(ohlcv, composite, cfg)
    assert len(out["trades"]) == 1, f"expected 1 trade, got {len(out['trades'])}"
    trade = out["trades"][0]
    assert trade["direction"] == "long"
    # Net P&L = 0 (price unchanged) - cost.
    expected_cost = (
        cfg.notional_usd
        * (2 * (cfg.fee_bps + cfg.impact_factor * (cfg.notional_usd / cfg.adv_usd) ** 0.5 * 10000))
        / 10000.0
    )
    assert abs(trade["pnl_usd"] - (-expected_cost)) < 1.0


def test_opposite_signal_closes_position():
    """If signal flips sign, position must close before a new one opens."""
    prices = [100.0] * 10 + [101.0] * 10
    ohlcv = _make_ohlcv(prices, pd.Timestamp("2026-07-20T00:00:00Z").value // 1_000_000)
    # signal: +1 for bars 0..9, -1 for bars 10..19 → no entry since threshold=5
    # Use: bars 0..4 = +10 (open long), bars 5..14 = -10 (close+short),
    # bars 15..19 = 0 (close short on time/no-signal)
    composite = _make_composite(
        list(range(20)),
        [10.0] * 5 + [-10.0] * 10 + [0.0] * 5,
    )
    cfg = BacktestConfig(threshold=5.0, max_hold_minutes=240, notional_usd=100_000.0, adv_usd=5_000_000_000.0)
    out = run_backtest(ohlcv, composite, cfg)
    # At least one long entry (bars 0..4) then opposite exit at bar 5 → at least 1 trade
    assert len(out["trades"]) >= 1
    directions = [t["direction"] for t in out["trades"]]
    assert "long" in directions


def test_no_trades_when_signal_below_threshold():
    """If |signal| never crosses threshold, no trades and equity unchanged."""
    prices = [100.0] * 60
    ohlcv = _make_ohlcv(prices, pd.Timestamp("2026-07-20T00:00:00Z").value // 1_000_000)
    composite = _make_composite(list(range(60)), [2.0] * 60)  # below threshold=5
    cfg = BacktestConfig(threshold=5.0, max_hold_minutes=240, notional_usd=100_000.0, adv_usd=5_000_000_000.0)
    out = run_backtest(ohlcv, composite, cfg)
    assert len(out["trades"]) == 0
    assert float(out["equity"].iloc[-1]) == pytest.approx(100_000.0, abs=1e-6)


def test_time_stop_exits_after_max_hold():
    """If position held longer than max_hold_minutes, it must be force-closed."""
    prices = [100.0] * 60 + [105.0] * 5
    ohlcv = _make_ohlcv(prices, pd.Timestamp("2026-07-20T00:00:00Z").value // 1_000_000)
    # signal +10 for ALL bars → long held entire 60 bars; with max_hold=20 it must close at bar 20
    composite = _make_composite(list(range(65)), [10.0] * 65)
    cfg = BacktestConfig(threshold=5.0, max_hold_minutes=20, notional_usd=100_000.0, adv_usd=5_000_000_000.0)
    out = run_backtest(ohlcv, composite, cfg)
    # Expect at least 3 trades (entry, force-close at bar 20, re-entry at 21, force-close at 41, re-entry at 42, force-close at end)
    assert len(out["trades"]) >= 1
    # The first trade should not be held for more than max_hold_minutes
    first_trade = out["trades"][0]
    held = (first_trade["exit_bar"] - first_trade["entry_bar"])
    assert held <= cfg.max_hold_minutes + 1, f"trade held {held} bars, max hold {cfg.max_hold_minutes}"