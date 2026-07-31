#!/usr/bin/env python3
"""Self-tests for platform-freshness-monitor.

Verifies:
  1. Module imports cleanly and exposes PROBES.
  2. evaluate() classifies age buckets correctly.
  3. run_once() (full mode) executes end-to-end against the live LAN DB
     and produces a JSON snapshot with the expected schema.

Stdlib only. Run via: `python3 /Users/mark/multica/platform-freshness-monitor/test_run.py`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as m  # noqa: E402


def test_import() -> None:
    assert hasattr(m, "PROBES"), "PROBES not defined"
    assert len(m.PROBES) == 6, f"expected 6 probes, got {len(m.PROBES)}"
    expected_ids = {"comment", "activity_log", "autopilot_run", "artifact",
                    "webhook_delivery", "task_usage"}
    actual_ids = {p["id"] for p in m.PROBES}
    assert actual_ids == expected_ids, f"probe ids mismatch: {actual_ids}"
    print("[PASS] import + PROBES schema ok")


def test_evaluate() -> None:
    cases = [
        # (age_s, warn_s, escalate_s, empty, expected)
        (5,    600,   3600, False, "healthy"),
        (599,  600,   3600, False, "healthy"),
        (600,  600,   3600, False, "warn"),
        (3599, 600,   3600, False, "warn"),
        (3600, 600,   3600, False, "escalate"),
        (99999,600,   3600, False, "escalate"),
        (None, 600,   3600, False, "unknown"),
        (None, 600,   3600, True,  "escalate"),
        (0,    600,   3600, True,  "escalate"),
    ]
    for age_s, w, e, empty, want in cases:
        got = m.evaluate("test", age_s, w, e, empty=empty)
        assert got == want, f"evaluate(age={age_s}, warn={w}, esc={e}, empty={empty}) -> {got}, want {want}"
    print(f"[PASS] evaluate() — {len(cases)} bucket cases")


def test_humanize() -> None:
    cases = [(5, "5s"), (90, "1m30s"), (3661, "1h01m"), (90061, "1d01h"), (-1, "-1s (clock skew?)")]
    for s, want in cases:
        got = m._humanize_age(s)
        assert got == want, f"humanize({s}) -> {got!r}, want {want!r}"
    print(f"[PASS] _humanize_age() — {len(cases)} cases")


def test_run_once_live() -> None:
    snap = m.run_once(probe_ids=None, quiet=True)
    assert snap["monitor"] == "platform-freshness-monitor"
    assert snap["monitor_id"] == "SMA-35770-082"
    assert snap["verdict"] in {"healthy", "warn", "escalate", "no-op"}, snap["verdict"]
    assert "tables" in snap and len(snap["tables"]) == 6
    for tid, t in snap["tables"].items():
        assert "verdict" in t, f"missing verdict for {tid}"
        assert t["verdict"] in {"healthy", "warn", "escalate", "unknown"}, t
    last = m.LAST_SNAPSHOT
    assert last.exists(), "last-snapshot.json was not written"
    print(f"[PASS] run_once() live — verdict={snap['verdict']}, "
          f"elapsed={snap['elapsed_sec']}s, last_snapshot={last.stat().st_size}B")


def test_run_once_subset() -> None:
    snap = m.run_once(probe_ids=["comment", "autopilot_run"], quiet=True)
    assert snap["probe_count"] == 2
    assert set(snap["tables"].keys()) == {"comment", "autopilot_run"}
    print("[PASS] run_once() subset mode")


def main() -> int:
    test_import()
    test_evaluate()
    test_humanize()
    test_run_once_subset()
    test_run_once_live()
    print("OK — all platform-freshness-monitor self-tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())