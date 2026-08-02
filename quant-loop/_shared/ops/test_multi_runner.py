"""Tests for _shared/ops/multi_runner.py (H9)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json
import sys as _sys
import time

import pytest

from _shared.ops.alerting import AlertLevel, Alerter, LogFileSink
from _shared.ops.heartbeat import write_beat
from _shared.ops.multi_runner import (
    SIGNAL_BUS_ENV,
    MultiRunner,
    StrategySpec,
    build_child_env,
    dead_process_alert,
)


def _spec(name, tmp_path, argv=None, env=None):
    if argv is None:
        argv = (_sys.executable, "-c", "import time; time.sleep(30)")
    return StrategySpec(
        name=name,
        argv=argv,
        heartbeat_path=tmp_path / "hb" / f"{name}.json",
        env=env or {},
    )


def test_spec_validation(tmp_path):
    with pytest.raises(ValueError):
        StrategySpec(name="", argv=("x",), heartbeat_path=tmp_path / "h")
    with pytest.raises(ValueError):
        StrategySpec(name="a", argv=(), heartbeat_path=tmp_path / "h")


def test_build_child_env_injects_bus_path_and_spec_env(tmp_path):
    spec = _spec("mm", tmp_path, env={"FOO": "bar"})
    env = build_child_env(spec, tmp_path / "bus.jsonl", base={"BASE": "1"})
    assert env["BASE"] == "1"
    assert env["FOO"] == "bar"
    assert env[SIGNAL_BUS_ENV] == str(tmp_path / "bus.jsonl")


def test_duplicate_names_rejected(tmp_path):
    specs = [_spec("dup", tmp_path), _spec("dup", tmp_path)]
    with pytest.raises(ValueError, match="duplicate"):
        MultiRunner(specs, tmp_path / "bus.jsonl")


def test_children_share_signal_bus_path(tmp_path):
    # Child copies the bus path from its env into a file we can assert on.
    out = tmp_path / "child_out.json"
    argv = (
        _sys.executable, "-c",
        "import json, os, sys;"
        "json.dump({'bus': os.environ['" + SIGNAL_BUS_ENV + "']}, open(sys.argv[1], 'w'))",
        str(out),
    )
    specs = [_spec("mm", tmp_path, argv=argv)]
    runner = MultiRunner(specs, tmp_path / "shared" / "bus.jsonl")
    runner.start_all()
    for _ in range(100):  # let the short-lived child finish on its own
        if out.exists():
            break
        time.sleep(0.05)
    runner.stop_all(grace_sec=5.0)
    assert json.loads(out.read_text())["bus"] == str(tmp_path / "shared" / "bus.jsonl")


def test_poll_aggregates_heartbeats_and_routes_alerts(tmp_path):
    specs = [_spec("alive", tmp_path), _spec("stale", tmp_path)]
    # 'alive' beats now, 'stale' beat long ago (simulating a wedged child
    # without waiting 60s of wall time).
    now = time.time()
    write_beat(specs[0].heartbeat_path, ts=now)
    write_beat(specs[1].heartbeat_path, ts=now - 3600.0)

    alerts_log = tmp_path / "alerts.jsonl"
    alerter = Alerter(sinks=[LogFileSink(alerts_log)])
    runner = MultiRunner(
        specs, tmp_path / "bus.jsonl",
        alerter=alerter, heartbeat_timeout_sec=60.0,
    )
    result = runner.poll(now=now)

    assert result.statuses["alive"].alive
    assert not result.statuses["stale"].alive
    assert len(result.alerts) == 1
    assert result.alerts[0].level == AlertLevel.CRITICAL.value
    assert "stale" in result.alerts[0].message
    # Routed to the sink:
    logged = [json.loads(l) for l in alerts_log.read_text().splitlines()]
    assert logged[0]["rule"] == "heartbeat_timeout"


def test_unexpected_exit_alerts_once(tmp_path):
    argv = (_sys.executable, "-c", "import sys; sys.exit(3)")
    specs = [_spec("crasher", tmp_path, argv=argv)]
    runner = MultiRunner(specs, tmp_path / "bus.jsonl")
    runner.start_all()
    rc = None
    for _ in range(100):
        result = runner.poll()
        if "crasher" in result.returncodes:
            rc = result.returncodes["crasher"]
            break
        time.sleep(0.05)
    assert rc == 3
    exit_alerts = [a for a in result.alerts if a.rule == "process_exit"]
    assert len(exit_alerts) == 1
    assert exit_alerts[0].context["returncode"] == 3
    # Second poll: still knows the rc, but does not re-alert the exit.
    again = runner.poll()
    assert again.returncodes["crasher"] == 3
    assert not [a for a in again.alerts if a.rule == "process_exit"]


def test_start_all_then_stop_all_terminates_children(tmp_path):
    specs = [_spec("a", tmp_path), _spec("b", tmp_path)]
    runner = MultiRunner(specs, tmp_path / "bus.jsonl")
    runner.start_all()
    procs = runner.processes
    assert set(procs) == {"a", "b"}
    assert all(p.poll() is None for p in procs.values())
    rcs = runner.stop_all(grace_sec=5.0)
    assert all(rc != 0 or rc is not None for rc in rcs.values())
    assert all(p.poll() is not None for p in procs.values())


def test_dead_process_alert_shape():
    alert = dead_process_alert("mm_btc", 137, now=1000.0)
    assert alert.ts == 1000.0
    assert alert.level == AlertLevel.CRITICAL.value
    assert "mm_btc" in alert.message
    assert "137" in alert.message
