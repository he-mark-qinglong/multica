"""Tests for maker_simulator.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.maker_simulator import (
    MakerSimConfig, simulate_market_making,
)
from _shared.run_backtest import Trade


def _make_synthetic_aggtrades(n=200, base_price=50000.0):
    """Create oscillating aggTrades for a controlled simulation."""
    ts = pd.date_range("2026-04-19 00:00:00", periods=n, freq="5s", tz="UTC")
    # Oscillate ±50 USD to generate fills
    prices = [base_price + 50 * np.sin(i * 0.15) + np.random.randn() * 2
              for i in range(n)]
    df = pd.DataFrame({
        "ts": ts,
        "price": prices,
        "qty": [0.5] * n,
        "is_buyer_maker": [i % 3 == 0 for i in range(n)],  # alternating sides
    })
    return df


def test_simulator_returns_tuple():
    df = _make_synthetic_aggtrades(200)
    config = MakerSimConfig(
        start_ts="2026-04-19",
        end_ts="2026-04-20",
        size_usd=500.0,
        max_inventory_usd=2000.0,
        base_spread_bp=3.0,
        tp_bp=3.0,
        sl_bp=8.0,
        max_hold_seconds=60.0,
    )
    result = simulate_market_making(df, config)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_simulator_trades_format():
    df = _make_synthetic_aggtrades(200)
    config = MakerSimConfig(
        start_ts="2026-04-19",
        end_ts="2026-04-20",
        size_usd=500.0,
        base_spread_bp=3.0,
        tp_bp=3.0,
        sl_bp=8.0,
        max_hold_seconds=60.0,
    )
    trades, metrics = simulate_market_making(df, config)
    assert isinstance(trades, list)
    for t in trades:
        assert isinstance(t, Trade)
        assert t.entry_ts <= t.exit_ts
        assert t.direction in ("long", "short")


def test_simulator_metrics_keys():
    df = _make_synthetic_aggtrades(200)
    config = MakerSimConfig(
        start_ts="2026-04-19",
        end_ts="2026-04-20",
        size_usd=500.0,
        max_hold_seconds=60.0,
    )
    _, metrics = simulate_market_making(df, config)
    expected_keys = {"n_trades", "sharpe_daily", "fill_rate", "maker_ratio",
                     "exit_reasons", "profit_factor", "max_drawdown_pct"}
    assert expected_keys.issubset(metrics.keys())


def test_simulator_empty_data():
    df = pd.DataFrame({"ts": [], "price": [], "qty": [], "is_buyer_maker": []})
    config = MakerSimConfig(start_ts="2026-04-19", end_ts="2026-04-20")
    trades, metrics = simulate_market_making(df, config)
    assert trades == []
    assert "error" in metrics or metrics.get("n_trades", 0) == 0


def test_simulator_tiny_data():
    ts = pd.date_range("2026-04-19", periods=3, freq="1s", tz="UTC")
    df = pd.DataFrame({
        "ts": ts, "price": [50000, 50001, 50002],
        "qty": [0.5]*3, "is_buyer_maker": [True, False, True],
    })
    config = MakerSimConfig(start_ts="2026-04-19", end_ts="2026-04-20")
    trades, _ = simulate_market_making(df, config)
    # Too few trades → either empty or minimal
    assert isinstance(trades, list)
