"""Tests for _shared/ops/heartbeat.py (H14, H15)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json

from _shared.ops.alerting import AlertLevel
from _shared.ops.heartbeat import (
    HeartbeatWatcher,
    check_heartbeat,
    heartbeat_alert,
    read_beat,
    write_beat,
)


def test_write_and_read_beat_roundtrip(tmp_path):
    beat = tmp_path / "hb" / "beat.json"
    write_beat(beat, state="quoting", ts=1000.0, extra={"pid": 42})
    payload = read_beat(beat)
    assert payload["ts"] == 1000.0
    assert payload["state"] == "quoting"
    assert payload["pid"] == 42


def test_check_heartbeat_alive_within_timeout(tmp_path):
    beat = tmp_path / "beat.json"
    write_beat(beat, ts=1000.0)
    status = check_heartbeat(beat, timeout_sec=60.0, now=1030.0)
    assert status.alive
    assert status.age_sec == 30.0
    assert status.last_ts == 1000.0


def test_check_heartbeat_stale_past_timeout(tmp_path):
    beat = tmp_path / "beat.json"
    write_beat(beat, ts=1000.0)
    status = check_heartbeat(beat, timeout_sec=60.0, now=1061.0)
    assert not status.alive
    assert status.age_sec == 61.0


def test_missing_or_corrupt_beat_is_not_alive(tmp_path):
    missing = tmp_path / "nope.json"
    status = check_heartbeat(missing, timeout_sec=60.0, now=1.0)
    assert not status.alive
    assert status.state == "missing"
    assert status.last_ts is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert not check_heartbeat(corrupt, 60.0, now=1.0).alive


def test_heartbeat_alert_critical_on_stale(tmp_path):
    beat = tmp_path / "beat.json"
    write_beat(beat, ts=100.0)
    status = check_heartbeat(beat, timeout_sec=10.0, now=200.0)
    alert = heartbeat_alert(status, process="mm_btc", now=200.0)
    assert alert is not None
    assert alert.level == AlertLevel.CRITICAL.value
    assert alert.rule == "heartbeat_timeout"
    assert "mm_btc" in alert.message


def test_heartbeat_alert_none_when_alive(tmp_path):
    beat = tmp_path / "beat.json"
    write_beat(beat, ts=100.0)
    status = check_heartbeat(beat, timeout_sec=10.0, now=105.0)
    assert heartbeat_alert(status) is None


def test_watcher_combines_check_and_alert(tmp_path):
    beat = tmp_path / "beat.json"
    write_beat(beat, ts=100.0)
    watcher = HeartbeatWatcher(path=beat, timeout_sec=10.0, process="mm_btc")
    assert watcher.check(now=105.0) is None
    alert = watcher.check(now=500.0)
    assert alert is not None
    assert alert.context["process"] == "mm_btc"


def test_beat_file_is_valid_single_json_object(tmp_path):
    beat = tmp_path / "beat.json"
    write_beat(beat, state="running", ts=1.0)
    # No .tmp leftover; file parses as one object.
    assert json.loads(beat.read_text())["state"] == "running"
    assert not (tmp_path / "beat.json.tmp").exists()
