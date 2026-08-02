"""Tests for _shared/execution/matching_engine.py."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.execution.matching_engine import Fill, MatchingEngine, Order


# --- Order validation ---------------------------------------------------------
def test_order_rejects_bad_side():
    with pytest.raises(ValueError, match="side"):
        Order(ack_id="1", side="invalid", order_type="limit", price=100, qty=1)


def test_order_rejects_bad_type():
    with pytest.raises(ValueError, match="order_type"):
        Order(ack_id="1", side="buy", order_type="stop", price=100, qty=1)


def test_order_rejects_zero_qty():
    with pytest.raises(ValueError, match="qty"):
        Order(ack_id="1", side="buy", order_type="limit", price=100, qty=0)


def test_limit_order_rejects_zero_price():
    with pytest.raises(ValueError, match="price"):
        Order(ack_id="1", side="buy", order_type="limit", price=0, qty=1)


def test_market_order_allows_zero_price():
    """Market orders don't need a price."""
    o = Order(ack_id="1", side="buy", order_type="market", price=0, qty=1)
    assert o.order_type == "market"


# --- Basic limit-order matching -----------------------------------------------
def test_limit_buy_rests_when_no_seller():
    engine = MatchingEngine()
    fills = engine.process(Order("b1", "buy", "limit", price=100, qty=5, timestamp=1))
    assert fills == []
    assert engine.best_bid() == 100
    assert engine.best_ask() is None


def test_limit_sell_matches_existing_buy():
    engine = MatchingEngine()
    engine.process(Order("b1", "buy", "limit", price=100, qty=5, timestamp=1))
    fills = engine.process(Order("s1", "sell", "limit", price=100, qty=3, timestamp=2))
    assert len(fills) == 1
    assert fills[0].price == 100
    assert fills[0].qty == 3
    assert fills[0].taker_side == "sell"
    assert fills[0].maker_ack_id == "b1"
    assert fills[0].taker_ack_id == "s1"
    # remaining buy qty should be 2
    assert engine.bid_depth() == pytest.approx(2.0)


def test_limit_sell_rests_above_best_bid():
    """Sell limit at 105 doesn't cross bid at 100."""
    engine = MatchingEngine()
    engine.process(Order("b1", "buy", "limit", price=100, qty=5, timestamp=1))
    fills = engine.process(Order("s1", "sell", "limit", price=105, qty=3, timestamp=2))
    assert fills == []
    assert engine.best_bid() == 100
    assert engine.best_ask() == 105


def test_full_cross_clears_both_sides():
    engine = MatchingEngine()
    engine.process(Order("b1", "buy", "limit", price=100, qty=5, timestamp=1))
    fills = engine.process(Order("s1", "sell", "limit", price=100, qty=5, timestamp=2))
    assert len(fills) == 1
    assert fills[0].qty == 5
    assert engine.best_bid() is None
    assert engine.best_ask() is None


# --- Price priority -----------------------------------------------------------
def test_price_priority_best_ask_matches_first():
    """Two asks: lower price fills first regardless of arrival time."""
    engine = MatchingEngine()
    engine.process(Order("a1", "sell", "limit", price=102, qty=5, timestamp=1))
    engine.process(Order("a2", "sell", "limit", price=100, qty=5, timestamp=2))
    assert engine.best_ask() == 100
    # Market buy sweeps from best ask (100)
    fills = engine.process(Order("b1", "buy", "market", qty=3, timestamp=3))
    assert len(fills) == 1
    assert fills[0].price == 100
    assert fills[0].maker_ack_id == "a2"


def test_price_priority_best_bid_matches_first():
    """Two bids: higher price fills first."""
    engine = MatchingEngine()
    engine.process(Order("b1", "buy", "limit", price=100, qty=5, timestamp=1))
    engine.process(Order("b2", "buy", "limit", price=102, qty=5, timestamp=2))
    assert engine.best_bid() == 102
    fills = engine.process(Order("s1", "sell", "market", qty=3, timestamp=3))
    assert len(fills) == 1
    assert fills[0].price == 102
    assert fills[0].maker_ack_id == "b2"


# --- Time priority (FIFO) -----------------------------------------------------
def test_fifo_same_price_level():
    """Two buys at same price: earlier arrival fills first."""
    engine = MatchingEngine()
    engine.process(Order("b1", "buy", "limit", price=100, qty=5, timestamp=1))
    engine.process(Order("b2", "buy", "limit", price=100, qty=5, timestamp=2))
    fills = engine.process(Order("s1", "sell", "market", qty=3, timestamp=3))
    assert len(fills) == 1
    assert fills[0].maker_ack_id == "b1"  # earlier arrival


def test_fifo_partial_fill_chain():
    """Market sell sweeps through multiple FIFO orders at the same level."""
    engine = MatchingEngine()
    engine.process(Order("b1", "buy", "limit", price=100, qty=2, timestamp=1))
    engine.process(Order("b2", "buy", "limit", price=100, qty=3, timestamp=2))
    fills = engine.process(Order("s1", "sell", "market", qty=4, timestamp=3))
    assert len(fills) == 2
    assert fills[0].maker_ack_id == "b1"
    assert fills[0].qty == 2
    assert fills[1].maker_ack_id == "b2"
    assert fills[1].qty == 2
    # b2 should have 1 remaining
    assert engine.bid_depth() == pytest.approx(1.0)


