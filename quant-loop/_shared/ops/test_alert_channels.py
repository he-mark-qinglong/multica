"""Tests for _shared/ops/alert_channels.py (H7)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json
import smtplib
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from _shared.ops.alert_channels import (
    EmailSink,
    TelegramSink,
    alert_message,
)
from _shared.ops.alerting import Alert, AlertLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_alert(level="WARN", rule="test_rule", message="something happened"):
    return Alert(
        ts=1700000000.0,
        level=level,
        rule=rule,
        message=message,
        context={"symbol": "BTCUSDT", "value": 42.0},
    )


# ---------------------------------------------------------------------------
# alert_message
# ---------------------------------------------------------------------------

def test_alert_message_contains_level_and_rule():
    """Formatted message should include level and rule."""
    alert = _make_alert(level="CRITICAL", rule="drawdown")
    msg = alert_message(alert)
    assert "CRITICAL" in msg
    assert "drawdown" in msg


def test_alert_message_contains_message_text():
    """Formatted message should include the alert message."""
    alert = _make_alert(message="drawdown exceeded 10%")
    msg = alert_message(alert)
    assert "drawdown exceeded 10%" in msg


def test_alert_message_contains_context():
    """Formatted message should include context values."""
    alert = _make_alert()
    msg = alert_message(alert)
    assert "BTCUSDT" in msg
    assert "42.0" in msg


def test_alert_message_empty_context():
    """Empty context should still produce a valid message."""
    alert = Alert(ts=0.0, level="INFO", rule="heartbeat", message="ok")
    msg = alert_message(alert)
    assert "INFO" in msg
    assert "heartbeat" in msg
    assert "ok" in msg


# ---------------------------------------------------------------------------
# TelegramSink
# ---------------------------------------------------------------------------

def test_telegram_sink_emit_calls_urlopen(monkeypatch):
    """emit() should POST to the Telegram API."""
    mock_response = BytesIO(b'{"ok":true}')
    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value = mock_response
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sink = TelegramSink(bot_token="123:abc", chat_id="-100123")
    alert = _make_alert()
    sink.emit(alert)

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    assert "api.telegram.org" in req.full_url
    assert req.get_method() == "POST"


def test_telegram_sink_payload_contains_chat_id(monkeypatch):
    """The POST body should include the chat_id."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode())
        captured["headers"] = dict(req.headers)
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=BytesIO(b'{"ok":true}'))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    sink = TelegramSink(bot_token="tok", chat_id="chat123")
    sink.emit(_make_alert())

    assert captured["data"]["chat_id"] == "chat123"
    assert "test_rule" in captured["data"]["text"]


def test_telegram_sink_cooldown_suppresses(monkeypatch):
    """Second alert within cooldown should be suppressed."""
    emit_count = [0]

    def fake_urlopen(req, timeout=None):
        emit_count[0] += 1
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=BytesIO(b'{"ok":true}'))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    sink = TelegramSink("tok", "chat", cooldown_sec=60.0)
    sink.emit(_make_alert(rule="same_rule"))
    sink.emit(_make_alert(rule="same_rule"))  # should be suppressed

    assert emit_count[0] == 1  # only first one went through


def test_telegram_sink_cooldown_different_rules(monkeypatch):
    """Different rules should not suppress each other."""
    emit_count = [0]

    def fake_urlopen(req, timeout=None):
        emit_count[0] += 1
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=BytesIO(b'{"ok":true}'))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    sink = TelegramSink("tok", "chat", cooldown_sec=60.0)
    sink.emit(_make_alert(rule="rule_a"))
    sink.emit(_make_alert(rule="rule_b"))

    assert emit_count[0] == 2


def test_telegram_sink_no_cooldown_by_default(monkeypatch):
    """Without cooldown_sec, every alert should be sent."""
    emit_count = [0]

    def fake_urlopen(req, timeout=None):
        emit_count[0] += 1
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=BytesIO(b'{"ok":true}'))
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    sink = TelegramSink("tok", "chat")  # default cooldown = 0
    sink.emit(_make_alert(rule="same_rule"))
    sink.emit(_make_alert(rule="same_rule"))

    assert emit_count[0] == 2


# ---------------------------------------------------------------------------
# EmailSink
# ---------------------------------------------------------------------------

def test_email_sink_emit_sends_email(monkeypatch):
    """emit() should connect to SMTP and send."""
    mock_smtp = MagicMock()
    monkeypatch.setattr(smtplib, "SMTP", mock_smtp)

    sink = EmailSink(
        smtp_host="smtp.test.com", smtp_port=587,
        sender="alerts@test.com", password="pass",
        recipients=["trader@test.com"],
    )
    sink.emit(_make_alert())

    assert mock_smtp.called
    instance = mock_smtp.return_value.__enter__.return_value
    instance.starttls.assert_called_once()
    instance.login.assert_called_once_with("alerts@test.com", "pass")
    instance.sendmail.assert_called_once()


def test_email_sink_subject_contains_level(monkeypatch):
    """Email subject should contain the alert level and rule."""
    captured = {}
    mock_smtp_instance = MagicMock()
    mock_smtp_instance.sendmail = lambda sender, recipients, body: captured.__setitem__("body", body)

    mock_smtp = MagicMock()
    mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(smtplib, "SMTP", mock_smtp)

    sink = EmailSink("host", 587, "s@t.com", "p", ["r@t.com"])
    sink.emit(_make_alert(level="CRITICAL", rule="kill_switch"))

    assert "CRITICAL" in captured["body"]
    assert "kill_switch" in captured["body"]


def test_email_sink_cooldown_suppresses(monkeypatch):
    """Cooldown should suppress repeated alerts."""
    send_count = [0]
    mock_instance = MagicMock()
    mock_instance.sendmail = lambda *a, **k: send_count.__setitem__(0, send_count[0] + 1)

    mock_smtp = MagicMock()
    mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_instance)
    mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(smtplib, "SMTP", mock_smtp)

    sink = EmailSink("host", 587, "s@t.com", "p", ["r@t.com"], cooldown_sec=60.0)
    sink.emit(_make_alert(rule="dd"))
    sink.emit(_make_alert(rule="dd"))

    assert send_count[0] == 1


def test_email_sink_tls_disabled(monkeypatch):
    """use_tls=False should skip starttls."""
    mock_smtp = MagicMock()
    monkeypatch.setattr(smtplib, "SMTP", mock_smtp)

    sink = EmailSink("host", 587, "s@t.com", "p", ["r@t.com"], use_tls=False)
    sink.emit(_make_alert())

    instance = mock_smtp.return_value.__enter__.return_value
    instance.starttls.assert_not_called()
    instance.login.assert_called_once()


# ---------------------------------------------------------------------------
# Protocol compliance (duck typing)
# ---------------------------------------------------------------------------

def test_telegram_sink_has_emit_method():
    """TelegramSink should have an emit method (AlertSink protocol)."""
    sink = TelegramSink("tok", "chat")
    assert hasattr(sink, "emit")
    assert callable(sink.emit)


def test_email_sink_has_emit_method():
    """EmailSink should have an emit method (AlertSink protocol)."""
    sink = EmailSink("host", 587, "s@t.com", "p", ["r@t.com"])
    assert hasattr(sink, "emit")
    assert callable(sink.emit)
