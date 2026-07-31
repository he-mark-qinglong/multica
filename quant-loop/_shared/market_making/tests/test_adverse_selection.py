"""Tests for adverse_selection.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pandas as pd
import pytest

from _shared.market_making.adverse_selection import (
    AdverseSelectionParams, AdverseSelectionState,
    ASK_LIFTED, BID_HIT,
    belief_update, decay_penalty, empty_state,
    is_quoting_allowed, on_fill,
)

TS0 = pd.Timestamp("2026-04-20 12:00:00", tz="UTC")
PARAMS = AdverseSelectionParams()


# ---- on_fill ----

def test_single_fill_increases_penalty():
    s = on_fill(empty_state(), BID_HIT, TS0, PARAMS)
    assert s.penalty_bp > 0.0
    assert s.cooldown_until is None  # no sweep yet

def test_consecutive_fills_count():
    s = empty_state()
    for i in range(3):
        s = on_fill(s, BID_HIT, TS0 + pd.Timedelta(seconds=i), PARAMS)
    assert s.consecutive_same_side == 3
    assert s.cooldown_until is not None  # sweep triggered

def test_different_side_resets():
    s = on_fill(empty_state(), BID_HIT, TS0, PARAMS)
    s = on_fill(s, ASK_LIFTED, TS0 + pd.Timedelta(seconds=1), PARAMS)
    assert s.consecutive_same_side == 1

def test_penalty_capped():
    p = AdverseSelectionParams(fill_penalty_bp=100.0, max_penalty_bp=10.0,
                               sweep_threshold=999)
    s = on_fill(empty_state(), BID_HIT, TS0, p)
    assert s.penalty_bp == pytest.approx(10.0)


# ---- decay_penalty ----

def test_penalty_decays():
    s = on_fill(empty_state(), BID_HIT, TS0, PARAMS)
    later = TS0 + pd.Timedelta(seconds=10)
    s2 = decay_penalty(s, later, PARAMS)
    assert s2.penalty_bp < s.penalty_bp

def test_cooldown_clears():
    s = on_fill(empty_state(), BID_HIT, TS0, PARAMS)
    s = on_fill(s, BID_HIT, TS0 + pd.Timedelta(seconds=1), PARAMS)
    s = on_fill(s, BID_HIT, TS0 + pd.Timedelta(seconds=2), PARAMS)
    assert s.cooldown_until is not None
    after = s.cooldown_until + pd.Timedelta(seconds=1)
    s2 = decay_penalty(s, after, PARAMS)
    assert s2.cooldown_until is None


# ---- belief_update ----

def test_belief_bid_hit_lowers():
    updated = belief_update(100.0, BID_HIT, expected_sweep_cost_bp=1.74)
    assert updated < 100.0

def test_belief_ask_lifted_raises():
    updated = belief_update(100.0, ASK_LIFTED, expected_sweep_cost_bp=1.74)
    assert updated > 100.0

def test_belief_unknown_side_unchanged():
    updated = belief_update(100.0, "unknown", expected_sweep_cost_bp=1.74)
    assert updated == pytest.approx(100.0)


# ---- is_quoting_allowed ----

def test_allowed_when_no_cooldown():
    assert is_quoting_allowed(empty_state(), TS0)

def test_blocked_during_cooldown():
    s = AdverseSelectionState(cooldown_until=TS0 + pd.Timedelta(seconds=5))
    assert not is_quoting_allowed(s, TS0)

def test_allowed_after_cooldown():
    s = AdverseSelectionState(cooldown_until=TS0 + pd.Timedelta(seconds=5))
    assert is_quoting_allowed(s, TS0 + pd.Timedelta(seconds=6))
