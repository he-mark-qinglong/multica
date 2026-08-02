"""Tests for _shared/ops/remote_control.py (P3) — real HTTP on ephemeral port."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json
import urllib.error
import urllib.request

import pytest

from _shared.ops.audit_trail import TransitionKind, load_trail, query_trail
from _shared.ops.remote_control import (
    RemoteControlServer,
    control_state,
    is_killed,
    is_paused,
)


@pytest.fixture()
def server(tmp_path):
    srv = RemoteControlServer(
        control_dir=tmp_path / "control",
        audit_path=tmp_path / "audit.jsonl",
        port=0,  # ephemeral
        clock=lambda: 1000.0,
    )
    srv.serve_in_thread()
    yield srv, tmp_path
    srv.shutdown()


def _url(srv, path):
    host, port = srv.address
    return f"http://{host}:{port}{path}"


def _get(srv, path):
    with urllib.request.urlopen(_url(srv, path), timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def _post(srv, path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        _url(srv, path), data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_status_reports_default_state_and_audits_manual(server):
    srv, tmp = server
    code, payload = _get(srv, "/status")
    assert code == 200
    assert payload["paused"] is False and payload["killed"] is False
    assert payload["ts"] == 1000.0

    trail = load_trail(tmp / "audit.jsonl")
    assert len(trail) == 1
    rec = trail[0]
    assert rec.kind == TransitionKind.STATUS.value
    assert rec.actor == "manual"


def test_pause_latches_flag_and_audits(server):
    srv, tmp = server
    code, payload = _post(srv, "/pause", {"reason": "ops investigate"})
    assert code == 200 and payload["paused"] is True
    assert is_paused(tmp / "control")

    # explicit unpause clears the flag
    code, payload = _post(srv, "/pause", {"paused": False})
    assert code == 200 and payload["paused"] is False
    assert not is_paused(tmp / "control")

    trail = load_trail(tmp / "audit.jsonl")
    assert all(r.actor == "manual" for r in trail)
    changes = query_trail(trail, kind=TransitionKind.CONFIG_CHANGE.value)
    assert len(changes) == 2
    assert changes[0].before == {"paused": False} and changes[0].after == {"paused": True}
    assert changes[1].before == {"paused": True} and changes[1].after == {"paused": False}


def test_kill_latches_and_audits_kill_kind(server):
    srv, tmp = server
    code, payload = _post(srv, "/kill", {"reason": "drawdown breach"})
    assert code == 200 and payload["killed"] is True
    assert is_killed(tmp / "control")

    # second kill stays latched; first reason wins
    _post(srv, "/kill", {"reason": "second"})
    state = control_state(tmp / "control")
    assert state["kill"]["reason"] == "drawdown breach"

    kills = query_trail(load_trail(tmp / "audit.jsonl"), kind=TransitionKind.KILL.value)
    assert len(kills) == 2 and all(r.actor == "manual" for r in kills)
    assert kills[0].note == "drawdown breach"


def test_reload_config_409_without_reloader(server):
    srv, _ = server
    code, payload = _post(srv, "/reload_config")
    assert code == 409 and payload["ok"] is False


def test_reload_config_calls_reloader_and_audits(tmp_path):
    calls = []

    def fake_reload():
        calls.append(1)
        return {"applied": True, "version": 3}

    srv = RemoteControlServer(
        control_dir=tmp_path / "control",
        audit_path=tmp_path / "audit.jsonl",
        port=0,
        reload_config=fake_reload,
    )
    srv.serve_in_thread()
    try:
        code, payload = _post(srv, "/reload_config")
    finally:
        srv.shutdown()
    assert code == 200 and payload["reload"] == {"applied": True, "version": 3}
    assert len(calls) == 1
    trail = load_trail(tmp_path / "audit.jsonl")
    assert trail[0].actor == "manual"
    assert trail[0].kind == TransitionKind.CONFIG_CHANGE.value


def test_unknown_path_404_and_get_on_action_404(server):
    srv, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(srv, "/nope")
    assert exc.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(srv, "/kill")  # actions are POST-only
    assert exc.value.code == 404
