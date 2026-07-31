#!/usr/bin/env python3
"""AGENT-HEALTH-DASHBOARD — per-agent workspace health aggregator.

Aggregates 5 agent-side signals into one JSON snapshot:

  S1. agent_definitions      — `multica agent list` (name, max_concurrent_tasks, runtime_id, model, archived)
  S2. task_flow              — `multica task list --status <s>` counts per status (queued/dispatched/running/completed/failed/cancelled)
  S3. per_agent_active_load  — running tasks per agent; capacity headroom vs max_concurrent_tasks
  S4. per_agent_failure_24h  — failed/total ratio over the last 24h per agent
  S5. long_running_tasks     — running tasks whose started_at is older than LONG_RUNNING_MIN (default 30m)

Output is written to DASHBOARD_DIR alongside status-page-monitor's layout:
  last-snapshot.json
  state.json
  agent-<UTC-ts>.json
  dedup-state.json

Verdict: escalate / warn / healthy / no-op (multica CLI not reachable).
The script does NOT mutate anything (no issue create, no autopilot trigger); it only reads.

Iteration history (MAP-P9 agent-health-dashboard series):
  iter-10 (#93) — initial implementation; 5 signals; per-agent verdict line.

Single-file, no external deps beyond Python stdlib + the `multica` CLI already on PATH.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DASHBOARD_DIR = Path("/Users/mark/multica/agent-health-dashboard")
DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DASHBOARD_DIR / "state.json"
LAST_SNAPSHOT = DASHBOARD_DIR / "last-snapshot.json"
DEDUP_FILE = DASHBOARD_DIR / "dedup-state.json"

# Thresholds (commented in §verdict table below).
LONG_RUNNING_MIN = 30          # running > 30m -> long-running task
FAIL_RATE_WARN_PCT = 30.0      # per-agent 24h fail% > 30% -> warn
FAIL_RATE_ESCALATE_PCT = 50.0  # per-agent 24h fail% > 50% -> escalate
OVERLOAD_FACTOR = 1.0          # active_load > max_concurrent_tasks * OVERLOAD_FACTOR -> escalate
NO_SUCCESS_DAYS = 7            # an idle active agent with no completed task in N days -> warn
LOOKBACK_HOURS = 24            # window for S4 failure-rate computation
RUNNING_PAGE_LIMIT = 200       # page size for --status running fetch
PER_AGENT_PAGE_LIMIT = 50      # page size for per-agent 24h recent fetch

# Task statuses we expect to surface in S2 task flow.
TASK_STATUSES = ("queued", "dispatched", "running", "completed", "failed", "cancelled")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def sh(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess; return (rc, stdout, stderr)."""
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        ts = datetime.fromisoformat(s)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception:
        return None


# ─── S1 — agent definitions ────────────────────────────────────────────────

def probe_agents() -> dict:
    """S1 — agent definitions. `multica agent list` returns a top-level array."""
    rc, out, err = sh("multica", "agent", "list", "--output", "json")
    if rc != 0:
        return {"rc": rc, "stderr": err.strip()[:300], "agents": [], "archived": []}
    try:
        agents = json.loads(out)
        if isinstance(agents, dict):
            agents = agents.get("agents", agents.get("data", []))
    except Exception as e:
        return {"parse_error": str(e), "agents": [], "archived": []}

    active = []
    archived = []
    for a in agents:
        rec = {
            "id": a.get("id"),
            "name": a.get("name"),
            "model": a.get("model"),
            "runtime_id": a.get("runtime_id"),
            "runtime_mode": a.get("runtime_mode"),
            "max_concurrent_tasks": a.get("max_concurrent_tasks"),
            "skill_count": len(a.get("skills") or []),
            "has_custom_env": a.get("has_custom_env"),
            "archived_at": a.get("archived_at"),
        }
        if a.get("archived_at"):
            archived.append(rec)
        else:
            active.append(rec)

    active_sorted = sorted(active, key=lambda x: (x.get("name") or ""))
    return {
        "total": len(agents),
        "active_count": len(active),
        "archived_count": len(archived),
        "sum_max_concurrent_tasks": sum((a.get("max_concurrent_tasks") or 0) for a in active),
        "agents": active_sorted,
        "archived": archived,
    }


