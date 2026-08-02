"""Tests for _shared/latency_model.py (B7) — deterministic, no I/O.

Run:
    python3 -m pytest _shared/test_latency_model.py -q
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest  # noqa: E402

from _shared.latency_model import (  # noqa: E402
    CancelStatus,
    EmpiricalLatency,
    FixedLatency,
    LatencySample,
    LatencySimulator,
    NormalLatency,
)

NS = 1_000_000_000  # one second in ns


def test_fixed_latency_submit_defers_live_timestamp():
    sim = LatencySimulator(FixedLatency(feed_ns=5, order_ns=100), seed=1)
    live = sim.submit("a", ts_ns=1_000)
    assert live == 1_100
    assert not sim.fillable("a", 1_099)
    assert sim.fillable("a", 1_100)
    po = sim.pending("a")
    assert po.decision_ts_ns == 995  # feed latency recorded


def test_zero_latency_orders_fill_immediately():
    sim = LatencySimulator(FixedLatency(), seed=1)
    live = sim.submit("a", ts_ns=42)
    assert live == 42
    assert sim.fillable("a", 42)


def test_normal_latency_is_non_negative_and_seeded():
    model = NormalLatency(
        feed_mean_ns=10.0, feed_std_ns=50.0,
        order_mean_ns=20.0, order_std_ns=100.0,
    )
    s1 = LatencySimulator(model, seed=7)
    s2 = LatencySimulator(model, seed=7)
    for i in range(200):
        live1 = s1.submit(f"o{i}", ts_ns=10_000)
        live2 = s2.submit(f"o{i}", ts_ns=10_000)
        assert live1 == live2               # reproducible under the same seed
        assert live1 >= 10_000              # clipped: never in the past
        assert s1.pending(f"o{i}").decision_ts_ns <= 10_000


def test_normal_latency_mean_tracks_config():
    model = NormalLatency(order_mean_ns=500.0, order_std_ns=10.0)
    sim = LatencySimulator(model, seed=3)
    lives = [sim.submit(f"o{i}", ts_ns=0) for i in range(2_000)]
    mean = sum(lives) / len(lives)
    assert 450.0 < mean < 550.0


def test_empirical_cycle_replays_in_order():
    samples = (
        LatencySample(feed_ns=1, order_ns=10, cancel_ns=2),
        LatencySample(feed_ns=2, order_ns=20, cancel_ns=4),
    )
    sim = LatencySimulator(EmpiricalLatency(samples=samples, mode="cycle"), seed=1)
    assert sim.submit("a", 1_000) == 1_010
    assert sim.submit("b", 1_000) == 1_020
    assert sim.submit("c", 1_000) == 1_010  # wraps around


def test_empirical_sample_mode_draws_from_pool():
    samples = (
        LatencySample(feed_ns=0, order_ns=10),
        LatencySample(feed_ns=0, order_ns=99),
    )
    sim = LatencySimulator(
        EmpiricalLatency(samples=samples, mode="sample"), seed=11,
    )
    lives = {sim.submit(f"o{i}", 0) for i in range(50)}
    assert lives <= {10, 99}
    assert len(lives) == 2  # both pool members were drawn


def test_order_cannot_fill_before_it_is_live():
    sim = LatencySimulator(FixedLatency(order_ns=500), seed=1)
    sim.submit("a", ts_ns=0)
    with pytest.raises(ValueError):
        sim.mark_filled("a", 499)
    sim.mark_filled("a", 500)
    assert sim.pending("a").filled is True
    assert not sim.fillable("a", 10_000)


def test_cancel_intercepts_future_fill():
    sim = LatencySimulator(
        FixedLatency(order_ns=0, cancel_ns=300), seed=1,
    )
    sim.submit("a", ts_ns=0)
    result = sim.cancel("a", ts_ns=100)
    assert result.status == CancelStatus.CANCELLED
    assert result.effective_ts_ns == 400
    # race window: fill before the cancel's effective time still wins
    assert sim.fillable("a", 399)
    sim.mark_filled("a", 399)
    assert not sim.fillable("a", 400)


def test_cancel_wins_after_effective_time():
    sim = LatencySimulator(
        FixedLatency(order_ns=0, cancel_ns=300), seed=1,
    )
    sim.submit("a", ts_ns=0)
    sim.cancel("a", ts_ns=100)
    assert not sim.fillable("a", 400)
    with pytest.raises(ValueError):
        sim.mark_filled("a", 400)


def test_cancel_after_fill_reports_already_filled():
    sim = LatencySimulator(FixedLatency(), seed=1)
    sim.submit("a", ts_ns=0)
    sim.mark_filled("a", 0)
    result = sim.cancel("a", ts_ns=10)
    assert result.status == CancelStatus.ALREADY_FILLED
    assert result.effective_ts_ns is None


def test_cancel_unknown_and_duplicate():
    sim = LatencySimulator(FixedLatency(cancel_ns=10), seed=1)
    assert sim.cancel("ghost", 0).status == CancelStatus.NOT_FOUND
    sim.submit("a", 0)
    sim.cancel("a", 0)
    assert sim.cancel("a", 5).status == CancelStatus.ALREADY_CANCELLED


def test_newly_live_buckets_orders_by_interval():
    sim = LatencySimulator(FixedLatency(order_ns=50), seed=1)
    sim.submit("a", ts_ns=0)     # live at 50
    sim.submit("b", ts_ns=100)   # live at 150
    sim.submit("c", ts_ns=200)   # live at 250
    assert sim.newly_live(0, 100) == ["a"]
    assert sim.newly_live(100, 200) == ["b"]
    assert sim.newly_live(0, 1_000) == ["a", "b", "c"]


def test_feed_latency_marks_stale_decisions():
    sim = LatencySimulator(FixedLatency(feed_ns=2 * NS, order_ns=NS), seed=1)
    sim.submit("a", ts_ns=10 * NS)
    po = sim.pending("a")
    assert po.decision_ts_ns == 8 * NS
    assert po.live_ts_ns == 11 * NS


def test_validation():
    with pytest.raises(ValueError):
        LatencySample(feed_ns=-1, order_ns=0)
    with pytest.raises(ValueError):
        FixedLatency(order_ns=-5)
    with pytest.raises(ValueError):
        NormalLatency(order_std_ns=-1.0)
    with pytest.raises(ValueError):
        EmpiricalLatency(samples=())
    with pytest.raises(ValueError):
        EmpiricalLatency(
            samples=(LatencySample(0, 0),), mode="shuffle",
        )
