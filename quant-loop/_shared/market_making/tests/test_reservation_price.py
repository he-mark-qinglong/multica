"""Tests for reservation_price.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.reservation_price import (
    reservation_price, rolling_sigma,
)


# ---- reservation_price ----

def test_zero_inventory_no_shift():
    r = reservation_price(fair_value=100.0, inventory_qty=0.0,
                          sigma=0.01, time_remaining=300, gamma=0.1)
    assert r == pytest.approx(100.0)

def test_zero_sigma_no_shift():
    r = reservation_price(fair_value=100.0, inventory_qty=1.0,
                          sigma=0.0, time_remaining=300, gamma=0.1)
    assert r == pytest.approx(100.0)

def test_zero_time_no_shift():
    r = reservation_price(fair_value=100.0, inventory_qty=1.0,
                          sigma=0.01, time_remaining=0, gamma=0.1)
    assert r == pytest.approx(100.0)

def test_long_inventory_lowers_price():
    r = reservation_price(fair_value=100.0, inventory_qty=1.0,
                          sigma=0.01, time_remaining=300, gamma=0.1)
    assert r < 100.0

def test_short_inventory_raises_price():
    r = reservation_price(fair_value=100.0, inventory_qty=-1.0,
                          sigma=0.01, time_remaining=300, gamma=0.1)
    assert r > 100.0

def test_symmetry():
    r_long = reservation_price(100.0, 1.0, 0.01, 300, 0.1)
    r_short = reservation_price(100.0, -1.0, 0.01, 300, 0.1)
    assert (100.0 - r_long) == pytest.approx(r_short - 100.0)


# ---- rolling_sigma ----

def test_sigma_empty():
    assert rolling_sigma(pd.DataFrame()) == 0.0

def test_sigma_single_row():
    df = pd.DataFrame({"ts": [pd.Timestamp("2026-04-20", tz="UTC")],
                       "price": [100.0]})
    assert rolling_sigma(df) == 0.0

def test_sigma_constant_prices():
    ts = pd.date_range("2026-04-20", periods=10, freq="1s", tz="UTC")
    df = pd.DataFrame({"ts": ts, "price": [100.0] * 10})
    assert rolling_sigma(df) == 0.0

def test_sigma_volatile():
    ts = pd.date_range("2026-04-20", periods=100, freq="100ms", tz="UTC")
    prices = [100.0 * (1 + 0.001 * ((-1) ** i)) for i in range(100)]
    df = pd.DataFrame({"ts": ts, "price": prices})
    sig = rolling_sigma(df)
    assert sig > 0.0
