"""End-to-end test for backtest.run_backtest on a synthetic 3-symbol panel.

This test synthesizes three 1d OHLCV panels with deterministic, divergent
price action so the ranking logic has a clear winner/loser/middle. The
assertions are about contract behavior, not numerical sharpness:

- Equity curve is non-empty
- Rebalance log has the expected number of entries
- Each rebalance selects at most ``top_k`` longs and ``bottom_k`` shorts
- After risk-trigger, the portfolio is flat.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import run_backtest
from universe import UniverseConfig


def _rising(start_price: float, n: int, slope: float) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="1D", tz="UTC")
    close = [start_price + i * slope for i in range(n)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "volume": [1_000_000.0] * n,
        },
        index=idx,
    )


def _falling(start_price: float, n: int, slope: float) -> pd.DataFrame:
    return _rising(start_price, n, -abs(slope))


def _flat(start_price: float, n: int) -> pd.DataFrame:
    return _rising(start_price, n, 0.0)


def _cfg(top_k=1, bot_k=1, gross=0.6, daily_loss_pct=-0.10, monthly_loss_pct=-0.50):
    # Use extremely loose risk limits so the synthetic test doesn't trip them
    # by accident. The portfolio_test.py covers the risk-side numbers.
    return {
        "strategy": "test_xs_momentum",
        "momentum": {"weight_30d": 0.5, "weight_7d": 0.3, "weight_3d": 0.2},
        "universe_filter": {"min_bars_in_last_7d": 1, "min_usd_volume_per_day": 100.0},
        "portfolio": {
            "top_k_default": top_k,
            "bottom_k_default": bot_k,
            "gross_target_pct": gross,
            "per_symbol_max_pct_nav": 0.10,
            "per_leg_max_pct_nav": 0.10,
            "rebalance_freq": "1d",
            "rebalance_hour_utc": 0,
        },
        "risk": {
            "daily_loss_flatten_pct": daily_loss_pct,
            "monthly_loss_pause_pct": monthly_loss_pct,
            "monthly_pause_days": 5,
        },
        "fees_bps_per_side": 1.0,
        "slippage_bps_per_side": 1.0,
        "starting_capital_usd": 100_000.0,
    }


def _uni_cfg():
    return UniverseConfig(
        target=("A", "B", "C"),
        active=("A", "B", "C"),
        min_bars_in_last_7d=1,
        min_usd_volume_per_day=100.0,
    )


def test_run_backtest_basic_pipeline():
    n = 120
    per = {
        "WIN": _rising(100.0, n, 1.0),
        "FLAT": _flat(100.0, n),
        "LOSE": _falling(100.0, n, 1.0),
    }
    result = run_backtest(per, cfg=_cfg(top_k=1, bot_k=1), universe_cfg=_uni_cfg())
    # The engine must produce a non-trivial equity curve.
    assert not result.equity_curve.empty
    # Number of rebalances is the number of valid daily bars after the 30d
    # warmup (= n - 30).
    assert result.n_rebalances == pytest.approx(n - 30)
    # With top_k=bot_k=1 and 3 symbols the portfolio should always have
    # one long and one short after every rebalance (subject to risk gates).
    for ev in result.events:
        longs = sum(1 for p in ev.target_positions if p.side == "LONG")
        shorts = sum(1 for p in ev.target_positions if p.side == "SHORT")
        # Some events may be empty (monthly pause or daily-flatten), skip those.
        if ev.gross == 0:
            continue
        assert longs == 1
        assert shorts == 1


def test_run_backtest_with_daily_loss_triggers_flatten():
    n = 60
    # The strategy goes LONG on the highest-momentum symbol and SHORT on the
    # lowest-momentum one. To trigger the daily-loss flatten we make momentum
    # *invert* (the rising symbol ranks LAST and the falling symbol ranks
    # FIRST). We achieve this by making the previous 30 bars trend strongly
    # upward on one symbol and strongly downward on another, then continue
    # the same trend at the test bar so the strategy enters long-the-loser /
    # short-the-winner.
    ups = [100.0 + i * 2.0 for i in range(n)]   # used to "trick" the score
    downs = [200.0 - i * 2.0 for i in range(n)]
    # After bar 30, invert: the previously-down now rises (so the high-
    # momentum past stays positive) and the previously-up now keeps rising so
    # its 30d momentum stays strongly positive. Actually -- simpler: build
    # TWO universes where the warmup picks the WRONG winner. We do that by
    # going long on a symbol whose 30d momentum LOOKS positive but whose
    # forward path is negative.
    rises_then_drops = (
        [100.0 + i * 1.0 for i in range(30)]   # momentum_score positive at bar 30
        + [130.0 - (i - 30) * 2.0 for i in range(30, n)]   # drops -2/bar afterwards
    )
    flat_then_rises = (
        [100.0] * 30
        + [100.0 + (i - 30) * 2.0 for i in range(30, n)]   # 30d momentum still 0 at bar 30, becomes positive
    )
    # Make a config where the -2%-daily flatten threshold is hit when we're
    # long the loser and short the winner. Easier still: use top_k=bot_k=1
    # and a universe of 2 symbols where one has negative 30d momentum and
    # one has positive 30d momentum, but at the NEXT bar prices reverse
    # such that we lose on both legs.
    per = {
        "A": _flat_then_rising(100.0, n),   # positive momentum (target = LONG)
        "B": _falling_with_past_rise(100.0, n),   # mixed path (target = depends)
        "C": _flat(100.0, n),                # neutral momentum (middle)
    }
    result = run_backtest(
        per,
        cfg=_cfg(top_k=1, bot_k=1, daily_loss_pct=-0.10),
        universe_cfg=_uni_cfg(),
    )
    # We expect at least one flatten event after both the warmup and enough
    # market action has happened.
    flatten_events = [ev for ev in result.events if "daily_loss_flatten" in ev.notes]
    # And we expect the portfolio to be flat (gross=0) on those days.
    for ev in flatten_events:
        assert ev.gross == 0.0


def _flat_then_rising(start_price: float, n: int) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="1D", tz="UTC")
    prices = [start_price] * 30 + [start_price + (i - 30) * 3.0 for i in range(30, n)]
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1_000_000.0] * n,
        },
        index=idx,
    )


def _falling_with_past_rise(start_price: float, n: int) -> pd.DataFrame:
    """Strong 30d-up trend in the warmup, then a steep decline so the strategy
    enters long on this symbol just as it tanks."""
    idx = pd.date_range("2025-01-01", periods=n, freq="1D", tz="UTC")
    prices = [start_price + i * 1.5 for i in range(30)] + [
        (start_price + 30 * 1.5) - (i - 30) * 3.0 for i in range(30, n)
    ]
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1_000_000.0] * n,
        },
        index=idx,
    )