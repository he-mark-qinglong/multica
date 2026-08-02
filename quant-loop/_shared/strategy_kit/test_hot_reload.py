import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json
import os
import time

import pytest

from _shared.strategy_kit.hot_reload import ConfigReloader


def _write(path, cfg):
    path.write_text(json.dumps(cfg), encoding="utf-8")
    # Ensure mtime differs between rapid successive writes.
    os.utime(path, ns=(time.time_ns(), time.time_ns()))


@pytest.fixture()
def cfg_path(tmp_path):
    return tmp_path / "config.json"


def test_initial_load_and_no_change(cfg_path):
    _write(cfg_path, {"risk": 0.01, "spread": 2})
    seen = []
    r = ConfigReloader(cfg_path, on_reload=seen.append)
    cfg = r.load_initial()
    assert cfg == {"risk": 0.01, "spread": 2}
    assert seen == [cfg]
    ev = r.check_once()
    assert not ev.changed and not ev.applied and ev.error is None


def test_reload_on_change(cfg_path):
    _write(cfg_path, {"risk": 0.01})
    seen = []
    r = ConfigReloader(cfg_path, on_reload=seen.append)
    r.load_initial()
    time.sleep(0.01)
    _write(cfg_path, {"risk": 0.02})
    ev = r.check_once()
    assert ev.changed and ev.applied
    assert r.config == {"risk": 0.02}
    assert seen[-1] == {"risk": 0.02}


def test_bad_json_rolls_back_to_previous(cfg_path):
    _write(cfg_path, {"risk": 0.01})
    seen = []
    r = ConfigReloader(cfg_path, on_reload=seen.append)
    r.load_initial()
    time.sleep(0.01)
    cfg_path.write_text("{not json", encoding="utf-8")
    ev = r.check_once()
    assert ev.changed and not ev.applied and ev.error
    # Previous good config still active; callback not invoked again.
    assert r.config == {"risk": 0.01}
    assert seen == [{"risk": 0.01}]
    # A later valid edit is picked up (failed mtime was recorded, no spam).
    time.sleep(0.01)
    _write(cfg_path, {"risk": 0.03})
    ev2 = r.check_once()
    assert ev2.applied and r.config == {"risk": 0.03}


def test_validator_rejection_rolls_back(cfg_path):
    _write(cfg_path, {"risk": 0.01})
    seen = []

    def validate(cfg):
        if cfg.get("risk", 0) <= 0 or cfg["risk"] > 0.5:
            raise ValueError("risk out of bounds")

    r = ConfigReloader(cfg_path, on_reload=seen.append, validator=validate)
    r.load_initial()
    time.sleep(0.01)
    _write(cfg_path, {"risk": 5.0})
    ev = r.check_once()
    assert not ev.applied and "risk out of bounds" in ev.error
    assert r.config == {"risk": 0.01}
    assert seen == [{"risk": 0.01}]


def test_callback_exception_rolls_back(cfg_path):
    _write(cfg_path, {"v": 1})
    calls = []

    def on_reload(cfg):
        calls.append(cfg)
        if cfg["v"] == 2:
            raise RuntimeError("strategy rejected params")

    r = ConfigReloader(cfg_path, on_reload=on_reload)
    r.load_initial()
    time.sleep(0.01)
    _write(cfg_path, {"v": 2})
    ev = r.check_once()
    assert not ev.applied and "strategy rejected" in ev.error
    assert r.config == {"v": 1}  # swap never happened


def test_bad_initial_config_raises(cfg_path):
    cfg_path.write_text("[1,2,3]", encoding="utf-8")  # not an object
    r = ConfigReloader(cfg_path, on_reload=lambda c: None)
    with pytest.raises(ValueError, match="JSON object"):
        r.load_initial()


def test_missing_file_reported_not_fatal(cfg_path):
    r = ConfigReloader(cfg_path, on_reload=lambda c: None)
    ev = r.check_once()
    assert not ev.changed and not ev.applied
    assert "missing" in ev.error


def test_first_load_via_check_once(cfg_path):
    _write(cfg_path, {"a": 1})
    r = ConfigReloader(cfg_path, on_reload=lambda c: None)
    ev = r.check_once()
    assert ev.changed and ev.applied and r.config == {"a": 1}