# --- Market order sweeps multiple levels --------------------------------------
def test_market_buy_sweeps_multiple_ask_levels():
    engine = MatchingEngine()
    engine.process(Order("a1", "sell", "limit", price=100, qty=2, timestamp=1))
    engine.process(Order("a2", "sell", "limit", price=101, qty=3, timestamp=2))
    engine.process(Order("a3", "sell", "limit", price=102, qty=5, timestamp=3))
    fills = engine.process(Order("b1", "buy", "market", qty=7, timestamp=4))
    assert len(fills) == 3
    assert fills[0].price == 100
    assert fills[0].qty == 2
    assert fills[1].price == 101
    assert fills[1].qty == 3
    assert fills[2].price == 102
    assert fills[2].qty == 2
    # a3 should have 3 remaining
    assert engine.ask_depth() == pytest.approx(3.0)


def test_market_buy_exhausts_book():
    """Market buy larger than all available liquidity."""
    engine = MatchingEngine()
    engine.process(Order("a1", "sell", "limit", price=100, qty=2, timestamp=1))
    fills = engine.process(Order("b1", "buy", "market", qty=10, timestamp=2))
    assert len(fills) == 1
    assert fills[0].qty == 2  # only 2 available
    assert engine.best_ask() is None
    assert engine.best_bid() is None  # nothing rests for a market order


# --- Limit order crosses then rests -------------------------------------------
def test_limit_buy_crosses_partial_then_rests():
    engine = MatchingEngine()
    engine.process(Order("a1", "sell", "limit", price=100, qty=2, timestamp=1))
    fills = engine.process(
        Order("b1", "buy", "limit", price=105, qty=5, timestamp=2)
    )
    assert len(fills) == 1
    assert fills[0].qty == 2
    assert fills[0].price == 100
    # remaining 3 should rest at 105 (aggressive limit acts as market then rests)
    assert engine.best_bid() == 105
    assert engine.bid_depth() == pytest.approx(3.0)


# --- Duplicate ack guard ------------------------------------------------------
def test_duplicate_ack_is_noop():
    engine = MatchingEngine()
    o = Order("b1", "buy", "limit", price=100, qty=5, timestamp=1)
    fills1 = engine.process(o)
    assert fills1 == []  # rested, no fill
    fills2 = engine.process(o)  # same ack_id
    assert fills2 == []  # ignored
    assert engine.bid_depth() == pytest.approx(5.0)  # not doubled


def test_duplicate_market_ack_is_noop():
    engine = MatchingEngine()
    engine.process(Order("a1", "sell", "limit", price=100, qty=5, timestamp=1))
    fills1 = engine.process(Order("b1", "buy", "market", qty=3, timestamp=2))
    assert len(fills1) == 1
    # replay the same ack_id — should be ignored
    fills2 = engine.process(Order("b1", "buy", "market", qty=3, timestamp=2))
    assert fills2 == []
    assert engine.ask_depth() == pytest.approx(2.0)  # not double-filled


# --- Cancel -------------------------------------------------------------------
def test_cancel_removes_resting_order():
    engine = MatchingEngine()
    engine.process(Order("b1", "buy", "limit", price=100, qty=5, timestamp=1))
    assert engine.cancel("b1") is True
    assert engine.best_bid() is None
    assert engine.bid_depth() == 0


def test_cancel_unknown_ack_returns_false():
    engine = MatchingEngine()
    assert engine.cancel("nonexistent") is False


def test_cancel_one_order_at_level_keeps_others():
    engine = MatchingEngine()
    engine.process(Order("b1", "buy", "limit", price=100, qty=5, timestamp=1))
    engine.process(Order("b2", "buy", "limit", price=100, qty=3, timestamp=2))
    engine.cancel("b1")
    assert engine.best_bid() == 100
    assert engine.bid_depth() == pytest.approx(3.0)


# --- Mid price & depth --------------------------------------------------------
def test_mid_price():
    engine = MatchingEngine()
    assert engine.mid_price() is None
    engine.process(Order("b1", "buy", "limit", price=100, qty=1, timestamp=1))
    engine.process(Order("a1", "sell", "limit", price=102, qty=1, timestamp=2))
    assert engine.mid_price() == pytest.approx(101.0)


# --- Integration: multi-order scenario ----------------------------------------
def test_full_scenario():
    engine = MatchingEngine()
    # Seed the book
    engine.process(Order("s1", "sell", "limit", price=101, qty=10, timestamp=1))
    engine.process(Order("s2", "sell", "limit", price=102, qty=10, timestamp=2))
    engine.process(Order("b1", "buy", "limit", price=99, qty=10, timestamp=3))
    engine.process(Order("b2", "buy", "limit", price=98, qty=10, timestamp=4))
    assert engine.best_bid() == 99
    assert engine.best_ask() == 101
    assert engine.mid_price() == pytest.approx(100.0)
    # New aggressive sell at 99 (crosses best bid)
    fills = engine.process(Order("s3", "sell", "limit", price=99, qty=15, timestamp=5))
    assert len(fills) == 1
    assert fills[0].price == 99
    assert fills[0].qty == 10  # b1 fully consumed
    assert fills[0].maker_ack_id == "b1"
    # remaining 5 of s3 rests at 99; b2 (at 98) is still resting
    assert engine.best_bid() == 98
    assert engine.best_ask() == 99
    assert engine.ask_depth() == pytest.approx(5 + 10 + 10)  # 5 + s1 + s2