# ─── S2 — task flow ────────────────────────────────────────────────────────

def issue_total_by_status_via_tasklist(status: str) -> int | None:
    """Use `multica task list --status <s> --limit 1` and parse `total` (or fall back to len(tasks))."""
    rc, out, err = sh(
        "multica", "task", "list",
        "--status", status,
        "--limit", "1",
        "--output", "json",
    )
    if rc != 0:
        return None
    try:
        d = json.loads(out)
    except Exception:
        return None
    if "total" in d:
        try:
            return int(d["total"])
        except Exception:
            pass
    return len(d.get("tasks", []))


def probe_task_flow() -> dict:
    """S2 — global task flow counts via per-status fetches."""
    counts: dict[str, int | None] = {}
    has_more_any: dict[str, bool] = {}
    for s in TASK_STATUSES:
        rc, out, err = sh(
            "multica", "task", "list",
            "--status", s,
            "--limit", "1",
            "--output", "json",
        )
        if rc != 0:
            counts[s] = None
            has_more_any[s] = False
            continue
        try:
            d = json.loads(out)
        except Exception:
            counts[s] = None
            has_more_any[s] = False
            continue
        if "total" in d:
            try:
                counts[s] = int(d["total"])
                has_more_any[s] = bool(d.get("has_more"))
                continue
            except Exception:
                pass
        # Fallback: count via full page
        rc2, out2, _ = sh(
            "multica", "task", "list",
            "--status", s,
            "--limit", "200",
            "--output", "json",
        )
        if rc2 != 0:
            counts[s] = None
            has_more_any[s] = False
            continue
        try:
            d2 = json.loads(out2)
            counts[s] = len(d2.get("tasks", []) or [])
            has_more_any[s] = bool(d2.get("has_more"))
        except Exception:
            counts[s] = None
            has_more_any[s] = False

    return {
        "status_counts": counts,
        "has_more_by_status": has_more_any,
    }


# ─── S3 / S5 — running tasks (load + long-running) ────────────────────────

def fetch_all_running() -> list[dict]:
    rc, out, err = sh(
        "multica", "task", "list",
        "--status", "running",
        "--limit", str(RUNNING_PAGE_LIMIT),
        "--output", "json",
    )
    if rc != 0:
        return []
    try:
        d = json.loads(out)
        return d.get("tasks", []) or []
    except Exception:
        return []


# ─── S4 — per-agent recent (24h) ──────────────────────────────────────────

def fetch_per_agent_recent(agent_id: str, since: datetime, limit: int = PER_AGENT_PAGE_LIMIT) -> list[dict]:
    rc, out, err = sh(
        "multica", "task", "list",
        "--agent-id", agent_id,
        "--limit", str(limit),
        "--output", "json",
    )
    if rc != 0:
        return []
    try:
        d = json.loads(out)
        tasks = d.get("tasks", []) or []
    except Exception:
        return []
    out_list: list[dict] = []
    for t in tasks:
        ts_raw = t.get("created_at") or t.get("started_at") or t.get("dispatched_at") or t.get("completed_at")
        ts = parse_ts(ts_raw)
        if ts is None:
            continue
        if ts >= since:
            out_list.append(t)
    return out_list


# ─── aggregator ───────────────────────────────────────────────────────────

