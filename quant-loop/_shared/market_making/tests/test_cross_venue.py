"""Tests for cross_venue.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.market_making.cross_venue import (
    BookTicker,
    VenueFees,
    compute_cross_venue_quote,
    cross_spread_bp,
    taker_edge_bp,
)

FEES = VenueFees(maker_bp=0.0, taker_bp=4.0)
ZERO = VenueFees(maker_bp=0.0, taker_bp=0.0)

# Venue A tight around 100; venue B quoted higher.
A = BookTicker("A", bid_price=99.99, bid_size=5.0, ask_price=100.01,
               ask_size=5.0, timestamp=1.0)
B_HIGH = BookTicker("B", bid_price=100.09, bid_size=3.0, ask_price=100.11,
                    ask_size=3.0, timestamp=1.0)


def test_spread_bp_signed():
    # a.ask 100.01 vs b.bid 100.09 → A cheaper → negative spread
    s = cross_spread_bp(A, B_HIGH)
    mid = (100.01 + 100.09) / 2
    assert s == pytest.approx((100.01 - 100.09) / mid * 1e4)
    assert s < 0.0


def test_taker_edge_net_of_fees():
    # buy A.ask 100.01, sell B.bid 100.09: gross ~8bp, fees 2×4bp → ~0
    edge = taker_edge_bp(A, B_HIGH, FEES, FEES)
    gross = (100.09 - 100.01) / ((100.01 + 100.09) / 2) * 1e4
    assert edge == pytest.approx(gross - 8.0, abs=0.05)
    # zero fees → edge equals gross
    assert taker_edge_bp(A, B_HIGH, ZERO, ZERO) == pytest.approx(gross)


def test_profitable_when_dislocation_exceeds_fees():
    b_far = BookTicker("B", bid_price=100.30, bid_size=3.0, ask_price=100.32,
                       ask_size=3.0, timestamp=1.0)
    q = compute_cross_venue_quote(A, b_far, FEES, FEES)
    assert q.profitable
    assert q.arb_buy_a_sell_b_bp > 0.0


def test_not_profitable_within_fee_band():
    q = compute_cross_venue_quote(A, B_HIGH, FEES, FEES)
    assert not q.profitable
    assert q.arb_buy_a_sell_b_bp < 0.0
    assert q.arb_buy_b_sell_a_bp < 0.0


def test_quote_bid_anchored_to_b_ask_minus_fees_buffer():
    buffer_bp = 2.0
    q = compute_cross_venue_quote(A, B_HIGH, FEES, FEES,
                                  buffer_bp=buffer_bp, tick_size=0.01)
    assert q.quote_bid_a is not None
    # raw anchor: 100.11 * (1 - 4bp - 2bp) / (1 + 0) = 100.11 * 0.9994
    raw = 100.11 * (1.0 - 0.0004 - 0.0002)
    assert q.quote_bid_a == pytest.approx(raw, abs=0.01)
    # floored to tick, never above raw
    assert q.quote_bid_a <= raw + 1e-12
    assert abs(q.quote_bid_a / 0.01 - round(q.quote_bid_a / 0.01)) < 1e-9
    # expected edge at the quote ≈ buffer (minus tick-rounding slack)
    assert q.edge_at_quote_bp is not None
    assert q.edge_at_quote_bp >= buffer_bp - 1e-9
    assert q.edge_at_quote_bp < buffer_bp + 1.0  # slack < one tick in bp


def test_quote_ask_anchored_to_b_bid_plus_fees_buffer():
    q = compute_cross_venue_quote(A, B_HIGH, FEES, FEES,
                                  buffer_bp=2.0, tick_size=0.01)
    assert q.quote_ask_a is not None
    raw = 100.09 * (1.0 + 0.0004 + 0.0002)
    assert q.quote_ask_a >= raw - 1e-12  # ceiled, never below raw
    assert q.quote_ask_a == pytest.approx(raw, abs=0.01)


def test_quote_below_b_ask():
    # the passive bid must never cross B's ask (would be taker, not maker)
    q = compute_cross_venue_quote(A, B_HIGH, FEES, FEES, buffer_bp=1.0)
    assert q.quote_bid_a < B_HIGH.ask_price


def test_invalid_book_yields_none_quotes():
    crossed = BookTicker("B", bid_price=100.20, bid_size=1.0,
                         ask_price=100.10, ask_size=1.0, timestamp=1.0)
    q = compute_cross_venue_quote(A, crossed, FEES, FEES)
    assert q.quote_bid_a is None and q.quote_ask_a is None
    assert q.edge_at_quote_bp is None
    assert not q.profitable


def test_nonpositive_prices_invalid():
    bad = BookTicker("B", bid_price=0.0, bid_size=1.0, ask_price=100.0,
                     ask_size=1.0, timestamp=1.0)
    q = compute_cross_venue_quote(A, bad, FEES, FEES)
    assert q.quote_bid_a is None
