"""Tests for _shared/ops/config_hot.py (H8)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json

import pytest

from _shared.ops.config_hot import (
    OpsConfigReloader,
    diff_configs,
    validate_ops_config,
)


def _write(path, cfg):
    path.write_text(json.dumps(cfg))


def _make(tmp_path, applied=None):
    cfg_path = tmp_path / "ops_config.json"
    audit_path = tmp_path / "audit" / "ops_audit.jsonl"
    seen = applied if applied is not None else []
    hot = OpsConfigReloader(
        path=cfg_path,
        audit_path=audit_path,
        on_reload=lambda cfg: seen.append(dict(cfg)),
    )
    return hot, cfg_path, audit_path, seen


def test_diff_configs_flat_and_nested():
    diff = diff_configs(
        {"a": 1, "b": {"x": 1, "y": 2}, "gone": 9},
        {"a": 2, "b": {"x": 1, "y": 3}, "new": 5},
    )
    assert diff == {
        "a": {"old": 1, "new": 2},
        "b.y": {"old": 2, "new": 3},
        "gone": {"old": 9, "new": None},
        "new": {"old": None, "new": 5},
    }
    assert diff_configs({"a": 1}, {"a": 1}) == {}


def test_validate_ops_config_accepts_known_sections():
    validate_ops_config({
        "alert_thresholds": {"drawdown_pct": 10.0},
        "kill_switch": {"max_drawdown_pct": 15.0},
        "exposure_limits": {"max_leverage": 2.0, "max_total_notional": None},
    })


def test_validate_ops_config_rejects_bad_values():
    with pytest.raises(ValueError):
        validate_ops_config({"kill_switch": {"max_drawdown_pct": -1.0}})
    with pytest.raises(ValueError):
        validate_ops_config({"alert_thresholds": {"drawdown_pct": "ten"}})
    with pytest.raises(ValueError):
        validate_ops_config({"exposure_limits": [1, 2, 3]})


def test_initial_load_and_reload_record_diff(tmp_path):
    hot, cfg_path, audit_path, seen = _make(tmp_path)
    _write(cfg_path, {"alert_thresholds": {"drawdown_pct": 10.0}})
    hot.load_initial()

    _write(cfg_path, {"alert_thresholds": {"drawdown_pct": 12.0},
                      "kill_switch": {"max_drawdown_pct": 20.0}})
    event = hot.check_once()
    assert event.applied

    log = hot.read_audit_log()
    assert len(log) == 2
    first, second = log
    assert first.source == "initial" and first.applied
    assert first.diff == {
        "alert_thresholds.drawdown_pct": {"old": None, "new": 10.0}}
    assert second.source == "file" and second.applied
    assert second.diff["alert_thresholds.drawdown_pct"] == {"old": 10.0, "new": 12.0}
    assert second.diff["kill_switch.max_drawdown_pct"] == {"old": None, "new": 20.0}
    assert seen[-1]["kill_switch"]["max_drawdown_pct"] == 20.0
    # Audit file is JSONL on disk, not only in memory.
    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) == 2 and all(json.loads(l) for l in lines)


def test_unchanged_file_is_not_audited(tmp_path):
    hot, cfg_path, _, _ = _make(tmp_path)
    _write(cfg_path, {"kill_switch": {"max_drawdown_pct": 20.0}})
    hot.load_initial()
    event = hot.check_once()
    assert not event.changed
    assert len(hot.read_audit_log()) == 1


def test_bad_config_rejected_and_audited(tmp_path):
    hot, cfg_path, _, seen = _make(tmp_path)
    good = {"kill_switch": {"max_drawdown_pct": 20.0}}
    _write(cfg_path, good)
    hot.load_initial()

    _write(cfg_path, {"kill_switch": {"max_drawdown_pct": -5.0}})
    event = hot.check_once()
    assert event.changed and not event.applied
    assert event.error is not None
    assert hot.config == good            # previous good config still active
    assert len(seen) == 1                # on_reload NOT called with bad cfg

    log = hot.read_audit_log()
    assert len(log) == 2
    assert not log[-1].applied
    assert log[-1].error
    assert log[-1].diff["kill_switch.max_drawdown_pct"] == {"old": 20.0, "new": -5.0}


def test_corrupt_json_rejected_and_audited(tmp_path):
    hot, cfg_path, _, _ = _make(tmp_path)
    _write(cfg_path, {"kill_switch": {"max_drawdown_pct": 20.0}})
    hot.load_initial()
    cfg_path.write_text("{not json")
    event = hot.check_once()
    assert event.changed and not event.applied
    log = hot.read_audit_log()
    assert not log[-1].applied and log[-1].error


def test_rollback_restores_earlier_version(tmp_path):
    hot, cfg_path, _, seen = _make(tmp_path)
    _write(cfg_path, {"alert_thresholds": {"drawdown_pct": 10.0}})
    hot.load_initial()
    _write(cfg_path, {"alert_thresholds": {"drawdown_pct": 25.0}})
    hot.check_once()
    assert hot.config["alert_thresholds"]["drawdown_pct"] == 25.0
    assert len(hot.history) == 2

    event = hot.rollback_to(index=0)
    assert event.applied
    assert hot.config["alert_thresholds"]["drawdown_pct"] == 10.0
    assert seen[-1]["alert_thresholds"]["drawdown_pct"] == 10.0
    # File on disk matches the restored config.
    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["alert_thresholds"]["drawdown_pct"] == 10.0
    # Rollback itself is audited and appended to history.
    log = hot.read_audit_log()
    assert log[-1].source == "rollback" and log[-1].applied
    assert log[-1].diff["alert_thresholds.drawdown_pct"] == {"old": 25.0, "new": 10.0}
    assert len(hot.history) == 3
    assert hot.history[-1].source == "rollback"


def test_rollback_by_timestamp(tmp_path):
    hot, cfg_path, _, _ = _make(tmp_path)
    _write(cfg_path, {"kill_switch": {"max_drawdown_pct": 20.0}})
    hot.load_initial()
    _write(cfg_path, {"kill_switch": {"max_drawdown_pct": 30.0}})
    hot.check_once()
    first_ts = hot.history[0].ts
    event = hot.rollback_to(ts=first_ts)
    assert event.applied
    assert hot.config["kill_switch"]["max_drawdown_pct"] == 20.0


def test_rollback_errors_when_no_such_version(tmp_path):
    hot, cfg_path, _, _ = _make(tmp_path)
    _write(cfg_path, {"kill_switch": {"max_drawdown_pct": 20.0}})
    with pytest.raises(ValueError):
        hot.rollback_to(index=0)          # nothing applied yet
    hot.load_initial()
    with pytest.raises(ValueError):
        hot.rollback_to(index=99)
    with pytest.raises(ValueError):
        hot.rollback_to(ts=12345.0)
