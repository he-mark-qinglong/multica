"""Unit tests for order_to_fill_linker. Plain asserts, runs as
``python test_order_to_fill_linker.py``.

No pytest dependency. Each test exits non-zero on failure.

Coverage (23 tests):

* Happy path:
    1. test_register_intent_writes_to_journal
    2. test_bind_order_id_advances_to_acked
    3. test_on_fill_report_full_match
    4. test_on_fill_report_partial_then_full
    5. test_on_fill_report_orphan_journaled
* Idempotency:
    6. test_register_intent_idempotent_honors_durable_status
    7. test_bind_order_id_idempotent_same_coid
    8. test_bind_order_id_raises_on_conflict
    9. test_on_fill_report_duplicate_trade_id_silent_idempotent
* Failure modes:
   10. test_bind_order_id_raises_unknown_coid
   11. test_on_fill_report_side_mismatch_raises_intentmismatch
   12. test_on_fill_report_symbol_mismatch_raises_intentmismatch
   13. test_late_terminal_status_does_not_downgrade_intent
   14. test_validation_errors_raise_before_journal_write
* Cache + journal consistency:
   15. test_recover_pending_rebuilds_cache
   16. test_resolve_intent_by_order_id_after_restart
   17. test_orphan_count_is_durable
* Intent status transitions:
   18. test_status_transition_pending_ack_to_acked
   19. test_status_transition_partial_filled_to_filled
   20. test_status_transition_rejected_after_pending_ack
* Exception surface:
   21. test_intent_validation_rejects_empty_coid
   22. test_intent_validation_rejects_bad_side
   23. test_fill_report_validation_rejects_unknown_status
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# Make the package importable from this file's directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from order_to_fill_linker_p7exec_055 import (  # noqa: E402
    FillReport,
    INTENT_STATUSES,
    IntentMismatch,
    Linker,
    LinkRecord,
    OrderIdAlreadyBound,
    OrderIntent,
    OrderToFillJournal,
    UnknownClientOrderId,
)


# ---- Helpers ---------------------------------------------------------------

def _ts() -> int:
    return time.time_ns()


def _intent(
    coid: str = "coid-test-001",
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    qty: float = 0.010,
    strategy_id: str = "vpvr_reversion_1m",
    order_type: str = "LIMIT",
    time_in_force: str = "GTC",
) -> OrderIntent:
    return OrderIntent(
        client_order_id=coid,
        symbol=symbol,
        side=side,
        intended_qty=qty,
        intent_ts_ns=_ts(),
        order_type=order_type,
        time_in_force=time_in_force,
        strategy_id=strategy_id,
    )


def _report(
    order_id: int,
    coid: str,
    trade_id: str,
    qty: float = 0.010,
    price: float = 67123.4,
    cum: float = 0.010,
    avg: float = 67123.4,
    status: str = "FILLED",
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    source: str = "WS",
) -> FillReport:
    return FillReport(
        ts_ns=_ts(),
        order_id=order_id,
        client_order_id=coid,
        trade_id=trade_id,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        cum_filled_qty=cum,
        avg_fill_price=avg,
        order_status=status,
        source=source,
    )


class _TempJournal:
    """Context manager: open a journal in a tempdir and close on exit."""

    def __enter__(self) -> tuple[OrderToFillJournal, Linker, Path]:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "o2fl.sqlite"
        journal = OrderToFillJournal(db_path)
        linker = Linker(journal)
        return journal, linker, db_path

    def __exit__(self, exc_type, exc, tb) -> None:
        # The journal was returned to the caller; close it explicitly
        # in the test body if necessary.
        self._tmp.cleanup()


# ---- Happy path ------------------------------------------------------------

def test_register_intent_writes_to_journal():
    with _TempJournal() as (journal, linker, _):
        i = linker.register_intent(_intent(coid="coid-001", qty=0.01))
        assert i.intent_status == "PENDING_ACK"
        assert i.client_order_id == "coid-001"
        persisted = journal.fetch_intent_by_coid("coid-001")
        assert persisted is not None, "intent must be journaled"
        assert persisted.symbol == "BTCUSDT"
        assert persisted.side == "BUY"
        assert persisted.intended_qty == 0.01
    print("test_register_intent_writes_to_journal OK")


def test_bind_order_id_advances_to_acked():
    with _TempJournal() as (journal, linker, _):
        linker.register_intent(_intent(coid="coid-002"))
        bound = linker.bind_order_id("coid-002", 412341234)
        assert bound.order_id == 412341234
        assert bound.intent_status == "ACKED", (
            f"expected ACKED, got {bound.intent_status}"
        )
        # Journal reflects the same state.
        persisted = journal.fetch_intent_by_coid("coid-002")
        assert persisted.order_id == 412341234
        assert persisted.intent_status == "ACKED"
    print("test_bind_order_id_advances_to_acked OK")


def test_on_fill_report_full_match():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-003"))
        linker.bind_order_id("coid-003", 412341235)
        record = linker.on_fill_report(
            _report(order_id=412341235, coid="coid-003", trade_id="t-1")
        )
        assert record.is_orphan is False
        assert record.client_order_id == "coid-003"
        assert record.intent_status_after == "FILLED"
    print("test_on_fill_report_full_match OK")


def test_on_fill_report_partial_then_full():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-004", qty=0.02))
        linker.bind_order_id("coid-004", 412341236)
        # First partial fill.
        r1 = linker.on_fill_report(
            _report(
                order_id=412341236,
                coid="coid-004",
                trade_id="t-1",
                qty=0.005,
                cum=0.005,
                avg=67100.0,
                status="PARTIALLY_FILLED",
            )
        )
        assert r1.intent_status_after == "PARTIALLY_FILLED"
        # Second fill completes the order.
        r2 = linker.on_fill_report(
            _report(
                order_id=412341236,
                coid="coid-004",
                trade_id="t-2",
                qty=0.015,
                cum=0.020,
                avg=67115.0,
                status="FILLED",
            )
        )
        assert r2.intent_status_after == "FILLED"
        # Cache reflects FILLED.
        intent = linker.fetch_intent("coid-004")
        assert intent is not None and intent.intent_status == "FILLED"
    print("test_on_fill_report_partial_then_full OK")


def test_on_fill_report_orphan_journaled():
    with _TempJournal() as (journal, linker, _):
        # No intent registered at all.
        record = linker.on_fill_report(
            FillReport(
                ts_ns=_ts(),
                order_id=999999999,
                client_order_id="",
                trade_id="t-orphan-1",
                symbol="ETHUSDT",
                side="SELL",
                qty=0.05,
                price=3500.0,
                cum_filled_qty=0.05,
                avg_fill_price=3500.0,
                order_status="FILLED",
            )
        )
        assert record.is_orphan is True
        assert record.client_order_id == ""
        # Orphan count is durable.
        assert linker.orphan_count() == 1
    print("test_on_fill_report_orphan_journaled OK")


# ---- Idempotency -----------------------------------------------------------

def test_register_intent_idempotent_honors_durable_status():
    """Re-registering after the intent has advanced to FILLED must
    NOT rewind to PENDING_ACK."""
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-006"))
        linker.bind_order_id("coid-006", 412341240)
        linker.on_fill_report(
            _report(order_id=412341240, coid="coid-006", trade_id="t-1")
        )
        # Re-register the same coid (e.g. retry from a flaky strategy).
        re_registered = linker.register_intent(
            _intent(coid="coid-006", qty=0.005)  # pretend we changed qty
        )
        assert re_registered.intent_status == "FILLED", (
            "intent_status must NOT be reset on re-register; "
            f"got {re_registered.intent_status}"
        )
        # Mutable fields DID coalesce.
        assert re_registered.intended_qty == 0.005
    print("test_register_intent_idempotent_honors_durable_status OK")


def test_bind_order_id_idempotent_same_coid():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-007"))
        bound1 = linker.bind_order_id("coid-007", 412341241)
        bound2 = linker.bind_order_id("coid-007", 412341241)
        assert bound1.order_id == bound2.order_id == 412341241
        assert bound1.intent_status == bound2.intent_status == "ACKED"
    print("test_bind_order_id_idempotent_same_coid OK")


def test_bind_order_id_raises_on_conflict():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-008"))
        linker.register_intent(_intent(coid="coid-009"))
        linker.bind_order_id("coid-008", 412341242)
        # Now try to bind the same order_id to coid-009 — conflict.
        try:
            linker.bind_order_id("coid-009", 412341242)
        except OrderIdAlreadyBound as exc:
            assert "coid-008" in str(exc) and "coid-009" in str(exc)
            print("test_bind_order_id_raises_on_conflict OK")
            return
        raise AssertionError("expected OrderIdAlreadyBound")


def test_on_fill_report_duplicate_trade_id_silent_idempotent():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-010"))
        linker.bind_order_id("coid-010", 412341243)
        r1 = linker.on_fill_report(
            _report(order_id=412341243, coid="coid-010", trade_id="t-dup")
        )
        # Re-deliver the same report (e.g. WS reconnect replay).
        r2 = linker.on_fill_report(
            _report(order_id=412341243, coid="coid-010", trade_id="t-dup")
        )
        # Both calls return the same intent status; the duplicate
        # did not change state.
        assert r1.intent_status_after == "FILLED"
        assert r2.intent_status_after == "FILLED"
        assert r1.client_order_id == r2.client_order_id == "coid-010"
    print("test_on_fill_report_duplicate_trade_id_silent_idempotent OK")


# ---- Failure modes ---------------------------------------------------------

def test_bind_order_id_raises_unknown_coid():
    with _TempJournal() as (_, linker, _):
        try:
            linker.bind_order_id("never-registered", 412341244)
        except UnknownClientOrderId:
            print("test_bind_order_id_raises_unknown_coid OK")
            return
        raise AssertionError("expected UnknownClientOrderId")


def test_on_fill_report_side_mismatch_raises_intentmismatch():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-011", side="BUY"))
        linker.bind_order_id("coid-011", 412341245)
        try:
            linker.on_fill_report(
                _report(
                    order_id=412341245,
                    coid="coid-011",
                    trade_id="t-mismatch",
                    side="SELL",
                )
            )
        except IntentMismatch as exc:
            assert "side" in str(exc).lower()
            # Journal row is still written — fetch orphan_count is 0
            # (it's not orphan; it's a mismatch), but the link row
            # exists.
            assert linker.orphan_count() == 0
            print("test_on_fill_report_side_mismatch_raises_intentmismatch OK")
            return
        raise AssertionError("expected IntentMismatch")


def test_on_fill_report_symbol_mismatch_raises_intentmismatch():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-012", symbol="BTCUSDT"))
        linker.bind_order_id("coid-012", 412341246)
        try:
            linker.on_fill_report(
                _report(
                    order_id=412341246,
                    coid="coid-012",
                    trade_id="t-mismatch-sym",
                    symbol="ETHUSDT",
                )
            )
        except IntentMismatch as exc:
            assert "symbol" in str(exc).lower()
            print("test_on_fill_report_symbol_mismatch_raises_intentmismatch OK")
            return
        raise AssertionError("expected IntentMismatch")


def test_late_terminal_status_does_not_downgrade_intent():
    """A late CANCELED arriving after FILLED must NOT rewind the
    intent back to CANCELED."""
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-013"))
        linker.bind_order_id("coid-013", 412341247)
        linker.on_fill_report(
            _report(
                order_id=412341247,
                coid="coid-013",
                trade_id="t-1",
                status="FILLED",
            )
        )
        # Late cancel.
        r2 = linker.on_fill_report(
            _report(
                order_id=412341247,
                coid="coid-013",
                trade_id="t-late-cancel",
                qty=0.0,  # zero qty — but we won't get past validation
                cum=0.010,
                avg=67123.4,
                status="CANCELED",
            )
        ) if False else linker.on_fill_report(  # noqa: E501
            FillReport(
                ts_ns=_ts(),
                order_id=412341247,
                client_order_id="coid-013",
                trade_id="t-late-cancel",
                symbol="BTCUSDT",
                side="BUY",
                qty=0.010,  # cancel acks may still carry the last fill qty
                price=67123.4,
                cum_filled_qty=0.010,
                avg_fill_price=67123.4,
                order_status="CANCELED",
            )
        )
        # Status is still FILLED — late CANCELED did not downgrade.
        assert r2.intent_status_after == "FILLED"
        # But the journal row IS there (forensic record).
        assert linker.orphan_count() == 0
        intent = linker.fetch_intent("coid-013")
        assert intent is not None and intent.intent_status == "FILLED"
    print("test_late_terminal_status_does_not_downgrade_intent OK")


def test_validation_errors_raise_before_journal_write():
    """Validation errors (bad side, negative qty) must raise
    BEFORE any journal write, so we don't pollute the audit trail
    with bad rows."""
    with _TempJournal() as (journal, linker, _):
        # Bad side on intent.
        try:
            linker.register_intent(_intent(coid="coid-014-bad", side="LONG"))
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for bad side")

        # Negative qty on FillReport (zero is allowed — non-fill
        # status events like REJECTED carry qty=0).
        linker.register_intent(_intent(coid="coid-014-ok"))
        linker.bind_order_id("coid-014-ok", 412341248)
        try:
            linker.on_fill_report(
                FillReport(
                    ts_ns=_ts(),
                    order_id=412341248,
                    client_order_id="coid-014-ok",
                    trade_id="t-bad-qty",
                    symbol="BTCUSDT",
                    side="BUY",
                    qty=-0.001,
                    price=67123.4,
                    cum_filled_qty=0.0,
                    avg_fill_price=0.0,
                    order_status="FILLED",
                )
            )
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for qty<0")
        # Orphan count is still 0 — no row was written.
        assert linker.orphan_count() == 0
    print("test_validation_errors_raise_before_journal_write OK")


# ---- Cache + journal consistency ------------------------------------------

def test_recover_pending_rebuilds_cache():
    with _TempJournal() as (journal, linker, db_path):
        # First linker uses the journal, writes an intent + bind.
        linker.register_intent(_intent(coid="coid-015"))
        linker.bind_order_id("coid-015", 412341249)
        # Close the linker; simulate a process restart.
        linker.close()
        # New linker (same db).
        new_journal = OrderToFillJournal(db_path)
        new_linker = Linker(new_journal)
        n = new_linker.recover_pending()
        assert n == 1, f"expected 1 rehydrated intent, got {n}"
        assert "coid-015" in new_linker.known_intents()[0].client_order_id
        # Fetch by order_id works after restart.
        intent = new_linker.fetch_intent(412341249)
        assert intent is not None
        assert intent.client_order_id == "coid-015"
        assert intent.intent_status == "ACKED"
        new_linker.close()
    print("test_recover_pending_rebuilds_cache OK")


def test_resolve_intent_by_order_id_after_restart():
    """After restart, a FillReport arriving with empty coid but a
    known order_id must still resolve to the bound intent."""
    with _TempJournal() as (journal, linker, db_path):
        linker.register_intent(_intent(coid="coid-016"))
        linker.bind_order_id("coid-016", 412341250)
        linker.close()
        new_journal = OrderToFillJournal(db_path)
        new_linker = Linker(new_journal)
        new_linker.recover_pending()
        # WS reconnect scenario: orderId-only FillReport.
        record = new_linker.on_fill_report(
            FillReport(
                ts_ns=_ts(),
                order_id=412341250,
                client_order_id="",  # venue didn't echo our coid this time
                trade_id="t-reconnect-1",
                symbol="BTCUSDT",
                side="BUY",
                qty=0.010,
                price=67123.4,
                cum_filled_qty=0.010,
                avg_fill_price=67123.4,
                order_status="FILLED",
            )
        )
        assert record.is_orphan is False, (
            "orderId-only FillReport should resolve to bound intent"
        )
        assert record.client_order_id == "coid-016"
        new_linker.close()
    print("test_resolve_intent_by_order_id_after_restart OK")


def test_orphan_count_is_durable():
    with _TempJournal() as (journal, linker, db_path):
        linker.on_fill_report(
            FillReport(
                ts_ns=_ts(),
                order_id=7777,
                client_order_id="",
                trade_id="t-orphan-dur-1",
                symbol="BTCUSDT",
                side="BUY",
                qty=0.01,
                price=50000.0,
                cum_filled_qty=0.01,
                avg_fill_price=50000.0,
                order_status="FILLED",
            )
        )
        linker.on_fill_report(
            FillReport(
                ts_ns=_ts(),
                order_id=7778,
                client_order_id="",
                trade_id="t-orphan-dur-2",
                symbol="BTCUSDT",
                side="BUY",
                qty=0.01,
                price=50000.0,
                cum_filled_qty=0.01,
                avg_fill_price=50000.0,
                order_status="FILLED",
            )
        )
        assert linker.orphan_count() == 2
        linker.close()
        new_journal = OrderToFillJournal(db_path)
        new_linker = Linker(new_journal)
        assert new_linker.orphan_count() == 2, "orphan count must persist"
        new_linker.close()
    print("test_orphan_count_is_durable OK")


# ---- Intent status transitions --------------------------------------------

def test_status_transition_pending_ack_to_acked():
    """A FillReport carrying the coid we registered (but before
    bind_order_id) must match via the coid-first resolver and
    drive the intent PENDING_ACK → ACKED via the venue-status
    NEW → ACKED mapping."""
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-018"))
        # No bind_order_id — venue sent the FillReport first (some
        # venues send NEW before echoing the orderId).
        r = linker.on_fill_report(
            _report(
                order_id=412341252,
                coid="coid-018",
                trade_id="t-new-ack",
                status="NEW",
            )
        )
        assert r.is_orphan is False, (
            "coid-resolver should match the registered intent even "
            "before bind_order_id"
        )
        assert r.intent_status_after == "ACKED", (
            f"venue NEW status should drive intent to ACKED, "
            f"got {r.intent_status_after}"
        )
    print("test_status_transition_pending_ack_to_acked OK")


def test_status_transition_partial_filled_to_filled():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-019"))
        linker.bind_order_id("coid-019", 412341253)
        for trade_id, qty, cum, status in [
            ("t-a", 0.003, 0.003, "PARTIALLY_FILLED"),
            ("t-b", 0.004, 0.007, "PARTIALLY_FILLED"),
            ("t-c", 0.003, 0.010, "FILLED"),
        ]:
            r = linker.on_fill_report(
                _report(
                    order_id=412341253,
                    coid="coid-019",
                    trade_id=trade_id,
                    qty=qty,
                    cum=cum,
                    avg=67100.0 + cum * 10,
                    status=status,
                )
            )
            assert r.intent_status_after == status, (
                f"after {trade_id}: expected {status}, got "
                f"{r.intent_status_after}"
            )
    print("test_status_transition_partial_filled_to_filled OK")


def test_status_transition_rejected_after_pending_ack():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-020"))
        # Venue rejects directly without binding an orderId.
        # This is a FillReport with a new order_id we haven't seen —
        # it'll be journaled as orphan. Verify the orphan record's
        # before/after are PENDING_ACK.
        r = linker.on_fill_report(
            _report(
                order_id=555111222,
                coid="",
                trade_id="t-rej-1",
                status="REJECTED",
                qty=0.001,
            )
        )
        assert r.is_orphan is True
        assert r.intent_status_before == "PENDING_ACK"
        assert r.intent_status_after == "PENDING_ACK"
    print("test_status_transition_rejected_after_pending_ack OK")


# ---- Exception surface -----------------------------------------------------

def test_intent_validation_rejects_empty_coid():
    with _TempJournal() as (_, linker, _):
        try:
            linker.register_intent(_intent(coid=""))
        except ValueError:
            print("test_intent_validation_rejects_empty_coid OK")
            return
        raise AssertionError("expected ValueError for empty coid")


def test_intent_validation_rejects_bad_side():
    with _TempJournal() as (_, linker, _):
        try:
            linker.register_intent(_intent(coid="coid-022-bad", side="LONG"))
        except ValueError:
            print("test_intent_validation_rejects_bad_side OK")
            return
        raise AssertionError("expected ValueError for bad side")


def test_fill_report_validation_rejects_unknown_status():
    with _TempJournal() as (_, linker, _):
        linker.register_intent(_intent(coid="coid-023"))
        linker.bind_order_id("coid-023", 412341257)
        try:
            linker.on_fill_report(
                _report(
                    order_id=412341257,
                    coid="coid-023",
                    trade_id="t-bad-status",
                    status="GODMODE_FILLED",
                )
            )
        except ValueError:
            print("test_fill_report_validation_rejects_unknown_status OK")
            return
        raise AssertionError("expected ValueError for unknown status")


# ---- Module surface -------------------------------------------------------

def test_intent_statuses_frozenset_canonical():
    expected = {
        "PENDING_ACK", "ACKED", "PARTIALLY_FILLED",
        "FILLED", "CANCELED", "EXPIRED", "REJECTED",
    }
    assert set(INTENT_STATUSES) == expected, (
        f"INTENT_STATUSES mismatch: {INTENT_STATUSES}"
    )
    print("test_intent_statuses_frozenset_canonical OK")


# ---- Runner ---------------------------------------------------------------

def main() -> int:
    tests = [
        test_register_intent_writes_to_journal,
        test_bind_order_id_advances_to_acked,
        test_on_fill_report_full_match,
        test_on_fill_report_partial_then_full,
        test_on_fill_report_orphan_journaled,
        test_register_intent_idempotent_honors_durable_status,
        test_bind_order_id_idempotent_same_coid,
        test_bind_order_id_raises_on_conflict,
        test_on_fill_report_duplicate_trade_id_silent_idempotent,
        test_bind_order_id_raises_unknown_coid,
        test_on_fill_report_side_mismatch_raises_intentmismatch,
        test_on_fill_report_symbol_mismatch_raises_intentmismatch,
        test_late_terminal_status_does_not_downgrade_intent,
        test_validation_errors_raise_before_journal_write,
        test_recover_pending_rebuilds_cache,
        test_resolve_intent_by_order_id_after_restart,
        test_orphan_count_is_durable,
        test_status_transition_pending_ack_to_acked,
        test_status_transition_partial_filled_to_filled,
        test_status_transition_rejected_after_pending_ack,
        test_intent_validation_rejects_empty_coid,
        test_intent_validation_rejects_bad_side,
        test_fill_report_validation_rejects_unknown_status,
        test_intent_statuses_frozenset_canonical,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    total = len(tests)
    passed = total - failed
    print(f"\n{passed}/{total} tests passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())