def probe_per_agent(agents: list[dict], running_tasks: list[dict]) -> dict:
    """S3 + S4 + S5 — per-agent load, failure-rate, last-success, long-running."""
    now = now_utc()
    since_24h = now - timedelta(hours=LOOKBACK_HOURS)

    # S3 — group running tasks by agent
    running_by_agent: dict[str, list[dict]] = {}
    long_running: list[dict] = []
    for t in running_tasks:
        aid = t.get("agent_id") or ""
        started_raw = t.get("started_at")
        started = parse_ts(started_raw)
        if started is None:
            continue
        age_min = (now - started).total_seconds() / 60.0
        rec = {"task_id": t.get("id"), "started_at": started_raw, "age_min": round(age_min, 1)}
        running_by_agent.setdefault(aid, []).append(rec)
        if age_min > LONG_RUNNING_MIN:
            long_running.append({
                "task_id": t.get("id"),
                "agent_id": aid,
                "started_at": started_raw,
                "age_min": round(age_min, 1),
                "trigger_summary": t.get("trigger_summary"),
            })

    per_agent: list[dict] = []
    for a in agents:
        aid = a["id"]
        name = a["name"]
        cap = a.get("max_concurrent_tasks") or 0

        # S3
        running = running_by_agent.get(aid, [])
        active_load = len(running)
        headroom = cap - active_load  # negative when overloaded
        overloaded = cap and active_load > cap * OVERLOAD_FACTOR

        # S4
        recent = fetch_per_agent_recent(aid, since_24h)
        failed = sum(1 for t in recent if t.get("status") == "failed")
        completed = sum(1 for t in recent if t.get("status") == "completed")
        total_24h = len(recent)
        fail_pct = round((failed / total_24h) * 100, 1) if total_24h else 0.0
        success_pct = round((completed / total_24h) * 100, 1) if total_24h else 0.0

        # last_success: from the global running_tasks (cross-agent view) + a per-agent
        # completed fetch for older history. Keep this light: scan running_tasks only
        # for completed-at; that's a 24h upper bound. For >24h last-success we use the
        # per-agent completed-only fetch.
        last_success_ts: datetime | None = None
        for t in running_tasks:
            if t.get("agent_id") != aid or t.get("status") != "completed":
                continue
            tt = parse_ts(t.get("completed_at") or t.get("started_at"))
            if tt and (last_success_ts is None or tt > last_success_ts):
                last_success_ts = tt

        # Also check per-agent broader recent (any completed in 7d for `no_success_too_long`)
        # using a per-agent fetch with no time filter via task list default sort
        if last_success_ts is None:
            rc, out, _ = sh(
                "multica", "task", "list",
                "--agent-id", aid,
                "--status", "completed",
                "--limit", "10",
                "--output", "json",
            )
            if rc == 0:
                try:
                    d = json.loads(out)
                    for t in d.get("tasks", []) or []:
                        tt = parse_ts(t.get("completed_at") or t.get("started_at"))
                        if tt and (last_success_ts is None or tt > last_success_ts):
                            last_success_ts = tt
                except Exception:
                    pass

        last_success_age_h = round((now - last_success_ts).total_seconds() / 3600.0, 1) if last_success_ts else None
        no_success_too_long = (
            last_success_age_h is not None
            and last_success_age_h > NO_SUCCESS_DAYS * 24
            and active_load == 0
        )

        per_agent.append({
            "agent_id": aid,
            "name": name,
            "model": a.get("model"),
            "max_concurrent_tasks": cap,
            "active_load": active_load,
            "headroom": headroom,
            "overloaded": bool(overloaded),
            "tasks_24h": total_24h,
            "completed_24h": completed,
            "failed_24h": failed,
            "fail_pct_24h": fail_pct,
            "success_pct_24h": success_pct,
            "last_success_at": last_success_ts.isoformat() if last_success_ts else None,
            "last_success_age_h": last_success_age_h,
            "no_success_too_long": bool(no_success_too_long),
        })

    return {
        "per_agent": per_agent,
        "long_running_tasks": long_running,
        "running_total": len(running_tasks),
        "lookback_hours": LOOKBACK_HOURS,
        "long_running_min": LONG_RUNNING_MIN,
    }


# ─── state helpers ────────────────────────────────────────────────────────

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


# ─── main ─────────────────────────────────────────────────────────────────

