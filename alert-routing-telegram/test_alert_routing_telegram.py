#!/usr/bin/env python3
"""Smoke test for alert-routing-telegram.

Validates:
  1. The router script exists and is non-trivial.
  2. Importing run.py does not raise.
  3. Required callables are present (synthesize_alerts, format_telegram_message,
     filter_fresh, parse_uptime_seconds, main).
  4. _alert_id is deterministic.
  5. parse_uptime_seconds handles common formats.
  6. format_telegram_message produces a Markdown payload with all 3 sections.
  7. synthesize_alerts handles synthetic healthy / warn / critical inputs.
  8. filter_fresh + dedup correctly suppresses within window.
  9. telegram_send fails gracefully when network is unavailable (no panic).
 10. End-to-end run.py exits 0 and updates last-snapshot.json.

Exit non-zero on first failure with a descriptive message.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

ALERT_DIR = Path("/Users/mark/multica/alert-routing-telegram")
RUN_PY = ALERT_DIR / "run.py"
LAST = ALERT_DIR / "last-snapshot.json"


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def load_module():
    spec = importlib.util.spec_from_file_location("alert_routing_telegram", RUN_PY)
    if spec is None or spec.loader is None:
        fail("could not load run.py as module")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        fail(f"run.py raised on import: {e!r}")
    return mod


def test_router_file_present():
    if not RUN_PY.exists():
        fail(f"run.py missing: {RUN_PY}")
    if not RUN_PY.stat().st_size > 5000:
        fail(f"run.py suspiciously small: {RUN_PY.stat().st_size} bytes")
    ok(f"run.py present, {RUN_PY.stat().st_size} bytes")


def test_runpy_imports():
    mod = load_module()
    for name in ("synthesize_alerts", "format_telegram_message", "filter_fresh",
                 "parse_uptime_seconds", "main", "telegram_send", "_alert_id"):
        if not hasattr(mod, name):
            fail(f"run.py missing callable: {name}")
    ok("run.py imports & exposes all required functions")


def test_alert_id_deterministic():
    mod = load_module()
    a1 = mod._alert_id("foo", "bar", "baz")
    a2 = mod._alert_id("foo", "bar", "baz")
    a3 = mod._alert_id("foo", "bar", "qux")
    if a1 != a2:
        fail(f"_alert_id not deterministic: {a1} != {a2}")
    if a1 == a3:
        fail(f"_alert_id collision for different inputs: {a1}")
    if len(a1) != 12:
        fail(f"_alert_id expected length 12 hex, got {len(a1)}: {a1!r}")
    ok(f"_alert_id stable: {a1} (len 12)")


def test_parse_uptime():
    mod = load_module()
    cases = {
        "15h17m41s": 15 * 3600 + 17 * 60 + 41,
        "2d": 2 * 86400,
        "45s": 45,
        "1d2h3m4s": 86400 + 2 * 3600 + 3 * 60 + 4,
        "0s": 0,
        "": -1,
        "garbage": -1,
    }
    for raw, expected in cases.items():
        got = mod.parse_uptime_seconds(raw)
        if got != expected:
            fail(f"uptime parse: {raw!r} -> {got}, expected {expected}")
    ok(f"parse_uptime_seconds: {len(cases)} cases pass")


def test_format_telegram_message():
    mod = load_module()
    alert = {
        "id": "abc123def456",
        "source": "daemon",
        "severity": mod.SEVERITY_CRITICAL,
        "summary": "multica daemon unreachable",
        "detail": ["stderr line 1", "stderr line 2"],
        "ts_utc": "2026-07-26T05:00:00+00:00",
    }
    payload = mod.format_telegram_message(alert)
    for k in ("text", "parse_mode", "_meta"):
        if k not in payload:
            fail(f"payload missing key: {k}")
    if payload["parse_mode"] != "Markdown":
        fail(f"payload parse_mode: {payload['parse_mode']}")
    for token in ("CRITICAL", "daemon", "abc123def456"):
        if token not in payload["text"]:
            fail(f"payload text missing token: {token}")
    ok("format_telegram_message: text/parse_mode/_meta all present")


def test_synthesize_alerts_healthy():
    mod = load_module()
    sources = {
        "status_page": {
            "verdict": "healthy",
            "ts_utc": "2026-07-26T05:00:00+00:00",
            "warnings": [],
        },
        "db_pool": None,
    }
    alerts = mod.synthesize_alerts(
        sources,
        daemon={"alive": True, "uptime": "1h"},
        ap={"total": 27, "paused": 0, "paused_pct": 0.0},
        backlog={"in_progress": 100, "blocked": 5},
    )
    if not alerts:
        fail("expected at least one healthy alert from status-page")
    if any(a["severity"] in (mod.SEVERITY_WARNING, mod.SEVERITY_CRITICAL) for a in alerts):
        fail(f"unexpected escalation in healthy scenario: {[a['severity'] for a in alerts]}")
    ok(f"synthesize_alerts(healthy): {len(alerts)} alert(s), max severity={alerts[-1]['severity']}")


def test_synthesize_alerts_critical():
    mod = load_module()
    sources = {
        "status_page": {
            "verdict": "escalate",
            "ts_utc": "2026-07-26T05:00:00+00:00",
            "escalations": ["autopilot missed"],
        },
        "db_pool": {
            "verdict": "critical",
            "ts_utc": "2026-07-26T05:00:00+00:00",
            "alerts": ["pool exhausted"],
        },
    }
    alerts = mod.synthesize_alerts(
        sources,
        daemon={"alive": False, "stderr": "connection refused"},
        ap={"total": 27, "paused": 20, "paused_pct": 74.0},
        backlog={"in_progress": 999, "blocked": 200},
    )
    severities = sorted({a["severity"] for a in alerts})
    if mod.SEVERITY_CRITICAL not in severities:
        fail(f"expected at least one CRITICAL alert, got severities={severities}")
    if len(alerts) < 3:
        fail(f"expected >=3 critical sources, got {len(alerts)} alerts")
    ok(f"synthesize_alerts(critical): {len(alerts)} alerts, severities={severities}")


def test_dedup_filter():
    mod = load_module()
    now = datetime.now(timezone.utc)
    alert_id = mod._alert_id("daemon", "down")
    dedup = {"seen": {alert_id: now.isoformat()}}
    alerts = [{
        "id": alert_id, "source": "daemon", "severity": mod.SEVERITY_CRITICAL,
        "summary": "x", "detail": [], "ts_utc": now.isoformat(),
    }]
    fresh, suppressed = mod.filter_fresh(alerts, dedup, window_min=30)
    if fresh:
        fail(f"expected fresh=[], got {len(fresh)}")
    if len(suppressed) != 1:
        fail(f"expected suppressed=1, got {len(suppressed)}")
    # Now test window expiry
    old = (now - timedelta(minutes=120)).isoformat()
    dedup = {"seen": {alert_id: old}}
    fresh, suppressed = mod.filter_fresh(alerts, dedup, window_min=30)
    if not fresh or suppressed:
        fail(f"expected alert outside window to be fresh; fresh={len(fresh)}, suppressed={len(suppressed)}")
    ok(f"filter_fresh: in-window suppressed, expired fresh (window_min=30)")


def test_telegram_send_handles_bad_url():
    mod = load_module()
    # Use a token that's syntactically valid but unreachable host (forces HTTPError).
    res = mod.telegram_send("0000000000:AA_notreal_notreal_notreal", "0", {
        "text": "test", "parse_mode": "Markdown",
    }, timeout=3)
    if not isinstance(res, dict):
        fail(f"telegram_send returned non-dict: {type(res)}")
    for k in ("ok", "http_code", "body"):
        if k not in res:
            fail(f"telegram_send result missing key: {k}")
    ok(f"telegram_send: returns ok={res['ok']}, http_code={res['http_code']} (no panic)")


def test_end_to_end():
    """Run run.py once; require exit 0 + updated last-snapshot.json."""
    before = LAST.read_text() if LAST.exists() else ""
    proc = subprocess.run(
        ["python3", str(RUN_PY), "--dedup-window", "30"],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        fail(f"run.py rc={proc.returncode}, stderr={proc.stderr[:500]}")
    after = LAST.read_text()
    if not after or after == before:
        fail("run.py did not update last-snapshot.json")
    snap = json.loads(after)
    ts = datetime.fromisoformat(snap["ts_utc"])
    age_sec = (datetime.now(timezone.utc) - ts).total_seconds()
    if age_sec > 120:
        fail(f"last-snapshot.json is stale by {age_sec:.0f}s (expected <120s)")
    for k in ("ts_utc", "severity_counts", "fresh_alert_count",
              "raw_alert_count", "sources_present", "thresholds"):
        if k not in snap:
            fail(f"last-snapshot.json missing key: {k}")
    ok(f"run.py end-to-end pass; snapshot age {age_sec:.1f}s, "
       f"raw={snap['raw_alert_count']}, fresh={snap['fresh_alert_count']}")


def main():
    print("== alert-routing-telegram smoke tests ==")
    print(f"== run.py:    {RUN_PY}")
    print(f"== last snap: {LAST}")
    test_router_file_present()
    test_runpy_imports()
    test_alert_id_deterministic()
    test_parse_uptime()
    test_format_telegram_message()
    test_synthesize_alerts_healthy()
    test_synthesize_alerts_critical()
    test_dedup_filter()
    test_telegram_send_handles_bad_url()
    test_end_to_end()
    print("[PASS] all checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())