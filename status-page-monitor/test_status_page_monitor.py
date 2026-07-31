#!/usr/bin/env python3
"""Smoke test for status-page-monitor.

Validates:
  1. The monitor script exists and is executable.
  2. Importing run.py does not raise (catches top-level syntax errors).
  3. `multica daemon status --output json` shape is what run.py expects (key fields).
  4. `multica autopilot list` returns at least one autopilot (sanity).
  5. `multica issue list --status <s>` returns `total` field for known statuses.
  6. parse_uptime_to_seconds: tested for several formats.
  7. last-snapshot.json schema after a real run has required keys.

Run: python3 /Users/mark/multica/status-page-monitor/test_status_page_monitor.py
Exits non-zero on first failure with a descriptive message.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MONITOR_DIR = Path("/Users/mark/multica/status-page-monitor")
RUN_PY = MONITOR_DIR / "run.py"
LAST = MONITOR_DIR / "last-snapshot.json"


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def test_monitor_file_present() -> None:
    if not RUN_PY.exists():
        fail(f"run.py missing: {RUN_PY}")
    if not RUN_PY.stat().st_size > 1000:
        fail(f"run.py suspiciously small: {RUN_PY.stat().st_size} bytes")
    ok(f"run.py present, {RUN_PY.stat().st_size} bytes")


def test_runpy_imports() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("status_page_monitor", RUN_PY)
    if spec is None or spec.loader is None:
        fail("could not load run.py as module")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        fail(f"run.py raised on import: {e!r}")
    # Verify required callables
    for name in ("probe_daemon", "probe_autopilots", "probe_issue_counts",
                 "probe_stalled_in_progress", "parse_uptime_to_seconds",
                 "main"):
        if not hasattr(mod, name):
            fail(f"run.py missing callable: {name}")
    ok("run.py imports & exposes all required functions")


def test_parse_uptime() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("status_page_monitor", RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cases = {
        "15h17m41s": 15 * 3600 + 17 * 60 + 41,
        "2d": 2 * 86400,
        "45s": 45,
        "1d2h3m4s": 86400 + 2 * 3600 + 3 * 60 + 4,
        "0s": 0,
    }
    for raw, expected in cases.items():
        got = mod.parse_uptime_to_seconds(raw)
        if got != expected:
            fail(f"uptime parse: {raw!r} -> {got}, expected {expected}")
    ok(f"parse_uptime_to_seconds: {len(cases)} cases pass")


def test_daemon_json_shape() -> None:
    proc = subprocess.run(
        ["multica", "daemon", "status", "--output", "json"],
        capture_output=True, text=True, timeout=20,
    )
    if proc.returncode != 0:
        fail(f"`multica daemon status` rc={proc.returncode}, stderr={proc.stderr[:200]}")
    try:
        d = json.loads(proc.stdout)
    except Exception as e:
        fail(f"daemon status JSON parse error: {e!r}")
    for k in ("status", "pid", "uptime", "agents", "active_task_count"):
        if k not in d:
            fail(f"daemon JSON missing key: {k}")
    ok(f"daemon JSON has required keys (status={d['status']}, pid={d['pid']})")


def test_autopilot_list_nonempty() -> None:
    proc = subprocess.run(
        ["multica", "autopilot", "list", "--output", "json"],
        capture_output=True, text=True, timeout=20,
    )
    if proc.returncode != 0:
        fail(f"`multica autopilot list` rc={proc.returncode}, stderr={proc.stderr[:200]}")
    d = json.loads(proc.stdout)
    n = len(d.get("autopilots", []))
    if n < 1:
        fail(f"autopilot list empty (n={n})")
    ok(f"autopilot list returns {n} autopilots")


def test_issue_total_field() -> None:
    """Ensure `multica issue list --status <s> --limit 1` returns `total`."""
    for s in ("in_progress", "blocked", "done"):
        proc = subprocess.run(
            ["multica", "issue", "list", "--status", s, "--limit", "1", "--output", "json"],
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode != 0:
            fail(f"issue list --status {s} rc={proc.returncode}")
        d = json.loads(proc.stdout)
        if "total" not in d:
            fail(f"issue list --status {s} missing 'total' field; keys={list(d.keys())}")
        ok(f"issue list --status {s}: total={d['total']}")


def test_last_snapshot_schema() -> None:
    if not LAST.exists():
        fail(f"last-snapshot.json missing; run.py must run before this test")
    d = json.loads(LAST.read_text())
    for k in ("ts_utc", "verdict", "daemon", "autopilots", "issue_counts"):
        if k not in d:
            fail(f"last-snapshot.json missing key: {k}")
    if d["verdict"] not in ("healthy", "warn", "escalate", "no-op"):
        fail(f"last-snapshot verdict unexpected: {d['verdict']!r}")
    ok(f"last-snapshot.json verdict={d['verdict']}")


def test_monitor_end_to_end() -> None:
    """Run run.py; require exit code 0 and that last-snapshot.json updates."""
    before = LAST.read_text() if LAST.exists() else ""
    proc = subprocess.run(
        ["python3", str(RUN_PY)],
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
    ok(f"run.py end-to-end pass; snapshot age {age_sec:.1f}s, verdict={snap['verdict']}")


def main() -> int:
    print(f"== Status-Page-Monitor smoke tests ==")
    print(f"== run.py:    {RUN_PY}")
    print(f"== last snap: {LAST}")
    test_monitor_file_present()
    test_runpy_imports()
    test_parse_uptime()
    test_daemon_json_shape()
    test_autopilot_list_nonempty()
    test_issue_total_field()
    test_last_snapshot_schema()
    test_monitor_end_to_end()
    print("[PASS] all checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
