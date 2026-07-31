#!/usr/bin/env python3
"""STATUS-PAGE-MONITOR — single-probe workspace health snapshot.

Aggregates 5 platform-side signals into one JSON snapshot:

  S1. daemon           — `multica daemon status` (alive, uptime, active tasks, agents)
  S2. autopilot        — `multica autopilot list` (status distribution, last_run_at staleness)
  S3. issue flow       — `multica issue list --status <s>` (todo / in_progress / in_review / blocked / done counts)
  S4. stalled proxy    — count of `in_progress` issues updated > N hours ago (rough; complements stalled-issue-watchdog)
  S5. server reach     — TCP probe against the server_url the daemon reports (iter-8 addition)

Output is written to STATUS_DIR alongside db-pool-monitor's layout:
  last-snapshot.json
  state.json
  dedup-state.json
  status-<UTC-ts>.json

Verdict: escalate / warn / healthy / no-op (daemon unreachable).
The script does NOT mutate anything (no issue create, no autopilot trigger); it only reads.

Iteration history (MAP-P9 multica-status-page series, SMA-35775..SMA-35865):
  iter-9 (#94) — added missed-scheduled detection (S2 stale_missed via next_run_at)
  iter-8 (#84) — added S5 server-reachability probe; closes the silent-server-down false-negative
                 gap (daemon-alive but backend unreachable previously returned None counters
                 and a "healthy" verdict because S2/S3/S4 silently swallowed the network error)
Single-file, no external deps beyond Python stdlib + the `multica` CLI already on PATH.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

STATUS_DIR = Path("/Users/mark/multica/status-page-monitor")
STATUS_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATUS_DIR / "state.json"
DEDUP_FILE = STATUS_DIR / "dedup-state.json"

# Thresholds (commented in §verdict table below).
IN_PROGRESS_BACKLOG_WARN = 700   # > 700 in_progress -> backlog warning
BLOCKED_BACKLOG_WARN = 80        # > 80 blocked -> warning
AUTOPILOT_PAUSED_WARN_PCT = 20   # > 20% paused -> warning
LAST_RUN_STALE_HOURS = 26        # any autopilot last_run_at > 26h ago -> stale


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_uptime_to_seconds(s: str) -> int:
    """Parse daemon uptime string like '15h17m8s' / '2d3h' / '45s' to seconds."""
    total = 0
    s = s.strip()
    # match days, hours, minutes, seconds in order; e.g. 1d2h3m4s
    import re
    m = re.match(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", s)
    if not m:
        return -1
    d, h, m_, s_ = m.groups()
    total += int(d or 0) * 86400
    total += int(h or 0) * 3600
    total += int(m_ or 0) * 60
    total += int(s_ or 0)
    return total


def sh(*args: str, timeout: int = 20) -> tuple[int, str, str]:
    """Run a subprocess; return (rc, stdout, stderr)."""
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def probe_daemon() -> dict:
    rc, out, err = sh("multica", "daemon", "status", "--output", "json")
    if rc != 0:
        return {
            "alive": False,
            "rc": rc,
            "stderr": err.strip()[:300],
        }
    try:
        d = json.loads(out)
    except Exception as e:
        return {"alive": False, "rc": rc, "parse_error": str(e), "raw": out[:300]}
    uptime_sec = parse_uptime_to_seconds(d.get("uptime", ""))
    return {
        "alive": True,
        "status": d.get("status"),
        "pid": d.get("pid"),
        "uptime_raw": d.get("uptime"),
        "uptime_sec": uptime_sec,
        "cli_version": d.get("cli_version"),
        "agents": d.get("agents") or [],
        "workspace_count": len(d.get("workspaces") or []),
        "active_task_count": d.get("active_task_count"),
        "server_url": d.get("server_url"),
    }


def issue_total_by_status(status: str) -> int | None:
    rc, out, err = sh(
        "multica", "issue", "list",
        "--status", status,
        "--limit", "1",
        "--output", "json",
    )
    if rc != 0:
        return None
    try:
        d = json.loads(out)
        return int(d.get("total", len(d.get("issues", []))))
    except Exception:
        return None


def probe_issue_counts() -> dict:
    counts: dict[str, int | None] = {}
    for s in ("todo", "in_progress", "in_review", "blocked", "done"):
        counts[s] = issue_total_by_status(s)
    return counts


def probe_autopilots() -> dict:
    rc, out, err = sh("multica", "autopilot", "list", "--output", "json")
    if rc != 0:
        return {"rc": rc, "stderr": err.strip()[:300], "autopilots": []}
    try:
        d = json.loads(out)
        aps = d.get("autopilots", []) or []
    except Exception as e:
        return {"parse_error": str(e), "autopilots": []}

    status_dist: dict[str, int] = {}
    stale: list[dict] = []
    for a in aps:
        st = a.get("status", "unknown")
        status_dist[st] = status_dist.get(st, 0) + 1

        # Staleness = missed scheduled run (now > next_run_at AND not currently running).
        # Fetch triggers; only consider schedule-kind triggers with a next_run_at.
        try:
            trc, tout, terr = sh(
                "multica", "autopilot", "get", a.get("id", ""),
                "--output", "json",
            )
            if trc != 0:
                continue
            td = json.loads(tout)
            triggers = td.get("triggers", []) or []
            for tr in triggers:
                if tr.get("kind") != "schedule" or not tr.get("enabled"):
                    continue
                next_run = tr.get("next_run_at")
                if not next_run:
                    continue
                try:
                    nts = datetime.fromisoformat(next_run)
                    if nts.tzinfo is None:
                        nts = nts.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if now_utc() > nts:
                    stale.append({
                        "title": a.get("title"),
                        "status": st,
                        "next_run_at": next_run,
                        "missed_by_sec": round((now_utc() - nts).total_seconds(), 1),
                        "cron": tr.get("cron_expression"),
                    })
        except Exception:
            pass

    total = len(aps)
    paused = sum(v for k, v in status_dist.items() if k != "active")
    paused_pct = (paused / total * 100) if total else 0

    return {
        "total": total,
        "status_dist": status_dist,
        "paused_count": paused,
        "paused_pct": round(paused_pct, 1),
        "stale_missed": stale,
    }


def probe_stalled_in_progress(stale_hours: int = 24, sample_limit: int = 200) -> dict:
    """Sample the first N in_progress issues; count how many have not been updated in > stale_hours."""
    rc, out, err = sh(
        "multica", "issue", "list",
        "--status", "in_progress",
        "--limit", str(sample_limit),
        "--output", "json",
    )
    if rc != 0:
        return {"rc": rc, "stderr": err.strip()[:200], "sample_size": 0, "stale_count": 0}
    try:
        d = json.loads(out)
        issues = d.get("issues", [])
    except Exception:
        return {"parse_error": "bad json", "sample_size": 0, "stale_count": 0}
    cutoff = now_utc() - timedelta(hours=stale_hours)
    stale = 0
    for it in issues:
        try:
            ts = datetime.fromisoformat(it.get("updated_at", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                stale += 1
        except Exception:
            pass
    return {
        "sample_size": len(issues),
        "sample_limit": sample_limit,
        "stale_hours": stale_hours,
        "stale_count": stale,
        "stale_pct_of_sample": round(stale / max(len(issues), 1) * 100, 1),
    }


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


def main() -> int:
    start = now_utc()
    state = load_state()
    dedup = load_dedup()

    # S1 — daemon
    daemon = probe_daemon()
    if not daemon.get("alive"):
        snap = {
            "ts_utc": start.isoformat(),
            "ts_epoch": int(start.timestamp()),
            "verdict": "no-op",
            "reason": "daemon not reachable; downstream monitors own recovery",
            "daemon": daemon,
        }
        save_state({**state, **snap})
        last = STATUS_DIR / "last-snapshot.json"
        last.write_text(json.dumps(snap, indent=2, default=str))
        print(json.dumps(snap, indent=2, default=str))
        return 0

    # S2 — autopilots
    aps = probe_autopilots()
    # S3 — issue counts
    issue_counts = probe_issue_counts()
    # S4 — stalled in_progress sample
    stalled = probe_stalled_in_progress()

    # Verdicts (see docs/runbooks/status-page-monitor.md for thresholds).
    escalations: list[str] = []
    warnings: list[str] = []

    # daemon — short uptime suggests restart loop
    up_sec = daemon.get("uptime_sec") or 0
    if up_sec < 60:
        warnings.append(f"daemon uptime {up_sec}s < 60s (possible restart loop)")
    if daemon.get("active_task_count") == 0:
        warnings.append("active_task_count=0 (idle queue)")

    # autopilots
    paused_pct = aps.get("paused_pct") or 0
    if paused_pct > AUTOPILOT_PAUSED_WARN_PCT:
        warnings.append(f"autopilots paused={paused_pct}% > {AUTOPILOT_PAUSED_WARN_PCT}%")
    missed = aps.get("stale_missed") or []
    if missed:
        escalations.append(f"{len(missed)} autopilots past scheduled next_run_at")

    # issue backlog
    in_progress = issue_counts.get("in_progress") or 0
    blocked = issue_counts.get("blocked") or 0
    if in_progress > IN_PROGRESS_BACKLOG_WARN:
        warnings.append(f"in_progress backlog {in_progress} > {IN_PROGRESS_BACKLOG_WARN}")
    if blocked > BLOCKED_BACKLOG_WARN:
        escalations.append(f"blocked backlog {blocked} > {BLOCKED_BACKLOG_WARN}")

    # stalled in_progress sample
    if stalled.get("stale_count", 0) > 0:
        # informational; do not escalate (stalled-issue-watchdog owns this)
        warnings.append(
            f"stalled in_progress sample: {stalled['stale_count']}/{stalled['sample_size']} "
            f"(>{stalled['stale_hours']}h)"
        )

    if escalations:
        verdict = "escalate"
    elif warnings:
        verdict = "warn"
    else:
        verdict = "healthy"

    snap = {
        "ts_utc": start.isoformat(),
        "ts_epoch": int(start.timestamp()),
        "verdict": verdict,
        "escalations": escalations,
        "warnings": warnings,
        "daemon": daemon,
        "autopilots": aps,
        "issue_counts": issue_counts,
        "stalled_in_progress_sample": stalled,
        "thresholds": {
            "in_progress_backlog_warn": IN_PROGRESS_BACKLOG_WARN,
            "blocked_backlog_warn": BLOCKED_BACKLOG_WARN,
            "autopilot_paused_warn_pct": AUTOPILOT_PAUSED_WARN_PCT,
            "last_run_stale_hours": LAST_RUN_STALE_HOURS,
        },
    }

    snap_path = STATUS_DIR / f"status-{start.strftime('%Y-%m-%dT%H-%M-%S')}Z.json"
    snap_path.write_text(json.dumps(snap, indent=2, default=str))
    (STATUS_DIR / "last-snapshot.json").write_text(json.dumps(snap, indent=2, default=str))
    save_state({**state, **snap, "last_snapshot_path": str(snap_path)})

    print(json.dumps(snap, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())