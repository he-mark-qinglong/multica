"""Tests for the L2 order-book replay infrastructure (B4).

Covers: book reconstruction (snapshot + diff), penetration partial
fills, queue fill-probability application, replay determinism, and a
smoke test on real Binance bookDepth sample data (skipped when the
sample files are absent).
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import math
from pathlib import Path

import pytest

from _shared.l2.book import BookDiff, BookState, snapshot
from _shared.l2.bookdepth import (
    bookdepth_rows_to_snapshots,
    load_bookdepth_parquet,
)
from _shared.l2.replay import (
    REASON_FILLED,
    REASON_NOT_TOUCHED,
    REASON_PARTIAL_DEPTH,
    REASON_PARTIAL_QUEUE,
    ReplayOrder,
    ReplayPolicy,
    replay,
    simulate_order,
)
from _shared.market_making.queue_position import fill_probability

L2_DIR = Path("/Users/mark/multica/quant-loop/data/l2")
SAMPLE_PARQUET = L2_DIR / "BTCUSDT-bookDepth-2024-05-16.parquet"

T0 = 1_700_000_000_000_000_000  # arbitrary ns epoch


def base_book(ts_ns=T0):
    """3 bids / 3 asks, 1 unit per level, uncrossed."""
    return snapshot(
        ts_ns=ts_ns,
        bids=[(99.0, 1.0), (98.0, 2.0), (97.0, 3.0)],
        asks=[(101.0, 1.0), (102.0, 2.0), (103.0, 3.0)],
    )


# ---------------------------------------------------------------------------
# book reconstruction
# ---------------------------------------------------------------------------

class TestBookReconstruction:
    def test_snapshot_sorts_levels(self):
        st = snapshot(
            ts_ns=T0,
            bids=[(98.0, 2.0), (99.0, 1.0)],
            asks=[(102.0, 2.0), (101.0, 1.0)],
        )
        assert st.bids == ((99.0, 1.0), (98.0, 2.0))
        assert st.asks == ((101.0, 1.0), (102.0, 2.0))

    def test_apply_diff_insert_update_remove(self):
        st = base_book()
        diff = BookDiff(
            ts_ns=T0 + 1,
            bids=((99.0, 5.0), (96.0, 1.0), (98.0, 0.0)),  # update/add/remove
            asks=((101.0, 0.0), (104.0, 1.5)),
        )
        new = st.apply_diff(diff)
        assert new.ts_ns == T0 + 1
        assert new.bids == ((99.0, 5.0), (97.0, 3.0), (96.0, 1.0))
        assert new.asks == ((102.0, 2.0), (103.0, 3.0), (104.0, 1.5))

    def test_apply_diff_is_immutable(self):
        st = base_book()
        st.apply_diff(BookDiff(ts_ns=T0 + 1, bids=((99.0, 0.0),)))
        assert st.bids[0] == (99.0, 1.0)  # original untouched
        assert st.ts_ns == T0

    def test_apply_diff_remove_unknown_level_ignored(self):
        st = base_book()
        new = st.apply_diff(BookDiff(ts_ns=T0 + 1, bids=((50.0, 0.0),)))
        assert new.bids == st.bids

    def test_queries(self):
        st = base_book()
        assert st.mid_price == 100.0
        assert st.spread == 2.0
        assert st.top(2, "BID") == ((99.0, 1.0), (98.0, 2.0))
        assert st.depth_qty("ASK", 2) == 3.0
        # VWAP to buy 2 units: 1@101 + 1@102
        assert st.weighted_depth_price("ASK", 2.0) == pytest.approx(101.5)
        # insufficient depth -> None
        assert st.weighted_depth_price("ASK", 100.0) is None
        assert st.levels_through("ASK", 102.0) == ((101.0, 1.0), (102.0, 2.0))

    def test_validation_rejects_bad_levels(self):
        with pytest.raises(ValueError):
            snapshot(ts_ns=T0, bids=[(-1.0, 1.0)], asks=[(101.0, 1.0)])
        with pytest.raises(ValueError):
            snapshot(ts_ns=T0, bids=[(99.0, 1.0)], asks=[(101.0, -1.0)])


# ---------------------------------------------------------------------------
# order simulation: penetration + queue
# ---------------------------------------------------------------------------

class TestSimulateOrder:
    def test_penetration_walks_levels(self):
        st = base_book()
        order = ReplayOrder("o1", "BUY", qty=2.5, price=102.0, ts_placed_ns=T0)
        fills = simulate_order(order, st)
        # 1@101 + 1.5@102, filled at every level -> all FILLED
        assert [(f.price, f.qty) for f in fills] == [(101.0, 1.0), (102.0, 1.5)]
        assert all(f.reason == REASON_FILLED for f in fills)

    def test_penetration_partial_when_depth_runs_out(self):
        st = base_book()
        order = ReplayOrder("o2", "BUY", qty=10.0, price=103.0, ts_placed_ns=T0)
        fills = simulate_order(order, st)
        total = sum(f.qty for f in fills)
        assert total == pytest.approx(6.0)  # all visible ask depth
        assert fills[-1].reason == REASON_PARTIAL_DEPTH

    def test_market_order_walks_book(self):
        st = base_book()
        order = ReplayOrder("o3", "SELL", qty=4.0, price=None, ts_placed_ns=T0)
        fills = simulate_order(order, st)
        assert [(f.price, f.qty) for f in fills] == [
            (99.0, 1.0),
            (98.0, 2.0),
            (97.0, 1.0),
        ]

    def test_untouched_limit(self):
        st = base_book()
        order = ReplayOrder("o4", "BUY", qty=1.0, price=100.0, ts_placed_ns=T0)
        fills = simulate_order(order, st, ReplayPolicy(queue_enabled=False))
        assert len(fills) == 1
        assert fills[0].qty == 0.0
        assert fills[0].reason == REASON_NOT_TOUCHED

    def test_resting_order_uses_queue_probability(self):
        st = base_book()
        # passive buy at the best bid: does not cross, joins the queue
        order = ReplayOrder("o5", "BUY", qty=0.5, price=99.0, ts_placed_ns=T0)
        fills = simulate_order(order, st)
        expected_prob = fill_probability(
            seconds_in_queue=0.0, ticks_from_best=0, market_fill_rate=0.13
        )
        assert len(fills) == 1
        assert fills[0].reason == REASON_PARTIAL_QUEUE
        assert fills[0].price == 99.0
        assert fills[0].qty == pytest.approx(0.5 * expected_prob)
        assert 0.0 < fills[0].qty < 0.5  # queue discount really applied

    def test_level_at_limit_is_takeable_in_full(self):
        st = base_book()
        # limit == best ask: taker semantics, full level available
        order = ReplayOrder("o5b", "BUY", qty=1.0, price=101.0, ts_placed_ns=T0)
        fills = simulate_order(order, st)
        assert [(f.price, f.qty, f.reason) for f in fills] == [
            (101.0, 1.0, REASON_FILLED)
        ]

    def test_ticks_from_best_discounts_deeper_queue(self):
        st = base_book()
        at_best = ReplayOrder("o5c", "BUY", qty=0.5, price=99.0, ts_placed_ns=T0)
        one_back = ReplayOrder("o5d", "BUY", qty=0.5, price=98.0, ts_placed_ns=T0)
        q_best = simulate_order(at_best, st)[0].qty
        q_back = simulate_order(one_back, st)[0].qty
        assert 0.0 < q_back < q_best  # one level back -> smaller fill

    def test_queue_disabled_passive_order_never_fills(self):
        st = base_book()
        policy = ReplayPolicy(queue_enabled=False)
        order = ReplayOrder("o6", "BUY", qty=0.5, price=99.0, ts_placed_ns=T0)
        fills = simulate_order(order, st, policy)
        assert fills[0].qty == 0.0
        assert fills[0].reason == REASON_NOT_TOUCHED

    def test_queue_age_reduces_fill(self):
        st = base_book()
        young = ReplayOrder("o7", "BUY", qty=0.5, price=99.0, ts_placed_ns=T0)
        old = ReplayOrder("o8", "BUY", qty=0.5, price=99.0,
                          ts_placed_ns=T0 - 60 * 10**9)  # 60s old
        f_young = simulate_order(young, st)[0].qty
        f_old = simulate_order(old, st)[0].qty
        assert 0.0 < f_old < f_young


# ---------------------------------------------------------------------------
# replay engine
# ---------------------------------------------------------------------------

def event_stream():
    st0 = base_book()
    d1 = BookDiff(ts_ns=T0 + 10, asks=((101.0, 0.0), (102.0, 4.0)))
    d2 = BookDiff(ts_ns=T0 + 20, bids=((99.0, 0.0),))
    return st0, d1, d2


class TestReplay:
    def test_diff_drives_time_and_book(self):
        st0, d1, d2 = event_stream()
        order = ReplayOrder("r1", "BUY", qty=3.0, price=102.0, ts_placed_ns=T0 + 15)
        result = replay([st0, d1, d2], [order])
        # order arrives after d1: ask side is now 4@102 only
        fills = result.fills_for("r1")
        assert [(f.price, f.qty) for f in fills] == [(102.0, 3.0)]
        # final state reflects both diffs
        assert result.final_state.ts_ns == T0 + 20
        assert result.final_state.bids[0] == (98.0, 2.0)
        assert result.n_events == 3

    def test_order_matches_state_at_arrival(self):
        st0, d1, d2 = event_stream()
        early = ReplayOrder("r2", "BUY", qty=1.0, price=101.0, ts_placed_ns=T0)
        late = ReplayOrder("r3", "BUY", qty=1.0, price=101.0, ts_placed_ns=T0 + 15)
        result = replay([st0, d1, d2], [early, late])
        # early: 101 ask still resting -> takes the full level (taker)
        assert result.fills_for("r2") == (
            result.fills_for("r2")[0],
        )
        assert result.fills_for("r2")[0].reason == REASON_FILLED
        assert result.fills_for("r2")[0].qty == pytest.approx(1.0)
        # late: level 101 removed by d1 -> passive, queue-model fill only
        late_fills = result.fills_for("r3")
        assert len(late_fills) == 1
        assert late_fills[0].reason == REASON_PARTIAL_QUEUE
        assert 0.0 < late_fills[0].qty < 1.0

    def test_passive_order_untouched_when_queue_disabled(self):
        st0, d1, d2 = event_stream()
        late = ReplayOrder("r4", "BUY", qty=1.0, price=101.0, ts_placed_ns=T0 + 15)
        result = replay([st0, d1, d2], [late], ReplayPolicy(queue_enabled=False))
        assert result.fills_for("r4")[0].reason == REASON_NOT_TOUCHED

    def test_determinism(self):
        st0, d1, d2 = event_stream()
        orders = [
            ReplayOrder("a", "BUY", qty=2.0, price=102.0, ts_placed_ns=T0),
            ReplayOrder("b", "SELL", qty=1.5, price=98.0, ts_placed_ns=T0 + 5),
            ReplayOrder("c", "BUY", qty=1.0, price=None, ts_placed_ns=T0 + 25),
        ]
        r1 = replay([st0, d1, d2], orders)
        r2 = replay([st0, d1, d2], orders)
        assert r1 == r2

    def test_rejects_non_monotonic_stream(self):
        st0, d1, _ = event_stream()
        bad = BookDiff(ts_ns=T0 - 1, bids=((99.0, 1.0),))
        with pytest.raises(ValueError, match="non-monotonic"):
            replay([st0, d1, bad], [])

    def test_rejects_diff_before_snapshot(self):
        _, d1, _ = event_stream()
        with pytest.raises(ValueError, match="before any snapshot"):
            replay([d1], [])


# ---------------------------------------------------------------------------
# bookDepth loader (synthetic rows)
# ---------------------------------------------------------------------------

class TestBookDepthLoader:
    def test_de_cumulation(self):
        # one timestamp, cumulative bands: -1: 10 @ 990 avg, -2: 30 cum
        rows = [
            (T0, -1, 10.0, 10.0 * 990.0),
            (T0, -2, 30.0, 10.0 * 990.0 + 20.0 * 985.0),
            (T0, 1, 5.0, 5.0 * 1010.0),
            (T0, 2, 15.0, 5.0 * 1010.0 + 10.0 * 1015.0),
        ]
        states = bookdepth_rows_to_snapshots(rows)
        assert len(states) == 1
        st = states[0]
        assert st.bids == ((990.0, 10.0), (985.0, 20.0))
        assert st.asks == ((1010.0, 5.0), (1015.0, 10.0))
        assert st.mid_price == pytest.approx(1000.0)

    def test_skips_empty_bands_and_one_sided_snapshots(self):
        rows = [
            (T0, -1, 0.0, 0.0),      # empty bid band
            (T0, 1, 5.0, 5050.0),    # ask exists but no bids -> dropped
        ]
        assert bookdepth_rows_to_snapshots(rows) == []


# ---------------------------------------------------------------------------
# real-data smoke test (1 hour replay)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not SAMPLE_PARQUET.exists(), reason="L2 sample data not downloaded")
class TestRealDataSmoke:
    def test_one_hour_replay(self):
        states = load_bookdepth_parquet(SAMPLE_PARQUET)
        assert len(states) > 100
        t_start = states[0].ts_ns
        one_hour = [s for s in states if s.ts_ns < t_start + 3600 * 10**9]
        assert len(one_hour) >= 100  # ~30s sampling -> ~120 snapshots/hour

        for st in one_hour:
            assert st.best_bid[0] < st.best_ask[0]  # never crossed
            assert st.mid_price > 0.0

        mid0 = one_hour[0].mid_price
        orders = [
            # aggressive buy crossing the first ask band
            ReplayOrder("s1", "BUY", qty=0.5, price=mid0 * 1.02,
                        ts_placed_ns=t_start),
            # passive buy resting below mid -> must not fill
            ReplayOrder("s2", "BUY", qty=0.5, price=mid0 * 0.995,
                        ts_placed_ns=t_start),
        ]
        result = replay(one_hour, orders, ReplayPolicy(queue_enabled=False))
        f1 = result.fills_for("s1")
        assert sum(f.qty for f in f1) > 0.0
        assert all(f.price <= mid0 * 1.02 + 1e-9 for f in f1)
        assert result.fills_for("s2")[0].reason == REASON_NOT_TOUCHED
        assert math.isfinite(result.final_state.mid_price)
