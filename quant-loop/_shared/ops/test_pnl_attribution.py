"""Tests for _shared/ops/pnl_attribution.py (H18)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.ops.pnl_attribution import (
    AttributionRow,
    Fill,
    aggregate,
    attribute_fills,
)

# 2026-07-31 00:00:00 UTC and 2026-08-01 12:00:00 UTC
DAY1_TS = 1785456000.0
DAY2_TS = 1785585600.0


def _fill(ts, side, qty, price, strategy="mm", symbol="BTCUSDT", **kw):
    return Fill(ts=ts, strategy=strategy, symbol=symbol, side=side,
                qty=qty, price=price, **kw)


def test_simple_long_round_trip():
    fills = [
        _fill(DAY1_TS, "buy", 1.0, 100.0, fee=0.1),
        _fill(DAY1_TS + 60, "sell", 1.0, 110.0, fee=0.1),
    ]
    rows = attribute_fills(fills)
    assert len(rows) == 1
    r = rows[0]
    assert r.price_pnl == pytest.approx(10.0)
    assert r.fee_pnl == pytest.approx(-0.2)
    assert r.funding_pnl == 0.0
    assert r.slippage_pnl == 0.0
    assert r.closed_qty == pytest.approx(1.0)
    assert r.total_pnl == pytest.approx(9.8)


def test_short_round_trip():
    fills = [
        _fill(DAY1_TS, "sell", 2.0, 100.0),
        _fill(DAY1_TS + 60, "buy", 2.0, 90.0),
    ]
    (r,) = attribute_fills(fills)
    assert r.price_pnl == pytest.approx(20.0)


def test_slippage_bucket_against_reference_price():
    fills = [
        # bought 1 above the arrival mid -> -1 slippage
        _fill(DAY1_TS, "buy", 1.0, 101.0, reference_price=100.0),
        # sold 1 below the arrival mid -> -1 slippage; price move +4
        _fill(DAY1_TS + 60, "sell", 1.0, 104.0, reference_price=105.0),
    ]
    (r,) = attribute_fills(fills)
    assert r.slippage_pnl == pytest.approx(-2.0)
    assert r.price_pnl == pytest.approx(3.0)  # 104 - 101 on the matched leg
    assert r.total_pnl == pytest.approx(1.0)


def test_funding_is_a_cost_bucket():
    fills = [
        _fill(DAY1_TS, "buy", 1.0, 100.0),
        _fill(DAY1_TS + 60, "buy", 0.0 + 1.0, 100.0, funding=0.5),
        _fill(DAY1_TS + 120, "sell", 2.0, 100.0),
    ]
    rows = attribute_fills(fills)
    total = aggregate(rows, by=("strategy",))[0]
    assert total.funding_pnl == pytest.approx(-0.5)
    assert total.price_pnl == pytest.approx(0.0)
    assert total.total_pnl == pytest.approx(-0.5)


def test_buckets_attributed_to_realizing_day():
    fills = [
        _fill(DAY1_TS, "buy", 1.0, 100.0, fee=0.1),   # fee on day 1
        _fill(DAY2_TS, "sell", 1.0, 110.0, fee=0.1),  # price + fee on day 2
    ]
    rows = attribute_fills(fills)
    assert len(rows) == 2
    d1, d2 = rows
    assert d1.day == "2026-07-31"
    assert d1.fee_pnl == pytest.approx(-0.1)
    assert d1.price_pnl == 0.0
    assert d2.day == "2026-08-01"
    assert d2.price_pnl == pytest.approx(10.0)
    assert d2.fee_pnl == pytest.approx(-0.1)


def test_fifo_partial_close_and_flip():
    fills = [
        _fill(DAY1_TS, "buy", 2.0, 100.0),
        _fill(DAY1_TS + 1, "sell", 1.0, 110.0),   # close half: +10
        _fill(DAY1_TS + 2, "sell", 2.0, 90.0),    # close rest (-10) + open short 1
        _fill(DAY1_TS + 3, "buy", 1.0, 80.0),     # close short: +10
    ]
    (r,) = attribute_fills(fills)
    assert r.price_pnl == pytest.approx(10.0 - 10.0 + 10.0)
    assert r.closed_qty == pytest.approx(3.0)


def test_aggregate_by_strategy_across_symbols_and_days():
    fills = [
        _fill(DAY1_TS, "buy", 1.0, 100.0, symbol="BTCUSDT"),
        _fill(DAY2_TS, "sell", 1.0, 110.0, symbol="BTCUSDT"),
        _fill(DAY1_TS, "buy", 1.0, 50.0, symbol="ETHUSDT"),
        _fill(DAY1_TS + 60, "sell", 1.0, 40.0, symbol="ETHUSDT"),
        _fill(DAY1_TS, "buy", 1.0, 100.0, strategy="trend"),
        _fill(DAY1_TS + 60, "sell", 1.0, 105.0, strategy="trend"),
    ]
    by_strategy = aggregate(attribute_fills(fills), by=("strategy",))
    assert {r.strategy for r in by_strategy} == {"mm", "trend"}
    mm = next(r for r in by_strategy if r.strategy == "mm")
    assert mm.symbol == "" and mm.day == ""
    assert mm.price_pnl == pytest.approx(10.0 - 10.0)
    trend = next(r for r in by_strategy if r.strategy == "trend")
    assert trend.price_pnl == pytest.approx(5.0)


def test_aggregate_by_symbol_and_day():
    # Two strategies trading the same symbol on the same day; FIFO matching
    # is per strategy, and aggregation rolls the strategy dimension up.
    fills = [
        _fill(DAY1_TS, "buy", 1.0, 100.0, strategy="a"),
        _fill(DAY1_TS + 60, "sell", 1.0, 105.0, strategy="a"),
        _fill(DAY1_TS, "sell", 1.0, 105.0, strategy="b"),
        _fill(DAY1_TS + 60, "buy", 1.0, 100.0, strategy="b"),
    ]
    rows = aggregate(attribute_fills(fills), by=("symbol", "day"))
    assert len(rows) == 1
    assert rows[0].strategy == ""
    assert rows[0].price_pnl == pytest.approx(10.0)  # a: +5, b: +5


def test_aggregate_rejects_bad_keys():
    with pytest.raises(ValueError, match="invalid"):
        aggregate((), by=("strategy", "nope"))
    with pytest.raises(ValueError, match="at least one"):
        aggregate((), by=())


def test_fill_validation():
    with pytest.raises(ValueError):
        _fill(DAY1_TS, "hold", 1.0, 100.0)
    with pytest.raises(ValueError):
        _fill(DAY1_TS, "buy", 0.0, 100.0)


def test_row_total_is_sum_of_buckets():
    r = AttributionRow(strategy="s", symbol="x", day="2026-07-31",
                       price_pnl=10.0, fee_pnl=-1.0,
                       funding_pnl=-0.5, slippage_pnl=-2.0)
    assert r.total_pnl == pytest.approx(6.5)
