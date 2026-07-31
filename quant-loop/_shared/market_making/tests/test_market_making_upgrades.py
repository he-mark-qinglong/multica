"""Tests for optimal_spread.py, multi_level.py, queue_position.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import math
import pandas as pd
import pytest

from _shared.market_making.optimal_spread import (
    OptimalSpreadParams, optimal_half_spread, estimate_kappa,
)
from _shared.market_making.multi_level import (
    MultiLevelParams, TierConfig, generate_multi_level_quotes,
)
from _shared.market_making.queue_position import (
    QueueParams, fill_probability, expected_fill_value, optimal_quote_aggressiveness,
)


# ---- optimal_spread ----

def test_spread_positive():
    s = optimal_half_spread(sigma=0.001, time_remaining=300)
    assert s > 0

def test_spread_zero_vol_returns_min():
    s = optimal_half_spread(sigma=0.0, time_remaining=300)
    assert s > 0  # min floor

def test_higher_vol_wider_spread():
    # Use params with higher ceiling to distinguish vol levels
    from _shared.market_making.optimal_spread import OptimalSpreadParams
    p = OptimalSpreadParams(max_spread_bp=500.0, kappa=100.0)
    low = optimal_half_spread(0.0005, 300, p)
    high = optimal_half_spread(0.005, 300, p)
    assert high > low

def test_estimate_kappa():
    k = estimate_kappa(fills_per_second=10.0, our_quote_share=0.1)
    assert k > 0
    assert k == pytest.approx(9.0)


# ---- multi_level ----

def test_multi_level_3_tiers():
    # base_half_spread in PRICE units (10 USD on 50000 = 2bp)
    quotes = generate_multi_level_quotes(
        reservation_price=50000.0, base_half_spread=10.0,
        total_size_usd=1000.0, inventory_skew_offset=0.0,
        tick_size=0.01,
    )
    assert len(quotes) == 3
    # Tier 0 is tightest
    assert quotes[0].bid_price > quotes[1].bid_price

def test_multi_level_size_decreasing():
    quotes = generate_multi_level_quotes(
        reservation_price=50000.0, base_half_spread=10.0,
        total_size_usd=1000.0, inventory_skew_offset=0.0,
        tick_size=0.01,
    )
    assert quotes[0].bid_size > quotes[2].bid_size

def test_multi_level_inventory_skew():
    no_skew = generate_multi_level_quotes(
        50000.0, 10.0, 1000.0, 0.0, 0.01)
    with_skew = generate_multi_level_quotes(
        50000.0, 10.0, 1000.0, 5.0, 0.01)
    # Skewed quotes should be lower
    assert with_skew[0].bid_price < no_skew[0].bid_price


# ---- queue_position ----

def test_fill_prob_decreases_with_time():
    p0 = fill_probability(0, 0, 0.13)
    p10 = fill_probability(10, 0, 0.13)
    assert p0 > p10

def test_fill_prob_decreases_with_distance():
    p0 = fill_probability(0, 0, 0.13)
    p1 = fill_probability(0, 1, 0.13)
    assert p0 > p1

def test_expected_fill_value():
    ev = expected_fill_value(0.5, 4.0)
    assert ev == pytest.approx(2.0)

def test_aggressiveness_high_edge():
    assert optimal_quote_aggressiveness(10.0, 1.74) == 0  # aggressive

def test_aggressiveness_negative_edge():
    assert optimal_quote_aggressiveness(1.0, 2.0) == -1  # don't quote

def test_aggressiveness_moderate_edge():
    # net_edge = 5.0 - 1.74 = 3.26 > 1.74 → moderate (1 tick)
    assert optimal_quote_aggressiveness(5.0, 1.74) == 1  # moderate
