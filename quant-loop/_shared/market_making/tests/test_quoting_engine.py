"""Tests for quoting_engine.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pandas as pd
import pytest

from _shared.market_making.quoting_engine import (
    Quote, QuotingParams, generate_quotes,
)
from _shared.market_making.inventory import InventoryState, empty_inventory

TS = pd.Timestamp("2026-04-20 12:00:00", tz="UTC")
PARAMS = QuotingParams(tick_size=0.01)


# ---- null guards ----

def test_none_when_mcls_zero():
    inv = empty_inventory(max_inventory=10.0)
    q = generate_quotes(
        reservation_price=100.0, sigma=0.001,
        inventory_state=inv, adverse_selection_penalty_bp=0.0,
        mcls_size_multiplier=0.0, params=PARAMS, timestamp=TS,
    )
    assert q is None

def test_none_when_price_zero():
    inv = empty_inventory(max_inventory=10.0)
    q = generate_quotes(
        reservation_price=0.0, sigma=0.001,
        inventory_state=inv, adverse_selection_penalty_bp=0.0,
        mcls_size_multiplier=1.0, params=PARAMS, timestamp=TS,
    )
    assert q is None


# ---- symmetric quotes (flat inventory) ----

def test_symmetric_when_flat():
    inv = empty_inventory(max_inventory=10.0)
    q = generate_quotes(
        reservation_price=100.0, sigma=0.0,
        inventory_state=inv, adverse_selection_penalty_bp=0.0,
        mcls_size_multiplier=1.0, params=PARAMS, timestamp=TS,
    )
    assert q is not None
    mid = (q.bid_price + q.ask_price) / 2
    assert mid == pytest.approx(100.0, abs=0.02)
    assert q.bid_price < 100.0
    assert q.ask_price > 100.0


# ---- inventory skew ----

def test_long_inventory_shifts_down():
    flat_inv = empty_inventory(max_inventory=10.0)
    long_inv = InventoryState(net_qty=5.0, max_inventory=10.0)
    q_flat = generate_quotes(100.0, 0.0, flat_inv, 0.0, 1.0, PARAMS, TS)
    q_long = generate_quotes(100.0, 0.0, long_inv, 0.0, 1.0, PARAMS, TS)
    assert q_long is not None and q_flat is not None
    # Long → both quotes shift down
    assert q_long.bid_price < q_flat.bid_price
    assert q_long.ask_price < q_flat.ask_price

def test_short_inventory_shifts_up():
    flat_inv = empty_inventory(max_inventory=10.0)
    short_inv = InventoryState(net_qty=-5.0, max_inventory=10.0)
    q_flat = generate_quotes(100.0, 0.0, flat_inv, 0.0, 1.0, PARAMS, TS)
    q_short = generate_quotes(100.0, 0.0, short_inv, 0.0, 1.0, PARAMS, TS)
    assert q_short is not None and q_flat is not None
    assert q_short.bid_price > q_flat.bid_price
    assert q_short.ask_price > q_flat.ask_price


# ---- adverse selection widens spread ----

def test_penalty_widens_spread():
    inv = empty_inventory(max_inventory=10.0)
    q_narrow = generate_quotes(100.0, 0.0, inv, 0.0, 1.0, PARAMS, TS)
    q_wide = generate_quotes(100.0, 0.0, inv, 5.0, 1.0, PARAMS, TS)
    assert q_narrow is not None and q_wide is not None
    spread_narrow = q_narrow.ask_price - q_narrow.bid_price
    spread_wide = q_wide.ask_price - q_wide.bid_price
    assert spread_wide > spread_narrow


# ---- tick alignment ----

def test_prices_tick_aligned():
    inv = empty_inventory(max_inventory=10.0)
    q = generate_quotes(99.987, 0.001, inv, 2.0, 1.0, PARAMS, TS)
    assert q is not None
    # All prices should be multiples of tick_size
    for p in [q.bid_price, q.ask_price]:
        remainder = (p * 100) % 1  # 0.01 tick → *100
        assert abs(remainder) < 0.01 or abs(remainder - 1) < 0.01


# ---- inventory limit → one-sided ----

def test_at_long_limit_only_asks():
    inv = InventoryState(net_qty=10.0, max_inventory=10.0)
    q = generate_quotes(100.0, 0.0, inv, 0.0, 1.0, PARAMS, TS)
    assert q is not None
    assert q.bid_size == 0.0
    assert q.ask_size > 0.0

def test_at_short_limit_only_bids():
    inv = InventoryState(net_qty=-10.0, max_inventory=10.0)
    q = generate_quotes(100.0, 0.0, inv, 0.0, 1.0, PARAMS, TS)
    assert q is not None
    assert q.ask_size == 0.0
    assert q.bid_size > 0.0
