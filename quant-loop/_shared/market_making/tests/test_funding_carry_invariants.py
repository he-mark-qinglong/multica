"""Regression tests for funding carry model invariants.

These two invariants were violated by the naive model, producing a fake
PF=2.04 "profitable" strategy. The tests prevent regression:

  1. Funding is paid ONLY at settlement to whoever holds at that moment.
     A position stopped out before settlement collects ZERO funding.
  2. Stop-loss is checked on intraday 1m high/low with exit AT the SL level,
     not by capping the end-of-period price move.
"""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.strategy_sweeper import signal_funding_carry


def _make_fund(rates, prices):
    # rates: n entries; prices: n+1 entries (one per settlement point incl. next)
    ts = pd.date_range("2024-01-01", periods=len(prices), freq="8h", tz="UTC")
    fr = list(rates) + [0.0] * (len(prices) - len(rates))
    return pd.DataFrame({
        "ts": ts,
        "fundingRate": fr[:len(prices)],
        "markPrice": list(prices),
    })


def _make_bars_around(ts0, ts1, high_seq, low_seq, n=16):
    ts = pd.date_range(ts0, periods=n, freq="30min", tz="UTC")
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    ms = (ts - epoch) // pd.Timedelta(milliseconds=1)
    return pd.DataFrame({
        "open_time": ms,
        "high": high_seq,
        "low": low_seq,
    })


def test_sl_exit_collects_no_funding():
    """Position stopped out before settlement must NOT collect funding."""
    # Short entry at 100, price spikes to 102 → SL at 1% = 101 hit
    fund = _make_fund(rates=[0.001], prices=[100.0, 102.0])  # +10bp funding
    bars = _make_bars_around(
        fund.iloc[0]["ts"], fund.iloc[1]["ts"],
        high_seq=[100.0, 100.5, 101.5, 101.0] * 4,   # crosses 101 SL
        low_seq=[99.5, 99.8, 100.2, 100.5] * 4,
    )
    trades = signal_funding_carry(
        fund, {"threshold_bp": 1, "sl_bp": 100, "rt_fee_bp": 7}, bars_1m=bars,
    )
    assert len(trades) == 1
    t = trades[0]
    assert t["exit_reason"] == "sl"
    assert t["funding_bp"] == 0.0          # ← invariant 1: no funding on SL exit
    assert t["pnl_bp"] == pytest.approx(-100 - 7)  # SL 100bp + fees


def test_settle_exit_collects_funding():
    """Position held to settlement collects funding normally."""
    fund = _make_fund(rates=[0.001], prices=[100.0, 100.2])
    bars = _make_bars_around(
        fund.iloc[0]["ts"], fund.iloc[1]["ts"],
        high_seq=[100.0, 100.2] * 8,   # never crosses 101 SL
        low_seq=[99.8, 99.9] * 8,
    )
    trades = signal_funding_carry(
        fund, {"threshold_bp": 1, "sl_bp": 100, "rt_fee_bp": 7}, bars_1m=bars,
    )
    assert len(trades) == 1
    t = trades[0]
    assert t["exit_reason"] == "settle"
    assert t["funding_bp"] == pytest.approx(10.0)   # 10bp funding collected
    # short: price +0.2% against us → -20bp; net = 10 - 20 - 7
    assert t["pnl_bp"] == pytest.approx(10.0 - 20.0 - 7.0)


def test_sl_not_capped_by_end_price():
    """Dip below SL then recover must still count as SL exit (invariant 2)."""
    # Long entry at 100, price dips to 98 (below 99 SL) then recovers to 100.5
    fund = _make_fund(rates=[-0.001], prices=[100.0, 100.5])
    bars = _make_bars_around(
        fund.iloc[0]["ts"], fund.iloc[1]["ts"],
        high_seq=[100.0, 99.5, 99.0, 100.5] * 4,
        low_seq=[99.5, 98.0, 98.5, 100.0] * 4,   # dips to 98 < 99 SL
    )
    trades = signal_funding_carry(
        fund, {"threshold_bp": 1, "sl_bp": 100, "rt_fee_bp": 7}, bars_1m=bars,
    )
    assert len(trades) == 1
    t = trades[0]
    # End price moved +0.5% (favorable for long) — naive model would score +50bp.
    # Correct model: SL was hit intraday → exit at -100bp, no funding.
    assert t["exit_reason"] == "sl"
    assert t["pnl_bp"] == pytest.approx(-100 - 7)


def test_no_sl_when_disabled():
    """sl_bp=0 means no stop; all exits are 'settle'."""
    fund = _make_fund(rates=[0.001], prices=[100.0, 95.0])
    trades = signal_funding_carry(
        fund, {"threshold_bp": 1, "sl_bp": 0, "rt_fee_bp": 7},
    )
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "settle"
    # short: price -5% in our favor → +500bp; net = 10 + 500 - 7
    assert trades[0]["pnl_bp"] == pytest.approx(10.0 + 500.0 - 7.0)


def test_threshold_filters_small_funding():
    """Events below threshold produce no trades."""
    fund = _make_fund(rates=[0.0001], prices=[100.0, 100.1])  # 1bp
    trades = signal_funding_carry(
        fund, {"threshold_bp": 5, "sl_bp": 0, "rt_fee_bp": 7},
    )
    assert len(trades) == 0
