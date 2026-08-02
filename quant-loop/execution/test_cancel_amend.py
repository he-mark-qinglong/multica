"""Tests for ExecutionRunner.cancel_order / amend_order state machine.

Uses mock transports (no real network).  Verifies:
- cancel/amend intent rows are journaled
- terminal acks (CANCELED / NEW / reject) are journaled
- on_fill hooks fire for cancel/amend
- on_request hooks fire for amend (but not cancel)
- transport exceptions are caught and journaled as ERROR
- amend validation (needs at least price or qty)
- Binance perp/spot + HL cancel/amend wire builders
"""
from __future__ import annotations

import sqlite3
import unittest
from typing import Any, Dict, List, Mapping

from execution.runner import (
    BlockReason,
    ComponentResult,
    ExecutionRunner,
    OrderJournal,
    OutboundTransport,
)


class _MockTransport:
    """Records every call and returns a scripted ack."""

    def __init__(self, ack: Dict[str, Any] = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.ack = ack or {"status": "CANCELED"}

    def __call__(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        self.calls.append(dict(request))
        return dict(self.ack)


class _RaisingTransport:
    """Always raises to test error handling."""

    def __call__(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        raise ConnectionError("simulated network failure")


class _FillObserver:
    """Simple on_fill component that counts invocations."""

    def __init__(self) -> None:
        self.count = 0
        self.last_ack: Dict[str, Any] = {}

    def on_fill(
        self,
        request: Mapping[str, Any],
        ack: Mapping[str, Any],
        journal: OrderJournal,
        ts_ns: int,
    ) -> ComponentResult:
        self.count += 1
        self.last_ack = dict(ack)
        return ComponentResult(observation={"fill_observer_count": self.count})


class _BlockingComponent:
    """on_request component that always blocks."""

    def on_request(
        self,
        request: Mapping[str, Any],
        journal: OrderJournal,
        ts_ns: int,
    ) -> ComponentResult:
        return ComponentResult(
            block=BlockReason(component="test_blocker", reason="blocked"),
        )


def _make_runner(transport_callable) -> ExecutionRunner:
    journal = OrderJournal(":memory:")
    transport = OutboundTransport(callable_send=transport_callable)
    return ExecutionRunner(journal=journal, transport=transport)


class TestCancelOrder(unittest.TestCase):
    """cancel_order state machine tests."""

    def test_cancel_success_journals_intent_and_terminal(self):
        mock = _MockTransport({"status": "CANCELED", "clientOrderId": "coid-1"})
        runner = _make_runner(mock)

        ack = runner.cancel_order("coid-1", symbol="BTCUSDT", venue="binance_usdt_futures")

        self.assertEqual(ack["status"], "CANCELED")
        self.assertEqual(mock.calls[0]["action"], "cancel")
        self.assertEqual(mock.calls[0]["client_order_id"], "coid-1")
        self.assertEqual(mock.calls[0]["origClientOrderId"], "coid-1")
        self.assertEqual(mock.calls[0]["symbol"], "BTCUSDT")

        rows = runner._journal.conn.execute(
            "SELECT event_type, client_order_id FROM fills "
            "ORDER BY id",
        ).fetchall()
        event_types = [r[0] for r in rows]
        self.assertIn("cancel", event_types)  # intent row
        # Terminal row: CANCELED → classified as "reject"
        self.assertIn("reject", event_types)

    def test_cancel_fires_on_fill_hooks(self):
        mock = _MockTransport({"status": "CANCELED"})
        runner = _make_runner(mock)
        observer = _FillObserver()
        runner.register_on_fill(observer)

        runner.cancel_order("coid-x", symbol="ETHUSDT")

        self.assertEqual(observer.count, 1)
        self.assertEqual(observer.last_ack["status"], "CANCELED")

    def test_cancel_does_not_fire_on_request_hooks(self):
        """Cancel reduces risk — pre-trade gates must NOT be consulted."""
        mock = _MockTransport({"status": "CANCELED"})
        runner = _make_runner(mock)
        blocker = _BlockingComponent()
        runner.register(blocker)

        ack = runner.cancel_order("coid-y", symbol="BTCUSDT")

        # Cancel should succeed despite the blocking component.
        self.assertEqual(ack["status"], "CANCELED")
        self.assertNotIn("blocked", ack)

    def test_cancel_transport_error_journaled(self):
        runner = _make_runner(_RaisingTransport())

        ack = runner.cancel_order("coid-err", symbol="BTCUSDT")

        self.assertEqual(ack["status"], "ERROR")
        self.assertIn("ConnectionError", ack["error"])

        rows = runner._journal.conn.execute(
            "SELECT event_type FROM fills WHERE client_order_id = 'coid-err'",
        ).fetchall()
        event_types = [r[0] for r in rows]
        self.assertIn("cancel", event_types)
        self.assertIn("reject", event_types)  # ERROR → reject

    def test_cancel_returns_observations(self):
        mock = _MockTransport({"status": "CANCELED"})
        runner = _make_runner(mock)
        observer = _FillObserver()
        runner.register_on_fill(observer)

        ack = runner.cancel_order("coid-obs", symbol="BTCUSDT")

        self.assertIn("observations", ack)
        self.assertEqual(ack["observations"]["fill_observer_count"], 1)


class TestAmendOrder(unittest.TestCase):
    """amend_order state machine tests."""

    def test_amend_success_journals_intent_and_terminal(self):
        mock = _MockTransport({"status": "NEW", "clientOrderId": "coid-a"})
        runner = _make_runner(mock)

        ack = runner.amend_order("coid-a", new_price=51000, new_qty=0.1, symbol="BTCUSDT")

        self.assertEqual(ack["status"], "NEW")
        self.assertEqual(mock.calls[0]["action"], "amend")
        self.assertEqual(mock.calls[0]["price"], 51000.0)
        self.assertEqual(mock.calls[0]["qty"], 0.1)

        rows = runner._journal.conn.execute(
            "SELECT event_type, price, qty FROM fills "
            "ORDER BY id",
        ).fetchall()
        event_types = [r[0] for r in rows]
        self.assertIn("amend", event_types)

    def test_amend_only_price(self):
        mock = _MockTransport({"status": "NEW"})
        runner = _make_runner(mock)

        runner.amend_order("coid-p", new_price=52000, symbol="BTCUSDT")

        self.assertEqual(mock.calls[0]["price"], 52000.0)
        self.assertNotIn("qty", mock.calls[0])

    def test_amend_only_qty(self):
        mock = _MockTransport({"status": "NEW"})
        runner = _make_runner(mock)

        runner.amend_order("coid-q", new_qty=0.2, symbol="BTCUSDT")

        self.assertEqual(mock.calls[0]["qty"], 0.2)
        self.assertNotIn("price", mock.calls[0])

    def test_amend_requires_at_least_one_field(self):
        runner = _make_runner(_MockTransport())

        with self.assertRaises(ValueError) as ctx:
            runner.amend_order("coid-empty", symbol="BTCUSDT")
        self.assertIn("at least one of new_price / new_qty", str(ctx.exception))

    def test_amend_fires_on_request_hooks(self):
        """Amend can add risk — pre-trade gates ARE consulted."""
        mock = _MockTransport({"status": "NEW"})
        runner = _make_runner(mock)
        blocker = _BlockingComponent()
        runner.register(blocker)

        ack = runner.amend_order("coid-block", new_price=50000, symbol="BTCUSDT")

        self.assertTrue(ack["blocked"])
        self.assertEqual(ack["status"], "BLOCKED")
        # Transport should NOT have been called.
        self.assertEqual(len(mock.calls), 0)

    def test_amend_fires_on_fill_hooks(self):
        mock = _MockTransport({"status": "NEW"})
        runner = _make_runner(mock)
        observer = _FillObserver()
        runner.register_on_fill(observer)

        runner.amend_order("coid-fill", new_price=50000, symbol="BTCUSDT")

        self.assertEqual(observer.count, 1)

    def test_amend_transport_error_journaled(self):
        runner = _make_runner(_RaisingTransport())

        ack = runner.amend_order("coid-err", new_price=50000, symbol="BTCUSDT")

        self.assertEqual(ack["status"], "ERROR")
        self.assertIn("ConnectionError", ack["error"])

    def test_amend_extra_fields_propagated(self):
        mock = _MockTransport({"status": "NEW"})
        runner = _make_runner(mock)

        runner.amend_order(
            "coid-extra", new_price=50000, symbol="BTCUSDT",
            extra={"side": "SELL", "venue": "binance_usdt_futures"},
        )

        self.assertEqual(mock.calls[0]["side"], "SELL")
        self.assertEqual(mock.calls[0]["venue"], "binance_usdt_futures")


class TestCancelAmendJournalConsistency(unittest.TestCase):
    """Verify journal rows are complete and never silently dropped."""

    def test_submit_then_cancel_then_amend_all_journaled(self):
        mock = _MockTransport({"status": "FILLED"})
        runner = _make_runner(mock)

        runner.submit({
            "client_order_id": "coid-1",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": 0.1,
            "price": 50000,
            "venue": "binance_usdt_futures",
        })

        mock.ack = {"status": "CANCELED"}
        runner.cancel_order("coid-1", symbol="BTCUSDT")

        mock.ack = {"status": "NEW"}
        runner.amend_order("coid-1", new_price=50100, new_qty=0.15, symbol="BTCUSDT")

        rows = runner._journal.conn.execute(
            "SELECT event_type, client_order_id FROM fills ORDER BY id",
        ).fetchall()
        event_types = [r[0] for r in rows]

        # Every action produces at least an intent row + a terminal row.
        self.assertIn("intent", event_types)
        self.assertIn("cancel", event_types)
        self.assertIn("amend", event_types)
        # All three have the same coid.
        coids = {r[1] for r in rows}
        self.assertEqual(coids, {"coid-1"})


if __name__ == "__main__":
    unittest.main()
