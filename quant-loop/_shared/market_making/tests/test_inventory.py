"""Tests for inventory.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pandas as pd
import pytest

from _shared.market_making.inventory import (
    InventoryState, empty_inventory, flatten_required,
    inventory_skew, update_inventory,
)

TS = pd.Timestamp("2026-04-20 12:00:00", tz="UTC")


# ---- InventoryState properties ----

def test_inventory_ratio_flat():
    inv = empty_inventory(max_inventory=10.0)
    assert inv.inventory_ratio == 0.0

def test_inventory_ratio_long():
    inv = InventoryState(net_qty=5.0, max_inventory=10.0)
    assert inv.inventory_ratio == pytest.approx(0.5)

def test_inventory_ratio_clipped():
    inv = InventoryState(net_qty=15.0, max_inventory=10.0)
    assert inv.inventory_ratio == 1.0

def test_is_at_limit():
    inv = InventoryState(net_qty=10.0, max_inventory=10.0)
    assert inv.is_at_limit

def test_not_at_limit():
    inv = InventoryState(net_qty=5.0, max_inventory=10.0)
    assert not inv.is_at_limit

def test_is_flat():
    assert empty_inventory().is_flat


# ---- update_inventory ----

def test_buy_fill_increases_qty():
    inv = empty_inventory(max_inventory=10.0)
    inv2 = update_inventory(inv, fill_qty=2.0, fill_price=100.0, ts=TS)
    assert inv2.net_qty == pytest.approx(2.0)
    assert inv2.avg_price == pytest.approx(100.0)

def test_sell_fill_decreases_qty():
    inv = InventoryState(net_qty=2.0, avg_price=100.0, max_inventory=10.0)
    inv2 = update_inventory(inv, fill_qty=-1.0, fill_price=102.0, ts=TS)
    assert inv2.net_qty == pytest.approx(1.0)

def test_vwap_avg_price():
    inv = InventoryState(net_qty=2.0, avg_price=100.0, max_inventory=10.0)
    inv2 = update_inventory(inv, fill_qty=2.0, fill_price=110.0, ts=TS)
    # (2*100 + 2*110) / 4 = 105
    assert inv2.avg_price == pytest.approx(105.0)

def test_flat_after_offset():
    inv = InventoryState(net_qty=2.0, avg_price=100.0, max_inventory=10.0)
    inv2 = update_inventory(inv, fill_qty=-2.0, fill_price=102.0, ts=TS)
    assert inv2.is_flat
    assert inv2.open_since is None

def test_open_since_tracked():
    inv = empty_inventory(max_inventory=10.0)
    inv2 = update_inventory(inv, fill_qty=1.0, fill_price=100.0, ts=TS)
    assert inv2.open_since == TS


# ---- inventory_skew ----

def test_skew_positive_long():
    assert inventory_skew(5.0, 10.0) == pytest.approx(0.5)

def test_skew_negative_short():
    assert inventory_skew(-5.0, 10.0) == pytest.approx(-0.5)

def test_skew_clipped():
    assert inventory_skew(20.0, 10.0) == 1.0


# ---- flatten_required ----

def test_flatten_at_limit():
    inv = InventoryState(net_qty=10.0, max_inventory=10.0, avg_price=100.0,
                         open_since=TS)
    assert flatten_required(inv, current_price=100.0,
                            max_hold_seconds=300, current_ts=TS)

def test_flatten_time_exceeded():
    inv = InventoryState(net_qty=1.0, max_inventory=10.0, avg_price=100.0,
                         open_since=TS)
    later = TS + pd.Timedelta(seconds=301)
    assert flatten_required(inv, current_price=100.0,
                            max_hold_seconds=300, current_ts=later)

def test_flatten_stop_loss():
    inv = InventoryState(net_qty=1.0, max_inventory=10.0, avg_price=100.0,
                         open_since=TS)
    # 15bp loss
    assert flatten_required(inv, current_price=99.85,
                            max_hold_seconds=300, sl_bp=10.0, current_ts=TS)

def test_no_flatten_fresh():
    inv = InventoryState(net_qty=1.0, max_inventory=10.0, avg_price=100.0,
                         open_since=TS)
    assert not flatten_required(inv, current_price=100.5,
                                max_hold_seconds=300, current_ts=TS)

def test_no_flatten_when_flat():
    inv = empty_inventory(max_inventory=10.0)
    assert not flatten_required(inv, current_price=100.0,
                                max_hold_seconds=300, current_ts=TS)
