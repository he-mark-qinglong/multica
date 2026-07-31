"""test_partial_fill_accumulator — P7-EXEC-056 unit tests.

Covers the happy path (single + multi-fill aggregation, finalize) and
the failure modes documented in ``INTERFACE.md §7``:

* Duplicate ``trade_id`` is silently idempotent.
* Late fill after terminal status raises :exc:`LateFillRejected` and
  journals a ``LATE_FILL_REJECTED`` event without mutating state.
* Unknown ``terminal_status`` raises ``ValueError``.
* ``finalize`` on an unknown ``client_order_id`` raises ``KeyError``.
* ``finalize`` with the same terminal status twice is idempotent.
* Replay rebuilds state from the journal on cold start.
* ``on_fill`` hydrates from the journal when the in-memory cache is
  empty (cold-start path).

Stdlib-only (``unittest``). No pytest dependency per
``README.md``'s "no pytest dep" rule.

Run::

    cd ~/multica/quant-loop/execution/partial_fill_accumulator_p7exec_056
    python3 test_partial_fill_accumulator.py -v

Exit code 0 = all tests pass.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Allow running this file directly (``python3
# test_partial_fill_accumulator.py``) from the component folder.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from partial_fill_accumulator import (  # noqa: E402
    Accumulator,
    FillEvent,
    LATE_FILL_LIQUIDITY,
    LateFillRejected,
    PartialFillJournal,
    PartialFillState,
    TERMINAL_STATUSES,
)


def _ts() -> int:
    return time.time_ns()


def _event(coid: str, trade_id: str, qty: float, price: float,
           side: str = "BUY", symbol: str = "BTCUSDT",
           liquidity: str = "taker", ts_ns: int = 0) -> FillEvent:
    return FillEvent(
        ts_ns=ts_ns or _ts(),
        client_order_id=coid,
        trade_id=trade_id,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        liquidity=liquidity,
    )


class TestPartialFillAccumulator(unittest.TestCase):
    """Happy path + failure mode coverage per INTERFACE.md §7."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "pfa.sqlite"
        self.journal = PartialFillJournal(self.db_path)
        self.acc = Accumulator(self.journal)

    def tearDown(self) -> None:
        self.acc.close()
        self._tmp.cleanup()

    # ----------------------------------------------------------------- happy

    def test_first_fill_creates_state(self) -> None:
        """Unknown coid on first fill → new state from the event."""
        s = self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.assertEqual(s.client_order_id, "coid-A")
        self.assertEqual(s.total_qty, 0.01)
        self.assertEqual(s.avg_price, 50000.0)
        self.assertEqual(s.notional_usd, 500.0)
        self.assertEqual(s.fill_count, 1)
        self.assertIsNone(s.terminal_status)

    def test_vwap_across_two_fills(self) -> None:
        """Two fills fold to VWAP: (0.01*50000 + 0.005*50100)/0.015."""
        self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        s2 = self.acc.on_fill(
            _event("coid-A", "t-2", qty=0.005, price=50100.0)
        )
        self.assertAlmostEqual(s2.total_qty, 0.015, places=9)
        self.assertAlmostEqual(s2.avg_price, 50033.333333333336, places=6)
        self.assertAlmostEqual(s2.notional_usd, 750.5, places=6)
        self.assertEqual(s2.fill_count, 2)

    def test_finalize_sets_terminal_status(self) -> None:
        """finalize() records the terminal marker and persists it."""
        self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        final = self.acc.finalize("coid-A", "FILLED")
        self.assertEqual(final.terminal_status, "FILLED")
        self.assertIsNotNone(final.terminal_ts_ns)
        # Persisted to journal too.
        persisted = self.journal.fetch_state("coid-A")
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.terminal_status, "FILLED")

    # ------------------------------------------------------------ failure

    def test_duplicate_trade_id_is_idempotent(self) -> None:
        """Re-sent WS event with same (coid, trade_id) returns existing
        state without a second journal row."""
        s1 = self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        s2 = self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.assertEqual(s1, s2)
        # One row in partial_fill_events, not two.
        rows = self.journal.fetch_events("coid-A")
        self.assertEqual(len(rows), 1)

    def test_late_fill_after_terminal_raises(self) -> None:
        """Fill after finalize raises LateFillRejected but journals
        the late event with LATE_FILL_LIQUIDITY for forensics."""
        self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.acc.finalize("coid-A", "FILLED")
        # Now send a late fill — should raise and journal with the
        # sentinel liquidity.
        with self.assertRaises(LateFillRejected):
            self.acc.on_fill(
                _event("coid-A", "t-2", qty=0.001, price=51000.0)
            )
        # State unchanged after the late fill.
        s = self.journal.fetch_state("coid-A")
        self.assertEqual(s.total_qty, 0.01)
        self.assertEqual(s.avg_price, 50000.0)
        # Late event recorded in the journal.
        events = self.journal.fetch_events("coid-A")
        self.assertEqual(len(events), 2)
        late = [e for e in events if e.trade_id == "t-2"][0]
        self.assertEqual(late.liquidity, LATE_FILL_LIQUIDITY)

    def test_unknown_terminal_status_raises_value_error(self) -> None:
        """finalize() rejects unknown terminal status values."""
        self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        with self.assertRaises(ValueError):
            self.acc.finalize("coid-A", "PARTIAL")  # not in TERMINAL_STATUSES

    def test_finalize_unknown_coid_raises_key_error(self) -> None:
        """finalize() on an unknown coid raises KeyError."""
        with self.assertRaises(KeyError):
            self.acc.finalize("never-seen", "FILLED")

    def test_double_finalize_same_status_is_idempotent(self) -> None:
        """Two finalize() calls with the same status return the existing
        state; the second does not overwrite."""
        self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        f1 = self.acc.finalize("coid-A", "FILLED")
        ts1 = f1.terminal_ts_ns
        # Sleep so any new ts would differ.
        time.sleep(0.01)
        f2 = self.acc.finalize("coid-A", "FILLED")
        self.assertEqual(f1.terminal_status, f2.terminal_status)
        self.assertEqual(f1.terminal_ts_ns, f2.terminal_ts_ns)
        self.assertEqual(ts1, f2.terminal_ts_ns)

    def test_double_finalize_different_status_no_op(self) -> None:
        """A contradicting finalize() does NOT overwrite the existing
        terminal marker."""
        self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        f1 = self.acc.finalize("coid-A", "FILLED")
        f2 = self.acc.finalize("coid-A", "CANCELED")
        self.assertEqual(f1.terminal_status, "FILLED")
        self.assertEqual(f2.terminal_status, "FILLED")  # not CANCELED

    # ------------------------------------------------------------ recovery

    def test_replay_rebuilds_state(self) -> None:
        """Replay rebuilds state from the journal after the in-memory
        cache is gone (simulates a process restart)."""
        self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.acc.on_fill(_event("coid-A", "t-2", qty=0.005, price=50100.0))
        self.acc.finalize("coid-A", "FILLED")
        # Wipe the in-memory cache and replay.
        self.acc._state.clear()  # noqa: SLF001 (white-box recovery test)
        replayed = self.acc.replay("coid-A")
        self.assertIsNotNone(replayed)
        self.assertAlmostEqual(replayed.total_qty, 0.015, places=9)
        self.assertAlmostEqual(replayed.avg_price, 50033.333333333336, places=6)
        self.assertEqual(replayed.fill_count, 2)
        self.assertEqual(replayed.terminal_status, "FILLED")

    def test_on_fill_hydrates_from_journal(self) -> None:
        """on_fill for a coid known to the journal but not in the
        in-memory cache re-hydrates and folds correctly (cold-start
        hot-path path)."""
        self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.acc._state.clear()  # noqa: SLF001 (simulate restart)
        # Next on_fill for the same coid should hydrate and fold.
        s = self.acc.on_fill(
            _event("coid-A", "t-2", qty=0.005, price=50100.0)
        )
        self.assertAlmostEqual(s.total_qty, 0.015, places=9)
        self.assertAlmostEqual(s.avg_price, 50033.333333333336, places=6)
        self.assertEqual(s.fill_count, 2)

    def test_replay_unknown_coid_returns_none(self) -> None:
        """replay() on a coid with no journal rows returns None."""
        self.assertIsNone(self.acc.replay("never-seen"))

    def test_known_orders_enumerates_cache(self) -> None:
        """known_orders() returns the in-memory keys."""
        self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        self.acc.on_fill(_event("coid-B", "t-2", qty=0.02, price=3000.0))
        keys = set(self.acc.known_orders())
        self.assertEqual(keys, {"coid-A", "coid-B"})

    # ------------------------------------------------------------ invariants

    def test_terminal_statuses_is_complete(self) -> None:
        """TERMINAL_STATUSES contains the canonical set per INTERFACE.md."""
        self.assertEqual(
            TERMINAL_STATUSES,
            frozenset({"FILLED", "CANCELED", "EXPIRED", "REJECTED"}),
        )

    def test_state_is_immutable(self) -> None:
        """PartialFillState is a frozen dataclass — attribute writes
        raise FrozenInstanceError."""
        s = self.acc.on_fill(_event("coid-A", "t-1", qty=0.01, price=50000.0))
        with self.assertRaises(Exception):
            # dataclass(frozen=True) → FrozenInstanceError at runtime.
            s.total_qty = 999.0  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)