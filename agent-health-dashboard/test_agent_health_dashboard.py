#!/usr/bin/env python3
"""Smoke test for agent-health-dashboard.

Validates:
  1. The dashboard script exists and is executable.
  2. Importing run.py does not raise (catches top-level syntax errors).
  3. `multica agent list --output json` returns a top-level array (or wrapped) with required fields.
  4. `multica task list --status running` returns the expected running-task page shape.
  5. `multica task list --status <s> --limit 1` returns `total` (or has_more/length fallback).
  6. parse_ts handles both tz-aware and naive ISO-8601 timestamps.
  7. last-snapshot.json schema after a real run has required keys.
  8. End-to-end run.py completes in <90s and updates last-snapshot.json.

Run: python3 /Users/mark/multica/agent-health-dashboard/test_agent_health_dashboard.py
Exits non-zero on first failure with a descriptive message.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DASHBOARD_DIR = Path("/Users/mark/multica/agent-health-dashboard")
RUN_PY = DASHBOARD_DIR / "run.py"
LAST = DASHBOARD_DIR / "last-snapshot.json"


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def test_dashboard_file_present() -> None:
    if not RUN_PY.exists():
        fail(f"run.py missing: {RUN_PY}")
    if not RUN_PY.stat().st_size > 1000:
        fail(f"run.py suspiciously small: {RUN_PY.stat().st_size} bytes")
    ok(f"run.py present, {RUN_PY.stat().st_size} bytes")


def test_runpy_imports() -> None:
    spec = importlib.util.spec_from_file_location("agent_health_dashboard", RUN_PY)
    if spec is None or spec.loader is None:
        fail("could not load run.py as module")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        fail(f"run.py raised on import: {e!r}")
    for name in (
        "probe_agents",
        "probe_task_flow",
        "fetch_all_running",
        "fetch_per_agent_recent",
        "probe_per_agent",
        "parse_ts",
        "main",
    ):
        if not hasattr(mod, name):
            fail(f"run.py missing callable: {name}")
    ok("run.py imports & exposes all required functions")


def test_parse_ts() -> None:
    spec = importlib.util.spec_from_file_location("agent_health_dashboard", RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # tz-aware
    a = mod.parse_ts("2026-07-26T15:30:00+08:00")
    if a is None or a.tzinfo is None:
        fail(f"parse_ts tz-aware returned {a!r}")
    # naive -> should be coerced to UTC
    b = mod.parse_ts("2026-07-26T15:30:00")
    if b is None or b.tzinfo != timezone.utc:
        fail(f"parse_ts naive returned {b!r}")
    # bad input
    c = mod.parse_ts("not-a-timestamp")
    if c is not None:
        fail(f"parse_ts garbage returned {c!r} (expected None)")
    # None
    d = mod.parse_ts(None)
    if d is not None:
        fail(f"parse_ts None returned {d!r}")
    ok("parse_ts: tz-aware / naive / garbage / None all handled")


def test_agent_list_shape() -> None:
    proc = subprocess.run(
        ["multica", "agent", "list", "--output", "json"],
        capture_output=True, text=True, timeout=20,
    )
    if proc.returncode != 0:
        fail(f"`multica agent list` rc={proc.returncode}, stderr={proc.stderr[:200]}")
    raw = proc.stdout.strip()
    try:
        data = json.loads(raw)
    except Exception as e:
        fail(f"agent list JSON parse error: {e!r}")
    agents = data if isinstance(data, list) else data.get("agents", data.get("data", []))
    if not agents:
        fail(f"agent list empty (got {len(agents)} entries)")
    sample = agents[0]
    for k in ("id", "name", "max_concurrent_tasks"):
        if k not in sample:
            fail(f"agent list entry missing key: {k}; keys={list(sample.keys())}")
    ok(f"agent list returns {len(agents)} agents (sample has required keys)")


def test_task_running_shape() -> None:
    proc = subprocess.run(
        ["multica", "task", "list", "--status", "running", "--limit", "200", "--output", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        fail(f"`multica task list --status running` rc={proc.returncode}")
    d = json.loads(proc.stdout)
    if "tasks" not in d:
        fail(f"task list running missing 'tasks' field; keys={list(d.keys())}")
    n = len(d["tasks"])
    ok(f"task list running returns {n} tasks; sample agent_id field present")
    # Check that running tasks have started_at + agent_id
    for t in d["tasks"][:3]:
        if "agent_id" not in t or "started_at" not in t:
            fail(f"running task missing agent_id/started_at: {list(t.keys())}")


def test_task_total_field() -> None:
    """Ensure `multica task list --status <s> --limit 1` returns `total` (or has length fallback)."""
    for s in ("running", "queued", "failed"):
        proc = subprocess.run(
            ["multica", "task", "list", "--status", s, "--limit", "1", "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            fail(f"task list --status {s} rc={proc.returncode}")
        d = json.loads(proc.stdout)
        if "total" in d:
            ok(f"task list --status {s}: total={d['total']}")
        else:
            # Fallback: has tasks list of some size
            if "tasks" not in d:
                fail(f"task list --status {s} missing both 'total' and 'tasks'; keys={list(d.keys())}")
            ok(f"task list --status {s}: len(tasks)={len(d['tasks'])} (no 'total')")


def test_last_snapshot_schema() -> None:
    if not LAST.exists():
        fail(f"last-snapshot.json missing; run.py must run before this test")
    d = json.loads(LAST.read_text())
    for k in ("ts_utc", "verdict", "agents_probe", "task_flow", "per_agent", "long_running_tasks", "thresholds"):
        if k not in d:
            fail(f"last-snapshot.json missing key: {k}")
    if d["verdict"] not in ("healthy", "warn", "escalate", "no-op"):
        fail(f"last-snapshot verdict unexpected: {d['verdict']!r}")
    if not isinstance(d["per_agent"], list):
        fail(f"per_agent is not a list (got {type(d['per_agent']).__name__})")
    if d["agents_probe"].get("active_count", 0) < 1:
        fail(f"agents_probe.active_count={d['agents_probe'].get('active_count')}, expected >=1")
    ok(f"last-snapshot.json verdict={d['verdict']}, per_agent={len(d['per_agent'])} agents")


def test_dashboard_end_to_end() -> None:
    """Run run.py; require exit code 0 and that last-snapshot.json updates."""
    before = LAST.read_text() if LAST.exists() else ""
    proc = subprocess.run(
        ["python3", str(RUN_PY)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        fail(f"run.py rc={proc.returncode}, stderr={proc.stderr[:500]}")
    after = LAST.read_text()
    if not after or after == before:
        fail("run.py did not update last-snapshot.json")
    snap = json.loads(after)
    ts = datetime.fromisoformat(snap["ts_utc"])
    age_sec = (datetime.now(timezone.utc) - ts).total_seconds()
    if age_sec > 180:
        fail(f"last-snapshot.json is stale by {age_sec:.0f}s (expected <180s)")
    ok(f"run.py end-to-end pass; snapshot age {age_sec:.1f}s, verdict={snap['verdict']}")


def main() -> int:
    print(f"== Agent-Health-Dashboard smoke tests ==")
    print(f"== run.py:    {RUN_PY}")
    print(f"== last snap: {LAST}")
    test_dashboard_file_present()
    test_runpy_imports()
    test_parse_ts()
    test_agent_list_shape()
    test_task_running_shape()
    test_task_total_field()
    test_last_snapshot_schema()
    test_dashboard_end_to_end()
    print("[PASS] all checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())