def main() -> int:
    start = now_utc()
    state = load_state()
    dedup = load_dedup()

    # S1
    agents = probe_agents()
    if not agents.get("agents") and agents.get("rc") not in (None, 0):
        snap = {
            "ts_utc": start.isoformat(),
            "ts_epoch": int(start.timestamp()),
            "verdict": "no-op",
            "reason": "agent list not reachable; downstream monitors own recovery",
            "agents_probe": agents,
        }
        save_state({**state, **snap})
        LAST_SNAPSHOT.write_text(json.dumps(snap, indent=2, default=str))
        print(json.dumps(snap, indent=2, default=str))
        return 0

    # S2 — global task counts per status
    flow = probe_task_flow()

    # S3 / S5 — running tasks
    running_tasks = fetch_all_running()

    # S3 / S4 / S5 — per-agent aggregate
    per_agent = probe_per_agent(agents.get("agents", []), running_tasks)

    # Verdicts
    escalations: list[str] = []
    warnings: list[str] = []

    overloaded_agents = [a for a in per_agent["per_agent"] if a["overloaded"]]
    if overloaded_agents:
        names = ", ".join(f"{a['name']}({a['active_load']}/{a['max_concurrent_tasks']})" for a in overloaded_agents)
        escalations.append(f"{len(overloaded_agents)} agents overloaded: {names}")

    high_fail_agents = [a for a in per_agent["per_agent"]
                        if a["tasks_24h"] >= 3 and a["fail_pct_24h"] > FAIL_RATE_ESCALATE_PCT]
    if high_fail_agents:
        names = ", ".join(f"{a['name']}({a['fail_pct_24h']}%)" for a in high_fail_agents)
        escalations.append(f"{len(high_fail_agents)} agents fail% > {FAIL_RATE_ESCALATE_PCT}% over 24h: {names}")

    warn_fail_agents = [a for a in per_agent["per_agent"]
                        if FAIL_RATE_WARN_PCT < a["fail_pct_24h"] <= FAIL_RATE_ESCALATE_PCT and a["tasks_24h"] >= 3]
    if warn_fail_agents:
        names = ", ".join(f"{a['name']}({a['fail_pct_24h']}%)" for a in warn_fail_agents)
        warnings.append(f"{len(warn_fail_agents)} agents fail% in ({FAIL_RATE_WARN_PCT}, {FAIL_RATE_ESCALATE_PCT}] over 24h: {names}")

    long_running = per_agent.get("long_running_tasks") or []
    if long_running:
        worst = max(long_running, key=lambda x: x["age_min"])
        warnings.append(
            f"{len(long_running)} long-running tasks (>{LONG_RUNNING_MIN}m); worst={worst['age_min']}m "
            f"(agent={worst['agent_id'][:8]}, task={worst['task_id'][:8]})"
        )

    idle_no_success = [a for a in per_agent["per_agent"]
                       if a["active_load"] == 0 and a["no_success_too_long"]]
    if idle_no_success:
        names = ", ".join(f"{a['name']}({a['last_success_age_h']}h)" for a in idle_no_success)
        warnings.append(f"{len(idle_no_success)} agents idle with no success in >{NO_SUCCESS_DAYS}d: {names}")

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
        "agents_probe": {
            "total": agents.get("total"),
            "active_count": agents.get("active_count"),
            "archived_count": agents.get("archived_count"),
            "sum_max_concurrent_tasks": agents.get("sum_max_concurrent_tasks"),
        },
        "task_flow": flow,
        "per_agent": per_agent["per_agent"],
        "long_running_tasks": per_agent["long_running_tasks"],
        "running_total": per_agent["running_total"],
        "thresholds": {
            "long_running_min": LONG_RUNNING_MIN,
            "fail_rate_warn_pct": FAIL_RATE_WARN_PCT,
            "fail_rate_escalate_pct": FAIL_RATE_ESCALATE_PCT,
            "overload_factor": OVERLOAD_FACTOR,
            "no_success_days": NO_SUCCESS_DAYS,
            "lookback_hours": LOOKBACK_HOURS,
        },
    }

    snap_path = DASHBOARD_DIR / f"agent-{start.strftime('%Y-%m-%dT%H-%M-%S')}Z.json"
    snap_path.write_text(json.dumps(snap, indent=2, default=str))
    LAST_SNAPSHOT.write_text(json.dumps(snap, indent=2, default=str))
    save_state({**state, **snap, "last_snapshot_path": str(snap_path)})

    print(json.dumps(snap, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())