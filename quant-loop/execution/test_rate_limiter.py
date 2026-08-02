"""Tests for execution/rate_limiter (E18) — fully offline.

Covers:

* header parsing (X-MBX-USED-WEIGHT-* / X-MBX-ORDER-COUNT-* /
  Retry-After, case-insensitive, junk tolerated);
* interval label conversion;
* token-bucket acquire (window accounting, hard-cap refusal,
  window roll-over);
* soft-cap (80%) throttling ramp;
* header sync to venue-reported weight;
* 429 punitive backoff (Retry-After + fallback penalty);
* 418 ban (Retry-After + fallback penalty);
* multi-endpoint bucket independence.

Run:
    python3 -m pytest execution/test_rate_limiter.py -q
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest  # noqa: E402

from execution.rate_limiter import (  # noqa: E402
    RateLimiter,
    RateLimitPolicy,
    interval_to_seconds,
    parse_order_count_headers,
    parse_retry_after_s,
    parse_used_weight_headers,
)

POLICY = RateLimitPolicy(
    weight_limit=100,
    window_s=60.0,
    soft_cap_fraction=0.8,
    throttle_max_delay_s=5.0,
    penalty_429_s=10.0,
    penalty_418_s=120.0,
)
EP = "POST /fapi/v1/order"


def _limiter(**overrides):
    policy = RateLimitPolicy(**{
        **POLICY.__dict__, **overrides,
    }) if overrides else POLICY
    return RateLimiter(default_policy=policy)


# -- header parsing ------------------------------------------------------------


def test_parse_used_weight_headers():
    h = {
        "X-MBX-USED-WEIGHT-1M": "1200",
        "x-mbx-used-weight-10s": "40",
        "Content-Type": "application/json",
        "X-MBX-USED-WEIGHT-1H": "junk",
    }
    assert parse_used_weight_headers(h) == {"1M": 1200, "10S": 40}
    assert parse_used_weight_headers({}) == {}
    assert parse_used_weight_headers(None) == {}


def test_parse_order_count_headers():
    assert parse_order_count_headers(
        {"X-MBX-ORDER-COUNT-1M": "12"}) == {"1M": 12}


def test_parse_retry_after():
    assert parse_retry_after_s({"Retry-After": "30"}) == 30.0
    assert parse_retry_after_s({"retry-after": "2.5"}) == 2.5
    assert parse_retry_after_s({}) is None
    assert parse_retry_after_s({"Retry-After": "soon"}) is None
    assert parse_retry_after_s({"Retry-After": "-1"}) is None


def test_interval_to_seconds():
    assert interval_to_seconds("10S") == 10
    assert interval_to_seconds("1M") == 60
    assert interval_to_seconds("5m") == 300
    assert interval_to_seconds("1H") == 3600
    assert interval_to_seconds("1D") == 86400
    assert interval_to_seconds("1W") is None
    assert interval_to_seconds("M") is None
    assert interval_to_seconds("") is None


# -- token bucket ----------------------------------------------------------------


def test_acquire_within_limit():
    rl = _limiter()
    d = rl.acquire(EP, weight=10, now_s=0.0)
    assert d.allowed and not d.throttled and d.reason == "ok"
    assert d.used_after == 10 and d.wait_s == 0.0


def test_hard_cap_refusal_and_window_rollover():
    rl = _limiter()
    d = rl.acquire(EP, weight=100, now_s=0.0)
    assert d.allowed  # at the cap exactly
    d2 = rl.acquire(EP, weight=1, now_s=1.0)
    assert not d2.allowed and d2.reason == "hard_cap"
    # window started at t=0 → reset at t=60
    assert d2.wait_s == pytest.approx(59.0)
    # after the window rolls, the bucket is usable again
    d3 = rl.acquire(EP, weight=1, now_s=61.0)
    assert d3.allowed


def test_weight_must_be_positive():
    rl = _limiter()
    with pytest.raises(ValueError):
        rl.acquire(EP, weight=0, now_s=0.0)


# -- soft-cap throttling -----------------------------------------------------------


def test_soft_cap_throttle_ramp():
    rl = _limiter()
    # below the soft cap (80): no throttle
    d = rl.acquire(EP, weight=79, now_s=0.0)
    assert d.allowed and not d.throttled
    # exactly at the soft cap: throttle starts, zero delay
    d = rl.acquire(EP, weight=1, now_s=1.0)
    assert d.allowed and d.throttled
    assert d.reason == "throttled_soft_cap"
    assert d.wait_s == pytest.approx(0.0)
    # halfway between soft (80) and hard (100): half the max delay
    d = rl.acquire(EP, weight=10, now_s=2.0)
    assert d.used_after == 90
    assert d.wait_s == pytest.approx(2.5)
    # at the hard cap: full throttle delay
    d = rl.acquire(EP, weight=10, now_s=3.0)
    assert d.used_after == 100
    assert d.wait_s == pytest.approx(5.0)


# -- header sync --------------------------------------------------------------------


def test_header_syncs_used_weight():
    rl = _limiter()
    rl.acquire(EP, weight=5, now_s=0.0)
    # venue reports 90 — sync jumps the local estimate up
    rl.record_response(
        EP, status=200,
        headers={"X-MBX-USED-WEIGHT-1M": "90"}, now_s=1.0)
    snap = rl.snapshot(EP, now_s=1.0)
    assert snap["used_weight"] == 90
    # a stale lower header never lowers the count
    rl.record_response(
        EP, status=200,
        headers={"X-MBX-USED-WEIGHT-1M": "10"}, now_s=2.0)
    assert rl.snapshot(EP, now_s=2.0)["used_weight"] == 90


def test_header_with_wrong_interval_ignored():
    rl = _limiter()
    rl.acquire(EP, weight=5, now_s=0.0)
    rl.record_response(
        EP, status=200,
        headers={"X-MBX-USED-WEIGHT-10S": "99"}, now_s=1.0)
    assert rl.snapshot(EP, now_s=1.0)["used_weight"] == 5


# -- 429 / 418 punitive backoff -------------------------------------------------------


def test_429_cooldown_with_retry_after():
    rl = _limiter()
    rl.record_response(EP, status=429,
                       headers={"Retry-After": "30"}, now_s=100.0)
    d = rl.acquire(EP, weight=1, now_s=110.0)
    assert not d.allowed and d.reason == "cooldown_429"
    assert d.wait_s == pytest.approx(20.0)
    # cooldown elapsed
    d = rl.acquire(EP, weight=1, now_s=131.0)
    assert d.allowed


def test_429_cooldown_fallback_penalty():
    rl = _limiter()
    rl.record_response(EP, status=429, headers={}, now_s=0.0)
    d = rl.acquire(EP, weight=1, now_s=9.0)
    assert not d.allowed and d.reason == "cooldown_429"
    assert d.wait_s == pytest.approx(1.0)  # penalty_429_s=10
    assert rl.snapshot(EP, now_s=9.0)["cooldown_remaining_s"] == (
        pytest.approx(1.0))


def test_418_ban_with_retry_after_and_fallback():
    rl = _limiter()
    rl.record_response(EP, status=418,
                       headers={"Retry-After": "300"}, now_s=0.0)
    d = rl.acquire(EP, weight=1, now_s=60.0)
    assert not d.allowed and d.reason == "banned_418"
    assert d.wait_s == pytest.approx(240.0)

    rl2 = _limiter()
    rl2.record_response(EP, status=418, headers={}, now_s=0.0)
    d = rl2.acquire(EP, weight=1, now_s=119.0)
    assert not d.allowed and d.reason == "banned_418"
    assert d.wait_s == pytest.approx(1.0)  # penalty_418_s=120
    assert rl2.acquire(EP, weight=1, now_s=121.0).allowed


def test_ban_does_not_consume_weight():
    rl = _limiter()
    rl.record_response(EP, status=429, headers={}, now_s=0.0)
    d = rl.acquire(EP, weight=50, now_s=1.0)
    assert not d.allowed
    # refused acquires do not charge the bucket
    assert rl.snapshot(EP, now_s=1.0)["used_weight"] == 0


# -- multi-endpoint independence -------------------------------------------------------


def test_endpoints_are_independent():
    rl = _limiter()
    other = "GET /fapi/v1/ping"
    rl.acquire(EP, weight=100, now_s=0.0)
    rl.record_response(EP, status=429, headers={}, now_s=1.0)
    # EP is parked + at cap; the other endpoint is untouched
    assert not rl.acquire(EP, weight=1, now_s=2.0).allowed
    d = rl.acquire(other, weight=1, now_s=2.0)
    assert d.allowed and d.used_after == 1
    snaps = rl.snapshot_all(now_s=2.0)
    assert set(snaps) == {EP, other}


def test_per_endpoint_policy_override():
    strict = RateLimitPolicy(weight_limit=10, window_s=60.0,
                             soft_cap_fraction=0.8,
                             throttle_max_delay_s=1.0,
                             penalty_429_s=5.0, penalty_418_s=30.0)
    rl = RateLimiter(policies={"heavy": strict},
                     default_policy=POLICY)
    assert not rl.acquire("heavy", weight=11, now_s=0.0).allowed
    assert rl.acquire("light", weight=11, now_s=0.0).allowed


# -- policy validation ------------------------------------------------------------------


def test_policy_validation():
    with pytest.raises(ValueError):
        RateLimitPolicy(weight_limit=0)
    with pytest.raises(ValueError):
        RateLimitPolicy(window_s=0.0)
    with pytest.raises(ValueError):
        RateLimitPolicy(soft_cap_fraction=1.0)
    with pytest.raises(ValueError):
        RateLimitPolicy(penalty_429_s=-1.0)


def test_decision_frozen():
    rl = _limiter()
    d = rl.acquire(EP, weight=1, now_s=0.0)
    with pytest.raises(Exception):
        d.allowed = False  # type: ignore[misc]
