"""Tests for quote_throttle.py — state-machine boundary coverage."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.market_making.quote_throttle import (
    COOLDOWN,
    READY,
    ThrottleParams,
    initial_state,
    should_quote,
)

PARAMS = ThrottleParams(
    min_interval_seconds=0.5,
    max_amends_per_minute=5,
    burst_limit=3,
    burst_window_seconds=2.0,
    cooldown_seconds=5.0,
)


def _quote_n(state, t0, n, spacing, params=PARAMS):
    """Record n allowed quotes at fixed spacing; return final state."""
    for k in range(n):
        d = should_quote(t0 + k * spacing, state, params)
        assert d.allowed, f"quote {k} unexpectedly denied: {d.reason}"
        state = d.state
    return state


# ---- initial / min-interval gate ----

def test_first_quote_allowed():
    d = should_quote(1000.0, initial_state(), PARAMS)
    assert d.allowed and d.reason == "ok"
    assert d.state.phase == READY
    assert d.state.quote_times == (1000.0,)


def test_min_interval_denies_too_soon():
    s = _quote_n(initial_state(), 1000.0, 1, 1.0)
    d = should_quote(1000.4, s, PARAMS)   # 0.4 < 0.5
    assert not d.allowed and d.reason == "min_interval"
    # denied quote is NOT recorded
    assert d.state.quote_times == s.quote_times


def test_min_interval_boundary_exact_is_allowed():
    s = _quote_n(initial_state(), 1000.0, 1, 1.0)
    d = should_quote(1000.5, s, PARAMS)   # exactly min_interval → allowed
    assert d.allowed


# ---- per-minute cap gate ----

def test_minute_cap_denies_at_budget():
    # 5 quotes at 10 s spacing → all inside trailing 60 s; burst window
    # is 2 s so the burst latch never fires here.
    s = _quote_n(initial_state(), 1000.0, 5, 10.0)
    d = should_quote(1051.0, s, PARAMS)
    assert not d.allowed and d.reason == "minute_cap"


def test_minute_cap_boundary_oldest_expires():
    s = _quote_n(initial_state(), 1000.0, 5, 10.0)   # quotes at 0..40
    # at t=1061.0 the t=1000.0 quote is exactly 61 s old → pruned
    d = should_quote(1061.0, s, PARAMS)
    assert d.allowed
    # boundary: exactly 60 s old is pruned too (window is strictly >)
    s2 = _quote_n(initial_state(), 1000.0, 5, 10.0)
    d2 = should_quote(1060.0, s2, PARAMS)
    assert d2.allowed
    # one tick earlier: all 5 still inside → denied
    s3 = _quote_n(initial_state(), 1000.0, 5, 10.0)
    d3 = should_quote(1059.9, s3, PARAMS)
    assert not d3.allowed and d3.reason == "minute_cap"


# ---- burst circuit breaker ----

def test_burst_latch_on_limit():
    # burst_limit=3, window=2 s, min_interval=0.5 → quotes at 0, 0.6, 1.2
    s = _quote_n(initial_state(), 2000.0, 2, 0.6)
    d = should_quote(2001.2, s, PARAMS)   # 3rd quote inside window
    assert d.allowed                       # the breaching quote is still sent
    assert d.state.phase == COOLDOWN
    assert d.state.cooldown_until == pytest.approx(2001.2 + 5.0)


def test_burst_window_boundary_not_counted():
    # quote at t=0, next at exactly t=window → the first is outside the
    # strictly-greater burst window, so count stays 1 → no latch.
    s = _quote_n(initial_state(), 2000.0, 1, 1.0)
    d = should_quote(2002.0, s, PARAMS)   # 2000.0 > 2002.0-2.0 is False
    assert d.allowed and d.state.phase == READY


def test_cooldown_denies_and_expires():
    s = _quote_n(initial_state(), 2000.0, 2, 0.6)
    d = should_quote(2001.2, s, PARAMS)
    assert d.state.phase == COOLDOWN

    # inside cooldown → denied, even though other gates would pass
    d1 = should_quote(2004.0, d.state, PARAMS)
    assert not d1.allowed and d1.reason == "cooldown"
    assert d1.state.phase == COOLDOWN

    # exactly at cooldown_until → READY again, quote allowed
    d2 = should_quote(2006.2, d.state, PARAMS)
    assert d2.allowed and d2.state.phase == READY

    # past cooldown → allowed
    d3 = should_quote(2099.0, d.state, PARAMS)
    assert d3.allowed


def test_cooldown_until_cleared_on_expiry():
    s = _quote_n(initial_state(), 2000.0, 2, 0.6)
    d = should_quote(2001.2, s, PARAMS)
    d2 = should_quote(2006.2, d.state, PARAMS)
    assert d2.state.cooldown_until == 0.0


# ---- immutability ----

def test_input_state_never_mutated():
    s = initial_state()
    snapshot = s
    should_quote(1000.0, s, PARAMS)
    assert s is snapshot
    assert s.quote_times == () and s.phase == READY
