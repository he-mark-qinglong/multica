"""Tests for _shared/ops/risk_dashboard.py (D19)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json

from _shared.ops.alerting import Alert, AlertLevel
from _shared.ops.heartbeat import write_beat
from _shared.ops.risk_dashboard import (
    DashboardState,
    evaluate_traffic_light,
    load_state_from_dir,
    render_dashboard,
    watch_loop,
)
from _shared.market_making.tail_risk import compute_tail_risk
from _shared.portfolio.exposure import Position


def _state(**kw):
    base = dict(
        ts=1_700_000_000.0,
        equity=100_000.0,
        positions=(Position("BTC", 0.5, 60_000.0), Position("ETH", -2.0, 3_000.0)),
        pnl_history_bp=tuple([5.0, -8.0, 3.0, -2.0, 10.0] * 20),
    )
    base.update(kw)
    return DashboardState(**base)


def test_render_contains_key_fields():
    page = render_dashboard(_state())
    assert "risk dashboard" in page
    assert "BTC" in page and "ETH" in page
    assert "100,000.00" in page                      # equity
    assert "VaR 95%" in page and "CVaR 99%" in page  # tail-risk table
    assert "Exposure" in page and "Active alerts" in page
    assert "Heartbeat" in page
    assert "snapshot" in page


def test_render_has_meta_refresh_and_no_javascript():
    page = render_dashboard(_state(refresh_sec=7))
    assert '<meta http-equiv="refresh" content="7">' in page
    lowered = page.lower()
    assert "<script" not in lowered
    assert "javascript:" not in lowered


def test_traffic_light_green_when_quiet():
    state = _state()
    light = evaluate_traffic_light(state, compute_tail_risk(state.pnl_history_bp))
    assert light.level == "GREEN"
    assert light.reasons == ()
    assert 'class="light GREEN"' in render_dashboard(state)


def test_traffic_light_red_on_critical_alert_and_var_breach():
    crit = Alert(ts=1.0, level=AlertLevel.CRITICAL.value, rule="drawdown",
                 message="dd 12% exceeds threshold 10%")
    state = _state(alerts=(crit,), var_limit_bp=1.0)  # VaR95 of history >> 1bp
    light = evaluate_traffic_light(state, compute_tail_risk(state.pnl_history_bp))
    assert light.level == "RED"
    assert any("critical alert" in r for r in light.reasons)
    assert any("VaR95" in r for r in light.reasons)
    assert 'class="light RED"' in render_dashboard(state)


def test_traffic_light_yellow_on_warn_alert():
    warn = Alert(ts=1.0, level=AlertLevel.WARN.value, rule="latency",
                 message="p99 latency elevated")
    state = _state(alerts=(warn,))
    light = evaluate_traffic_light(state, compute_tail_risk(state.pnl_history_bp))
    assert light.level == "YELLOW"
    assert light.reasons == ("warn alert: latency",)
    assert 'class="light YELLOW"' in render_dashboard(state)


def test_alert_messages_are_html_escaped():
    evil = Alert(ts=1.0, level=AlertLevel.WARN.value, rule="xss",
                 message='<img src=x onerror="alert(1)">')
    page = render_dashboard(_state(alerts=(evil,)))
    assert "<img src=x" not in page
    assert "&lt;img src=x" in page


def test_load_state_from_dir_roundtrip(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({
        "ts": 123.0,
        "equity": 50_000.0,
        "positions": [{"symbol": "BTC", "qty": 0.1, "price": 60_000.0}],
        "pnl_history_bp": [1.0, -2.0, 3.0],
        "alerts": [{"ts": 1.0, "level": "WARN", "rule": "r", "message": "m"}],
        "var_limit_bp": 500.0,
    }))
    write_beat(tmp_path / "beat.json", state="running", ts=100.0)
    state = load_state_from_dir(tmp_path, beat_timeout_sec=30.0, now=110.0)
    assert state.ts == 123.0
    assert state.equity == 50_000.0
    assert state.positions[0].symbol == "BTC"
    assert len(state.alerts) == 1 and state.alerts[0].level == "WARN"
    assert state.var_limit_bp == 500.0
    assert state.heartbeat is not None and state.heartbeat.alive


def test_load_state_from_dir_missing_files_degrades_gracefully(tmp_path):
    state = load_state_from_dir(tmp_path, now=1.0)
    assert state.positions == ()
    assert state.alerts == ()
    assert state.heartbeat is not None and not state.heartbeat.alive
    # Still renders a full page.
    assert "risk dashboard" in render_dashboard(state)


def test_watch_loop_writes_html_file(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(json.dumps(
        {"equity": 10_000.0, "positions": []}))
    out = tmp_path / "dash" / "index.html"
    n = watch_loop(state_dir, out, interval_sec=0.01, max_iterations=2)
    assert n == 2
    page = out.read_text()
    assert "risk dashboard" in page
    assert "10,000.00" in page
    assert not out.with_suffix(".html.tmp").exists()  # atomic rename cleaned up
