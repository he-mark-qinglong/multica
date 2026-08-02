"""Regression tests for execution.runner.classify_terminal_event.

Bug 5: unknown acks defaulted to "fill" (correctness risk).
Fix: unknown acks default to "reject" (fail-safe) and log a warning.
"""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from execution.runner import classify_terminal_event


# ---- Known statuses still classify correctly ----

def test_known_fill_statuses():
    for status in ("FILLED", "filled", "PARTIALLY_FILLED", "partially_filled"):
        assert classify_terminal_event({"status": status}) == "fill"

def test_known_reject_statuses():
    for status in ("REJECTED", "rejected", "EXPIRED", "expired",
                    "CANCELED", "cancelled"):
        assert classify_terminal_event({"status": status}) == "reject"

def test_ok_false_is_reject():
    assert classify_terminal_event({"ok": False}) == "reject"

def test_ok_true_is_fill():
    """Minimal stub ack with ok=True still classifies as fill."""
    assert classify_terminal_event({"ok": True}) == "fill"

def test_error_code_is_reject():
    assert classify_terminal_event({"code": -1000}) == "reject"


# ---- Regression: unknown ack must default to reject, not fill ----

def test_unknown_status_defaults_to_reject():
    """An unrecognised status string must be treated as reject (fail-safe).

    Before the fix, this returned "fill", risking phantom fills.
    """
    assert classify_terminal_event({"status": "NEW_HOPE"}) == "reject"

def test_empty_ack_defaults_to_reject():
    """A completely empty ack must not be treated as a fill."""
    assert classify_terminal_event({}) == "reject"

def test_unknown_status_logs_warning(caplog):
    """Unknown ack should emit a warning so operators can investigate."""
    with caplog.at_level("WARNING"):
        result = classify_terminal_event({"status": "WEIRD"})
    assert result == "reject"
    assert any("unrecognised ack" in rec.message for rec in caplog.records)
