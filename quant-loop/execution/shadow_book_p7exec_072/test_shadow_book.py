"""test_shadow_book — P7-EXEC-072 unit tests.

Covers the happy path (single + multi-fill shadow aggregation, position
projection, finalize, reconciliation) and the failure modes documented
in ``INTERFACE.md §6``:

* Duplicate ``trade_id`` is silently idempotent.
* Late fill after terminal status raises :exc:`LateShadowFillRejected`
  and journals a ``LATE_FILL_REJECTED`` event without mutating state.
* Unknown ``terminal_status`` raises ``ValueError``.
* ``finalize_order`` on an unknown ``client_order_id`` raises
  ``KeyError``.
* ``finalize_order`` with the same terminal status twice is idempotent.
* Replay rebuilds order + position state from the journal on cold
  start.
* ``on_fill`` hydrates from the journal when the in-memory cache is
  empty (cold-start path).
* Reconciler diffs shadow vs live correctly, including only-in-shadow
  and only-in-live cases.

Stdlib-only (``unittest``). No pytest dependency per
``README.md``'s "no pytest dep" rule.

Run::

    cd ~/multica/quant-loop/execution/shadow_book_p7exec_072
    python3 test_shadow_book.py -v

Exit code 0 = all tests pass.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

# Allow running this file directly (``python3 test_shadow_book.py``)
# from the component folder.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from shadow_book import (  # noqa: E402
    LATE_FILL_LIQUIDITY,
    TERMINAL_STATUSES,
    LateShadowFillRejected,
    LiveOrderReport,
    ReconciliationRow,
    ShadowBook,
    ShadowBookJournal,
    ShadowFillEvent,
    ShadowOrderState,
    UnknownLiveReport,
    reconcile,
)


def _ts() -> int:
    return time.time_ns()


def _event(
    coid: str,
    trade_id: str,
    qty: float,
    price: float,
    side: str = "BUY",
    symbol: str = "BTCUSDT",
    liquidity: str = "taker",
    ts_ns: int = 0,
    strategy_id: str = "vpvr_btc_long",
) -> ShadowFillEvent:
    return ShadowFillEvent(
        ts_ns=ts_ns or _ts(),
        client_order_id=coid,
        trade_id=trade_id,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        liquidity=liquidity,
        strategy_id=strategy_id,
    )


def _live(
    coid: str,
    total_qty: float,
    avg_price: float,
    fill_count: int,
    terminal_status: str = None,
    symbol: str = "BTCUSDT",
    side: str = "BUY",
) -> LiveOrderReport:
    return LiveOrderReport(
        client_order_id=coid,
        symbol=symbol,
        side=side,
        total_qty=total_qty,
        avg_price=avg_price,
        fill_count=fill_count,
        terminal_status=terminal_status,
        terminal_ts_ns=_ts() if terminal_status else None,
        received_at_ns=_ts(),
    )


class TestShadowBook(unittest.TestCase):
    """Happy path + failure mode coverage per INTERFACE.md §6."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "sb.sqlite"
        self.journal = ShadowBookJournal(self.db_path)
        self.book = ShadowBook(self.journal)

    def tearDown(self) -> None:
        self.book.close()
        self._tmp.cleanup()

    # ----------------------------------------------------------------- happy

    def test_first_fill_creates_order_and_position(self) -> None:
        """Unknown coid on first fill → new order + new position
        projection."""
        s = self.book.on_fill(
            _event("coid-A", "t-1", qty=0.01, price=50000.0)
        )
        self.assertEqual(s.client_order_id, "coid-A")
        self.assertEqual(s.total_qty, 0.01)
        self.assertEqual(s.avg_price, 50000.0)
        self.assertEqual(s.notional_usd, 500.0)
        self.assertEqual(s.fill_count, 1)
        self.assertIsNone(s.terminal_status)

        pos = self.book.snapshot_position("BTCUSDT", "BUY")
        self.assertIsNotNone(pos)
        self.assertEqual(pos.net_qty, 0.01)
        self.assertEqual(pos.avg_price, 50000.0)
        self.assertEqual(pos.fill_count, 1)

    def test_vwap_across_two_fills(self) -> None:
        """Two fills fold to VWAP: (0.01*50000 + 0.005*50100)/0.015."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        s2 = self.book.on_fill(
            _event("coid-A", "t-2", qty=0.005, price=50100.0)
        )
        self.assertAlmostEqual(s2.total_qty, 0.015, places=9)
        self.assertAlmostEqual(s2.avg_price, 50033.333333333336, places=6)
        self.assertAlmostEqual(s2.notional_usd, 750.5, places=6)
        self.assertEqual(s2.fill_count, 2)

        # Position projection should aggregate across all fills for
        # (BTCUSDT, BUY), regardless of coid.
        pos = self.book.snapshot_position("BTCUSDT", "BUY")
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos.net_qty, 0.015, places=9)
        self.assertAlmostEqual(pos.avg_price, 50033.333333333336, places=6)
        self.assertEqual(pos.fill_count, 2)

    def test_position_aggregates_across_coids(self) -> None:
        """Two coids for the same (symbol, side) fold into the same
        position projection."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book.on_fill(_event("coid-B", "t-2", qty=0.02, price=51000.0))
        pos = self.book.snapshot_position("BTCUSDT", "BUY")
        self.assertIsNotNone(pos)
        # (0.01*50000 + 0.02*51000) / 0.03 = (500 + 1020) / 0.03 = 50666.66...
        self.assertAlmostEqual(pos.net_qty, 0.030, places=9)
        self.assertAlmostEqual(pos.avg_price, 50666.666666666664, places=6)
        self.assertEqual(pos.fill_count, 2)

    def test_position_side_isolated(self) -> None:
        """BUY and SELL on the same symbol are separate position
        projections; the cross-side netting is the P&L layer's job,
        not ours."""
        self.book.on_fill(_event(
            "coid-A", "t-1", qty=0.01, price=50000.0, side="BUY"
        ))
        self.book.on_fill(_event(
            "coid-B", "t-2", qty=0.02, price=51000.0, side="SELL"
        ))
        buy = self.book.snapshot_position("BTCUSDT", "BUY")
        sell = self.book.snapshot_position("BTCUSDT", "SELL")
        self.assertIsNotNone(buy)
        self.assertIsNotNone(sell)
        self.assertAlmostEqual(buy.net_qty, 0.01, places=9)
        self.assertAlmostEqual(sell.net_qty, 0.02, places=9)

    def test_finalize_order_sets_terminal_status(self) -> None:
        """finalize_order() records the terminal marker and persists
        it."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        final = self.book.finalize_order("coid-A", "FILLED")
        self.assertEqual(final.terminal_status, "FILLED")
        self.assertIsNotNone(final.terminal_ts_ns)
        # Persisted to journal too.
        persisted = self.journal.fetch_order("coid-A")
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.terminal_status, "FILLED")

    # ------------------------------------------------------------ failure

    def test_duplicate_trade_id_is_idempotent(self) -> None:
        """Re-sent WS event with same (coid, trade_id) returns existing
        state without a second journal row."""
        s1 = self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        s2 = self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.assertEqual(s1, s2)
        # One row in shadow_fill_events, not two.
        rows = self.journal.fetch_events("coid-A")
        self.assertEqual(len(rows), 1)

    def test_late_fill_after_terminal_raises(self) -> None:
        """Fill after finalize raises LateShadowFillRejected but journals
        the late event with LATE_FILL_LIQUIDITY for forensics."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book.finalize_order("coid-A", "FILLED")
        with self.assertRaises(LateShadowFillRejected):
            self.book.on_fill(
                _event("coid-A", "t-2", qty=0.001, price=51000.0)
            )
        # Order state unchanged after the late fill.
        s = self.journal.fetch_order("coid-A")
        self.assertEqual(s.total_qty, 0.01)
        self.assertEqual(s.avg_price, 50000.0)
        # Position state unchanged after the late fill.
        pos = self.journal.fetch_position("BTCUSDT", "BUY")
        self.assertAlmostEqual(pos.net_qty, 0.01, places=9)
        # Late event recorded in the journal with the sentinel.
        events = self.journal.fetch_events("coid-A")
        self.assertEqual(len(events), 2)
        late = [e for e in events if e.trade_id == "t-2"][0]
        self.assertEqual(late.liquidity, LATE_FILL_LIQUIDITY)

    def test_unknown_terminal_status_raises_value_error(self) -> None:
        """finalize_order() rejects unknown terminal status values."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        with self.assertRaises(ValueError):
            self.book.finalize_order("coid-A", "PARTIAL")

    def test_finalize_unknown_coid_raises_key_error(self) -> None:
        """finalize_order() on an unknown coid raises KeyError."""
        with self.assertRaises(KeyError):
            self.book.finalize_order("never-seen", "FILLED")

    def test_double_finalize_same_status_is_idempotent(self) -> None:
        """Two finalize_order() calls with the same status return the
        existing state."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        f1 = self.book.finalize_order("coid-A", "FILLED")
        ts1 = f1.terminal_ts_ns
        time.sleep(0.01)
        f2 = self.book.finalize_order("coid-A", "FILLED")
        self.assertEqual(f1.terminal_status, f2.terminal_status)
        self.assertEqual(f1.terminal_ts_ns, f2.terminal_ts_ns)
        self.assertEqual(ts1, f2.terminal_ts_ns)

    def test_double_finalize_different_status_no_op(self) -> None:
        """A contradicting finalize_order() does NOT overwrite the
        existing terminal marker."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        f1 = self.book.finalize_order("coid-A", "FILLED")
        f2 = self.book.finalize_order("coid-A", "CANCELED")
        self.assertEqual(f1.terminal_status, "FILLED")
        self.assertEqual(f2.terminal_status, "FILLED")

    def test_unknown_live_report_terminal_status_raises(self) -> None:
        """record_live_reports rejects unknown terminal statuses."""
        bad = _live(
            "coid-A", total_qty=0.01, avg_price=50000.0,
            fill_count=1, terminal_status="PARTIAL",
        )
        with self.assertRaises(UnknownLiveReport):
            self.book.record_live_reports([bad])

    # ------------------------------------------------------------ recovery

    def test_replay_order_rebuilds_state(self) -> None:
        """Replay rebuilds order state from the journal after the
        in-memory cache is gone (simulates a process restart)."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book.on_fill(_event("coid-A", "t-2", qty=0.005, price=50100.0))
        self.book.finalize_order("coid-A", "FILLED")
        # Wipe the in-memory cache and replay.
        self.book._orders.clear()  # noqa: SLF001
        replayed = self.book.replay_order("coid-A")
        self.assertIsNotNone(replayed)
        self.assertAlmostEqual(replayed.total_qty, 0.015, places=9)
        self.assertAlmostEqual(replayed.avg_price, 50033.333333333336, places=6)
        self.assertEqual(replayed.fill_count, 2)
        self.assertEqual(replayed.terminal_status, "FILLED")

    def test_replay_position_rebuilds_state(self) -> None:
        """Replay rebuilds position state by aggregating every fill
        for (symbol, side) across all coids."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book.on_fill(_event("coid-B", "t-2", qty=0.02, price=51000.0))
        self.book._positions.clear()  # noqa: SLF001
        replayed = self.book.replay_position("BTCUSDT", "BUY")
        self.assertIsNotNone(replayed)
        self.assertAlmostEqual(replayed.net_qty, 0.030, places=9)
        self.assertAlmostEqual(replayed.avg_price, 50666.666666666664, places=6)
        self.assertEqual(replayed.fill_count, 2)

    def test_on_fill_hydrates_from_journal(self) -> None:
        """on_fill for a coid known to the journal but not in the
        in-memory cache re-hydrates and folds correctly (cold-start
        hot-path path)."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book._orders.clear()  # noqa: SLF001
        s = self.book.on_fill(
            _event("coid-A", "t-2", qty=0.005, price=50100.0)
        )
        self.assertAlmostEqual(s.total_qty, 0.015, places=9)
        self.assertAlmostEqual(s.avg_price, 50033.333333333336, places=6)
        self.assertEqual(s.fill_count, 2)

    def test_replay_unknown_coid_returns_none(self) -> None:
        """replay_order() on a coid with no journal rows returns None."""
        self.assertIsNone(self.book.replay_order("never-seen"))

    def test_known_orders_enumerates_cache(self) -> None:
        """known_orders() returns the in-memory keys."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book.on_fill(_event(
            "coid-B", "t-2", qty=0.02, price=3000.0,
            symbol="ETHUSDT",
        ))
        keys = set(self.book.known_orders())
        self.assertEqual(keys, {"coid-A", "coid-B"})

    # ------------------------------------------------------------ reconcile

    def test_reconcile_no_diff_when_match(self) -> None:
        """If shadow and live agree on every metric, the diff is zero."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book.finalize_order("coid-A", "FILLED")
        self.book.record_live_reports([
            _live(
                "coid-A", total_qty=0.01, avg_price=50000.0,
                fill_count=1, terminal_status="FILLED",
            ),
        ])
        rows = self.book.reconcile()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r.client_order_id, "coid-A")
        self.assertEqual(r.qty_diff, 0.0)
        self.assertEqual(r.avg_price_diff, 0.0)
        self.assertEqual(r.fill_count_diff, 0)
        self.assertTrue(r.status_match)
        self.assertFalse(r.only_in_shadow)
        self.assertFalse(r.only_in_live)

    def test_reconcile_qty_mismatch_detected(self) -> None:
        """A qty divergence between shadow and live surfaces as a
        non-zero qty_diff on the reconciliation row."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book.finalize_order("coid-A", "FILLED")
        # Live thinks we got 0.012 not 0.01 — venue over-reported, or
        # shadow missed a fill. Either way, the row should show it.
        self.book.record_live_reports([
            _live(
                "coid-A", total_qty=0.012, avg_price=50050.0,
                fill_count=1, terminal_status="FILLED",
            ),
        ])
        rows = self.book.reconcile()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertAlmostEqual(r.qty_diff, 0.01 - 0.012, places=9)
        self.assertAlmostEqual(r.avg_price_diff, 50000.0 - 50050.0, places=6)
        self.assertEqual(r.fill_count_diff, 0)
        self.assertTrue(r.status_match)

    def test_reconcile_only_in_shadow(self) -> None:
        """An order in shadow but not in live (e.g. shadow saw a fill
        the venue hasn't reported yet, or a coid the live snapshot
        missed) gets only_in_shadow=True."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        # No live report yet.
        rows = self.book.reconcile()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r.client_order_id, "coid-A")
        self.assertTrue(r.only_in_shadow)
        self.assertFalse(r.only_in_live)
        self.assertIsNotNone(r.shadow)
        self.assertIsNone(r.live)
        self.assertEqual(r.qty_diff, 0.01)

    def test_reconcile_only_in_live(self) -> None:
        """An order in live but not in shadow (e.g. the venue has
        reported a fill we never saw) gets only_in_live=True."""
        # No on_fill calls — shadow is empty.
        self.book.record_live_reports([
            _live(
                "coid-A", total_qty=0.01, avg_price=50000.0,
                fill_count=1, terminal_status="FILLED",
            ),
        ])
        rows = self.book.reconcile()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r.client_order_id, "coid-A")
        self.assertFalse(r.only_in_shadow)
        self.assertTrue(r.only_in_live)
        self.assertIsNone(r.shadow)
        self.assertIsNotNone(r.live)
        self.assertEqual(r.qty_diff, -0.01)

    def test_reconcile_status_mismatch_detected(self) -> None:
        """A terminal-status divergence is surfaced as
        status_match=False."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book.finalize_order("coid-A", "FILLED")
        # Live reports CANCELED.
        self.book.record_live_reports([
            _live(
                "coid-A", total_qty=0.01, avg_price=50000.0,
                fill_count=1, terminal_status="CANCELED",
            ),
        ])
        rows = self.book.reconcile()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertFalse(r.status_match)

    def test_reconcile_pure_function_isolated(self) -> None:
        """reconcile() is a pure function — it does NOT touch the
        journal; calling it leaves both sides intact.

        We populate the journal with one coid, then call the pure
        ``reconcile()`` with synthetic inputs that disagree with the
        journal on a different coid. The pure call must reflect the
        synthetic inputs (not the journal) and the journal must be
        unchanged afterwards.
        """
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.book.record_live_reports([
            _live(
                "coid-A", total_qty=0.01, avg_price=50000.0,
                fill_count=1,
            ),
        ])
        # Synthetic shadow + live for a different coid.
        synth_shadow = ShadowOrderState(
            client_order_id="coid-X",
            symbol="BTCUSDT",
            side="BUY",
            total_qty=0.05,
            avg_price=42000.0,
            notional_usd=2100.0,
            fill_count=2,
            first_fill_ts_ns=0,
            last_fill_ts_ns=0,
            terminal_status="FILLED",
        )
        synth_live = _live(
            "coid-X", total_qty=0.05, avg_price=42000.0,
            fill_count=2, terminal_status="FILLED",
        )
        rows = reconcile([synth_shadow], [synth_live])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].qty_diff, 0.0)
        # Journal untouched — only coid-A is still there.
        all_orders = self.journal.fetch_all_orders()
        coids = {o.client_order_id for o in all_orders}
        self.assertEqual(coids, {"coid-A"})
        all_live = self.journal.fetch_all_live_reports()
        live_coids = {l.client_order_id for l in all_live}
        self.assertEqual(live_coids, {"coid-A"})

    # ------------------------------------------------------------ invariants

    def test_terminal_statuses_is_complete(self) -> None:
        """TERMINAL_STATUSES contains the canonical set per INTERFACE.md."""
        self.assertEqual(
            TERMINAL_STATUSES,
            frozenset({"FILLED", "CANCELED", "EXPIRED", "REJECTED"}),
        )

    def test_order_state_is_immutable(self) -> None:
        """ShadowOrderState is a frozen dataclass — attribute writes
        raise FrozenInstanceError."""
        s = self.book.on_fill(
            _event("coid-A", "t-1", qty=0.01, price=50000.0)
        )
        with self.assertRaises(Exception):
            s.total_qty = 999.0  # type: ignore[misc]

    def test_position_state_is_immutable(self) -> None:
        """ShadowPositionState is a frozen dataclass — attribute writes
        raise FrozenInstanceError."""
        self.book.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        pos = self.book.snapshot_position("BTCUSDT", "BUY")
        self.assertIsNotNone(pos)
        with self.assertRaises(Exception):
            pos.net_qty = 999.0  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)