"""Tests for _shared/ops/structured_log.py (H6)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import io
import json

import pytest

from _shared.ops.structured_log import (
    EventType,
    JsonLogger,
    LogLevel,
    format_record,
    make_record,
)


def test_record_is_one_json_line_with_required_keys():
    rec = make_record(EventType.ORDER_FILLED, LogLevel.INFO,
                      {"symbol": "BTCUSDT", "qty": 0.1}, ts=1700000000.0)
    line = format_record(rec)
    assert "\n" not in line
    obj = json.loads(line)
    assert obj["ts"] == 1700000000.0
    assert obj["level"] == "INFO"
    assert obj["event"] == "order_filled"
    assert obj["data"]["symbol"] == "BTCUSDT"


def test_unknown_event_type_rejected():
    with pytest.raises(ValueError):
        make_record("not_a_real_event")


def test_all_event_types_are_valid_strings():
    for ev in EventType:
        rec = make_record(ev)
        assert json.loads(format_record(rec))["event"] == ev.value


def test_logger_writes_to_stream():
    buf = io.StringIO()
    logger = JsonLogger(buf)
    logger.log(EventType.STARTUP, data={"version": "abc123"})
    logger.log(EventType.KILL_SWITCH_TRIGGERED, LogLevel.CRITICAL, {"rule": "pf"})
    lines = buf.getvalue().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["level"] == "CRITICAL"


def test_logger_writes_to_file(tmp_path):
    path = tmp_path / "logs" / "events.jsonl"
    with JsonLogger(path) as logger:
        logger.log(EventType.SHUTDOWN, data={"reason": "drain"})
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "shutdown"
    assert json.loads(lines[0])["data"]["reason"] == "drain"


def test_non_json_serializable_data_falls_back_to_str():
    rec = make_record(EventType.ERROR, data={"exc": ValueError("boom")})
    obj = json.loads(format_record(rec))
    assert "boom" in obj["data"]["exc"]
