#!/usr/bin/env python3
"""PLATFORM-FRESHNESS-MONITOR — iter-1 implementation of MAP-P9 monitor #82.

Goal: detect when key multica platform tables stop receiving writes.

This is the SMA-35770-082 ("Monitor #82 — data freshness alert") monitor, focused on
**platform-side write activity** in the multica Postgres backend (vs. the
strategy-side `run_metric` ingest already covered by `docs/runbooks/data-outage.md`
and Monitor #2). It probes 6 tables that should be growing whenever the platform is
healthy, and assigns a per-table age threshold based on the expected cadence:

  T1. comment            — issue/task/agent comments       warn=10m   escalate=60m
  T2. activity_log       — user + agent activity events    warn=10m   escalate=60m
  T3. autopilot_run      — autopilot execution history     warn=20m   escalate=2h
  T4. artifact           — published run/strategy artifacts warn=4h    escalate=24h
  T5. webhook_delivery   — inbound webhook processing      warn=30m   escalate=4h
  T6. task_usage         — per-task LLM token accounting    warn=4h    escalate=24h

Each probe is a single `MAX(<ts_col>)` query that returns age in seconds. The script
is read-only — it does not create issues, restart services, or alter configuration.
verdict ∈ {healthy, warn, escalate, no-op (DB unreachable)}.

Output layout (mirrors `db-pool-monitor/` and `status-page-monitor/`):
  /Users/mark/multica/platform-freshness-monitor/last-snapshot.json
  /Users/mark/multica/platform-freshness-monitor/state.json
  /Users/mark/multica/platform-freshness-monitor/dedup-state.json
  /Users/mark/multica/platform-freshness-monitor/snapshot-<UTC>.json

Exit code is always 0 — this monitor is a pure observer. Escalation is the
responsibility of whatever consumer (autopilot, on-call runbook) is wired to it.

Usage:
  python3 /Users/mark/multica/platform-freshness-monitor/run.py
  python3 /Users/mark/multica/platform-freshness-monitor/run.py --probe-only comment,autopilot_run
  python3 /Users/mark/multica/platform-freshness-monitor/run.py --quiet
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONITOR_DIR = Path("/Users/mark/multica/platform-freshness-monitor")
MONITOR_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = MONITOR_DIR / "state.json"
DEDUP_FILE = MONITOR_DIR / "dedup-state.json"
LAST_SNAPSHOT = MONITOR_DIR / "last-snapshot.json"

# SSH target for the Postgres host (LAN). Mirrors the convention in
# docs/runbooks/data-outage.md (192.168.0.105 / smark).
DB_HOST = "192.168.0.105"
DB_SSH_USER = "smark"
DB_CONTAINER = "multica-postgres-1"
DB_NAME = "multica"
DB_USER = "multica"

# Per-table probe definition: (table, ts_column, warn_seconds, escalate_seconds).
# Rationale captured in the runbook.
PROBES: list[dict[str, Any]] = [
    {"id": "comment",          "table": "comment",          "ts_col": "created_at", "warn_s": 600,  "escalate_s": 3600},
    {"id": "activity_log",     "table": "activity_log",     "ts_col": "created_at", "warn_s": 600,  "escalate_s": 3600},
    {"id": "autopilot_run",    "table": "autopilot_run",    "ts_col": "triggered_at", "warn_s": 1200, "escalate_s": 7200},
    {"id": "artifact",         "table": "artifact",         "ts_col": "created_at", "warn_s": 14400, "escalate_s": 86400},
    {"id": "webhook_delivery", "table": "webhook_delivery", "ts_col": "created_at", "warn_s": 1800, "escalate_s": 14400},
    {"id": "task_usage",       "table": "task_usage",       "ts_col": "created_at", "warn_s": 14400, "escalate_s": 86400},
]

# Hard cap on DB query time so a hung ssh/psql never wedges the monitor.
SSH_TIMEOUT_S = 10
PSQL_TIMEOUT_S = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    s = ts.strip()
    if not s:
        return None
    # psql -At with interval casts returns e.g. "00:01:09.464506" or "4 days 04:02:19.19"
    # and ISO timestamps. We try ISO first; fall back to None (skip the probe).
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def run_psql(sql: str, timeout: int = PSQL_TIMEOUT_S) -> tuple[int, str, str]:
    """Run a single SQL statement against the LAN Postgres container.

    Uses a local stdin-pipe approach to avoid nested shell quoting with
    single quotes (which would otherwise break the data-outage.md style
    `ssh ... docker exec ... psql ... 'SELECT ...'` chain).
    """
    proc = subprocess.run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={SSH_TIMEOUT_S}",
            f"{DB_SSH_USER}@{DB_HOST}",
            f"docker exec -i {DB_CONTAINER} psql -U {DB_USER} -d {DB_NAME} -X -At",
        ],
        input=sql,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def probe_table(table: str, ts_col: str) -> dict[str, Any]:
    """Return {newest_utc, age_seconds} for a single table, or {error}."""
    # Age in seconds is the source of truth. The newest ISO timestamp is
    # only used for human-readable output and SSH-time skew checks.
    sql = (
        "SELECT "
        f"COALESCE(to_char(max({ts_col}) AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'), ''), "
        f"COALESCE((EXTRACT(EPOCH FROM (now() - max({ts_col}))))::bigint::text, '') "
        f"FROM {table};\n"
    )
    rc, out, err = run_psql(sql)
    if rc != 0:
        return {"error": f"psql rc={rc}", "stderr": err[:300]}
    line = out.strip()
    if not line:
        return {"error": "empty psql output", "table": table}
    parts = line.split("|")
    if len(parts) != 2:
        return {"error": f"unexpected psql output: {line!r}", "table": table}
    newest_raw, age_raw = parts[0].strip(), parts[1].strip()
    if not age_raw:
        # Table is empty (no rows). Treat as "freshness = never" → escalate immediately.
        return {
            "newest_utc": None,
            "age_seconds": None,
            "age_human": "no-rows",
            "empty_table": True,
        }
    try:
        age_s = int(age_raw)
    except ValueError:
        return {"error": f"bad age value: {age_raw!r}", "newest_raw": newest_raw}
    return {
        "newest_utc": newest_raw or None,
        "age_seconds": age_s,
        "age_human": _humanize_age(age_s),
    }


def _humanize_age(seconds: int) -> str:
    """Compact human-readable age string."""
    if seconds < 0:
        return f"{seconds}s (clock skew?)"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m:02d}m"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d{h:02d}h"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(d: dict) -> None:
    STATE_FILE.write_text(json.dumps(d, indent=2, default=str))


def load_dedup() -> dict:
    if DEDUP_FILE.exists():
        try:
            return json.loads(DEDUP_FILE.read_text())
        except Exception:
            pass
    return {}


def save_dedup(d: dict) -> None:
    DEDUP_FILE.write_text(json.dumps(d, indent=2, default=str))


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def evaluate(probe_id: str, age_seconds: int | None, warn_s: int, escalate_s: int, empty: bool = False) -> str:
    """Return 'healthy' | 'warn' | 'escalate' | 'unknown' for a single probe."""
    if empty:
        # Empty table = nothing has ever been written; treat as escalate.
        return "escalate"
    if age_seconds is None:
        return "unknown"
    if age_seconds >= escalate_s:
        return "escalate"
    if age_seconds >= warn_s:
        return "warn"
    return "healthy"


def run_once(probe_ids: list[str] | None = None, quiet: bool = False) -> dict[str, Any]:
    start = now_utc()
    started_perf = time.perf_counter()
    state = load_state()
    dedup = load_dedup()

    selected = [p for p in PROBES if probe_ids is None or p["id"] in probe_ids]
    if not selected:
        snap = {
            "ts_utc": start.isoformat(),
            "ts_epoch": int(start.timestamp()),
            "verdict": "no-op",
            "reason": f"no probes selected (requested={probe_ids})",
        }
        if not quiet:
            print(json.dumps(snap, indent=2, default=str))
        return snap

    table_results: dict[str, dict[str, Any]] = {}
    escalations: list[str] = []
    warnings: list[str] = []

    for probe in selected:
        result = probe_table(probe["table"], probe["ts_col"])
        age_s = result.get("age_seconds")
        is_empty = bool(result.get("empty_table"))
        verdict = evaluate(probe["id"], age_s, probe["warn_s"], probe["escalate_s"], empty=is_empty)
        result["verdict"] = verdict
        result["threshold_warn_s"] = probe["warn_s"]
        result["threshold_escalate_s"] = probe["escalate_s"]
        table_results[probe["id"]] = result

        if verdict == "escalate":
            if is_empty:
                escalations.append(
                    f"{probe['id']}: table is empty (no rows ever written)"
                )
            else:
                escalations.append(
                    f"{probe['id']}: age={result.get('age_human', '?')} "
                    f"(>= escalate {probe['escalate_s']}s)"
                )
        elif verdict == "warn":
            warnings.append(
                f"{probe['id']}: age={result.get('age_human', '?')} "
                f"(>= warn {probe['warn_s']}s)"
            )

    elapsed = round(time.perf_counter() - started_perf, 3)

    # Aggregate verdict.
    if escalations:
        verdict = "escalate"
    elif warnings:
        verdict = "warn"
    elif all(r.get("verdict") == "unknown" for r in table_results.values()):
        verdict = "no-op"
    else:
        verdict = "healthy"

    snap = {
        "monitor": "platform-freshness-monitor",
        "monitor_id": "SMA-35770-082",
        "ts_utc": start.isoformat(),
        "ts_epoch": int(start.timestamp()),
        "elapsed_sec": elapsed,
        "verdict": verdict,
        "escalations": escalations,
        "warnings": warnings,
        "tables": table_results,
        "thresholds": {
            "comment":          {"warn_s": 600,  "escalate_s": 3600},
            "activity_log":     {"warn_s": 600,  "escalate_s": 3600},
            "autopilot_run":    {"warn_s": 1200, "escalate_s": 7200},
            "artifact":         {"warn_s": 14400, "escalate_s": 86400},
            "webhook_delivery": {"warn_s": 1800, "escalate_s": 14400},
            "task_usage":       {"warn_s": 14400, "escalate_s": 86400},
        },
        "probe_count": len(table_results),
    }

    # Persist
    snap_path = MONITOR_DIR / f"snapshot-{start.strftime('%Y-%m-%dT%H-%M-%S')}Z.json"
    snap_path.write_text(json.dumps(snap, indent=2, default=str))
    LAST_SNAPSHOT.write_text(json.dumps(snap, indent=2, default=str))
    save_state({**state, "last_run": snap, "last_snapshot_path": str(snap_path)})

    # Update dedup map (last verdict + ts per probe)
    for probe_id, r in table_results.items():
        dedup[probe_id] = {
            "verdict": r.get("verdict"),
            "age_seconds": r.get("age_seconds"),
            "ts_utc": snap["ts_utc"],
        }
    save_dedup(dedup)

    if not quiet:
        print(json.dumps(snap, indent=2, default=str))
    return snap


def main() -> int:
    p = argparse.ArgumentParser(description="platform-freshness-monitor (SMA-35770-082)")
    p.add_argument(
        "--probe-only",
        help="Comma-separated subset of probes (e.g. comment,autopilot_run). Default: all 6.",
    )
    p.add_argument("--quiet", action="store_true", help="Suppress stdout (still writes files).")
    args = p.parse_args()
    probe_ids = None
    if args.probe_only:
        probe_ids = [s.strip() for s in args.probe_only.split(",") if s.strip()]
    run_once(probe_ids=probe_ids, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())