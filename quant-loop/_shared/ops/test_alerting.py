"""Tests for _shared/ops/alerting.py (H5)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json

import pytest

from _shared.ops.alerting import (
    AlertLevel,
    Alerter,
    LogFileSink,
    WebhookSink,
    check_data_gap,
    check_drawdown,
    check_kill_switch,
)


# --- pure rules ---------------------------------------------------------------
def test_drawdown_rule_fires_above_threshold():
    alert = check_drawdown(peak_equity=10_000, current_equity=8_900,
                           threshold_pct=10.0, now=1000.0)
    assert alert is not None
    assert alert.level == AlertLevel.CRITICAL.value
    assert alert.rule == "drawdown"
    assert alert.context["drawdown_pct"] == pytest.approx(11.0)
    assert alert.ts == 1000.0


def test_drawdown_rule_silent_below_threshold():
    assert check_drawdown(10_000, 9_500, threshold_pct=10.0) is None
    assert check_drawdown(0, 0, threshold_pct=10.0) is None


def test_kill_switch_rule():
    assert check_kill_switch(False) is None
    alert = check_kill_switch(True, reason="pf < 1.2", now=5.0)
    assert alert is not None
    assert alert.level == AlertLevel.CRITICAL.value
    assert "pf < 1.2" in alert.message


def test_data_gap_rule():
    assert check_data_gap(last_data_ts=100.0, now_ts=110.0, max_gap_sec=30.0) is None
    alert = check_data_gap(100.0, 131.0, 30.0, feed="trades")
    assert alert is not None
    assert alert.context["feed"] == "trades"
    assert alert.context["gap_sec"] == pytest.approx(31.0)


# --- sinks + alerter ----------------------------------------------------------
def test_log_file_sink_writes_json_lines(tmp_path):
    sink = LogFileSink(tmp_path / "alerts.jsonl")
    alerter = Alerter([sink])
    alerter.evaluate(
        check_kill_switch(True, "test", now=1.0),
        check_data_gap(0.0, 100.0, 10.0),
    )
    lines = (tmp_path / "alerts.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["rule"] == "kill_switch"
    assert json.loads(lines[1])["rule"] == "data_gap"


def test_webhook_sink_posts_json(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["content_type"] = req.headers["Content-type"]
        captured["timeout"] = timeout
        return FakeResp()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    sink = WebhookSink("https://hooks.example.com/x", timeout_sec=2.0)
    sink.emit(check_kill_switch(True, "webhook test", now=2.0))
    assert captured["url"] == "https://hooks.example.com/x"
    assert captured["body"]["rule"] == "kill_switch"
    assert captured["content_type"] == "application/json"
    assert captured["timeout"] == 2.0


def test_sink_failure_does_not_block_other_sinks(tmp_path):
    class BadSink:
        def emit(self, alert):
            raise RuntimeError("network down")

    good = LogFileSink(tmp_path / "ok.jsonl")
    alerter = Alerter([BadSink(), good])
    alerter.dispatch(check_kill_switch(True, "x", now=1.0))
    assert alerter.failures == 1
    assert len((tmp_path / "ok.jsonl").read_text().strip().splitlines()) == 1
