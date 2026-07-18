#!/usr/bin/env python3
"""DB-POOL-MONITOR autopilot execution script."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

AUTOPILOT_ID = "c8ec222b-1f58-429a-b58d-7014da99f395"
AUTOPILOT_RUN_ID = os.environ.get("MULTICA_AUTOPILOT_RUN_ID", os.environ.get("AUTOPILOT_RUN_ID", "unknown"))
STATE_DIR = Path("/home/smark/multica/db-pool-monitor")
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "state.json"
DEDUP_FILE = STATE_DIR / "dedup-state.json"

DB_HOST_SPEC = "192.168.0.105"
DB_HOST_FALLBACK = "127.0.0.1"
DB_PORT = 5432
DB_NAME = "multica"
DB_USER = "multica"
DB_PASSWORD = "multica"
APP_POOL_MAX = 25

QUERY = """
SELECT
  count(*) FILTER (WHERE state = 'idle') AS idle,
  count(*) FILTER (WHERE state = 'active') AS active,
  count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_tx,
  count(*) FILTER (WHERE state = 'idle in transaction (aborted)') AS idle_in_tx_aborted,
  count(*) FILTER (WHERE xact_start < now() - interval '30 seconds' AND state LIKE 'idle in transaction%') AS stuck_in_tx,
  max(extract(epoch from (now() - xact_start))) FILTER (WHERE state LIKE 'idle in transaction%') AS oldest_tx_age_sec,
  count(*) FILTER (WHERE state = 'fastpath function call') AS fastpath,
  count(*) FILTER (WHERE state LIKE 'active%' AND query_start < now() - interval '60 seconds') AS slow_queries
FROM pg_stat_activity
WHERE datname = current_database();
"""

TOP_ACTIVITY_QUERY = """
SELECT pid, state, application_name, client_addr,
       extract(epoch from (now() - xact_start)) as tx_age_sec,
       extract(epoch from (now() - query_start)) as query_age_sec,
       left(query, 120) as query_head
FROM pg_stat_activity
WHERE datname = current_database()
ORDER BY CASE WHEN state LIKE 'idle in transaction%' THEN 0 ELSE 1 END,
         xact_start NULLS LAST,
         query_start NULLS LAST
LIMIT 10;
"""

PG_SETTINGS_QUERY = """
SELECT setting::int AS max_connections FROM pg_settings WHERE name='max_connections';
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def run_psql(host: str, query: str) -> tuple[bool, str]:
    env = os.environ.copy()
    env["PGHOST"] = host
    env["PGPORT"] = str(DB_PORT)
    env["PGDATABASE"] = DB_NAME
    env["PGUSER"] = DB_USER
    env["PGPASSWORD"] = DB_PASSWORD
    env["PGSSLMODE"] = "disable"
    proc = subprocess.run(
        ["psql", "-At", "-F", "|", "-c", query],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        return False, proc.stderr.strip()
    return True, proc.stdout.strip()


def host_reachable(host: str, port: int) -> bool:
    try:
        subprocess.run(
            ["timeout", "2", "bash", "-c", f"</dev/tcp/{host}/{port}"],
            capture_output=True,
            timeout=5,
            check=True,
        )
        return True
    except Exception:
        return False


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def load_dedup() -> dict:
    if DEDUP_FILE.exists():
        try:
            return json.loads(DEDUP_FILE.read_text())
        except Exception:
            pass
    return {}


def save_dedup(dedup: dict) -> None:
    DEDUP_FILE.write_text(json.dumps(dedup, indent=2, default=str))


def parse_query_output(stdout: str) -> dict:
    # psql -At -F '|' returns one row like: 15|1|0|0|0||0|0
    parts = stdout.split("|")
    keys = ["idle", "active", "idle_in_tx", "idle_in_tx_aborted", "stuck_in_tx", "oldest_tx_age_sec", "fastpath", "slow_queries"]
    result = {}
    for i, key in enumerate(keys):
        val = parts[i].strip() if i < len(parts) and parts[i].strip() != "" else None
        try:
            result[key] = int(val) if val is not None and key != "oldest_tx_age_sec" else (float(val) if val is not None else None)
        except ValueError:
            result[key] = val
    return result


def parse_top_activity(stdout: str) -> list:
    rows = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        rows.append({
            "pid": parts[0] if len(parts) > 0 else None,
            "state": parts[1] if len(parts) > 1 else None,
            "application_name": parts[2] if len(parts) > 2 else None,
            "client_addr": parts[3] if len(parts) > 3 else None,
            "tx_age_sec": float(parts[4]) if len(parts) > 4 and parts[4] else None,
            "query_age_sec": float(parts[5]) if len(parts) > 5 and parts[5] else None,
            "query_head": parts[6] if len(parts) > 6 else None,
        })
    return rows


def get_prior_run_age_sec() -> float | None:
    """Return seconds since the previous completed autopilot run (before current)."""
    try:
        proc = subprocess.run(
            ["multica", "autopilot", "runs", AUTOPILOT_ID, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        runs = data.get("runs", [])
        # Exclude the current run
        current_id = AUTOPILOT_RUN_ID
        prior_runs = [r for r in runs if r.get("id") != current_id and r.get("created_at")]
        if not prior_runs:
            return None
        prior = prior_runs[0]
        prior_ts = datetime.fromisoformat(prior["created_at"])
        if prior_ts.tzinfo is None:
            prior_ts = prior_ts.replace(tzinfo=timezone.utc)
        return (now_utc() - prior_ts).total_seconds()
    except Exception:
        return None


def daemon_alive() -> tuple[bool, int | None]:
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "multica daemon"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            pid = int(proc.stdout.strip().splitlines()[0])
            return True, pid
    except Exception:
        pass
    return False, None


def create_issue(title: str, body: str) -> str | None:
    desc_path = STATE_DIR / "issue_description.md"
    desc_path.write_text(body)
    try:
        proc = subprocess.run(
            ["multica", "issue", "create", "--title", title, "--description-file", str(desc_path), "--priority", "urgent", "--output", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            print(f"issue create failed: {proc.stderr}", file=sys.stderr)
            return None
        data = json.loads(proc.stdout)
        return data.get("issue", {}).get("id")
    except Exception as e:
        print(f"issue create exception: {e}", file=sys.stderr)
        return None


def main() -> int:
    start_ts = now_utc()
    state = load_state()
    dedup = load_dedup()

    alive, daemon_pid = daemon_alive()
    if not alive:
        result = {
            "verdict": "no-op",
            "reason": "daemon not running; DEPLOY-FAIL-DETECT owns this case",
            "daemon_alive": False,
        }
        save_state({**state, **result, "ts_utc": start_ts.isoformat()})
        print(json.dumps(result, indent=2, default=str))
        return 0

    prior_age = get_prior_run_age_sec()
    if prior_age is not None and prior_age < 100:
        result = {
            "verdict": "no-op",
            "reason": f"prior run {prior_age:.0f}s ago (< 100s guard)",
            "daemon_alive": True,
            "daemon_pid": daemon_pid,
        }
        save_state({**state, **result, "ts_utc": start_ts.isoformat()})
        print(json.dumps(result, indent=2, default=str))
        return 0

    # Pick DB host
    if host_reachable(DB_HOST_SPEC, DB_PORT):
        db_host = DB_HOST_SPEC
        host_note = f"spec host {DB_HOST_SPEC}:{DB_PORT} reachable"
    else:
        db_host = DB_HOST_FALLBACK
        host_note = f"spec host {DB_HOST_SPEC}:{DB_PORT} unreachable; fallback to {DB_HOST_FALLBACK}:{DB_PORT}"

    ok, db_out = run_psql(db_host, QUERY)
    if not ok:
        result = {
            "verdict": "error",
            "reason": f"psql failed: {db_out}",
            "daemon_alive": True,
            "daemon_pid": daemon_pid,
            "db_host_used": db_host,
        }
        save_state({**state, **result, "ts_utc": start_ts.isoformat()})
        print(json.dumps(result, indent=2, default=str))
        return 1

    metrics = parse_query_output(db_out)

    # Get max_connections from Postgres
    ok2, pg_settings_out = run_psql(db_host, PG_SETTINGS_QUERY)
    pg_max_conns = int(pg_settings_out.strip()) if ok2 and pg_settings_out.strip().isdigit() else 100

    # Estimate acquired app pool conns = active + idle_in_tx variants
    acquired_app = (
        metrics.get("active", 0)
        + metrics.get("idle_in_tx", 0)
        + metrics.get("idle_in_tx_aborted", 0)
    )
    utilization_pct = (acquired_app / APP_POOL_MAX) * 100 if APP_POOL_MAX else 0

    # Judgments
    pool_near_capacity = utilization_pct > 80
    stuck_tx = metrics.get("stuck_in_tx", 0) > 0
    slow_query_storm = metrics.get("slow_queries", 0) > 5
    oldest_tx_age = metrics.get("oldest_tx_age_sec")
    long_tx = oldest_tx_age is not None and oldest_tx_age > 60

    escalations = []
    warnings = []
    if pool_near_capacity:
        warnings.append("pool near capacity")
    if stuck_tx:
        escalations.append("pg stuck transaction")
    if slow_query_storm:
        warnings.append("pg slow query storm")
    if long_tx:
        escalations.append("pg long transaction")

    issue_id = None
    now_epoch = int(start_ts.timestamp())

    if escalations:
        # Dedup: same metric within 5 min
        fresh_escalations = []
        for e in escalations:
            last = dedup.get(e)
            if last is not None and (now_epoch - last) < 300:
                continue
            fresh_escalations.append(e)

        if fresh_escalations:
            # Fetch top activity detail
            ok3, top_out = run_psql(db_host, TOP_ACTIVITY_QUERY)
            top_activity = parse_top_activity(top_out) if ok3 else []

            title = f"[DB-POOL-MONITOR] {', '.join(fresh_escalations)}"
            body_lines = [
                f"**ESCALATE-TO-SMARK** triggered by DB-POOL-MONITOR at {start_ts.isoformat()}",
                "",
                "**Triggered metrics**:",
                ", ".join(f"`{e}`" for e in fresh_escalations),
                "",
                "**Pool snapshot**:",
                f"- acquired (app estimate): {acquired_app}/{APP_POOL_MAX} ({utilization_pct:.1f}%)",
                f"- idle: {metrics.get('idle')}, active: {metrics.get('active')}",
                f"- idle_in_tx: {metrics.get('idle_in_tx')}, idle_in_tx_aborted: {metrics.get('idle_in_tx_aborted')}",
                f"- stuck_in_tx: {metrics.get('stuck_in_tx')}",
                f"- oldest_tx_age_sec: {metrics.get('oldest_tx_age_sec')}",
                f"- slow_queries: {metrics.get('slow_queries')}",
                f"- fastpath: {metrics.get('fastpath')}",
                f"- pg_max_connections: {pg_max_conns}",
                "",
                f"**DB host**: {db_host} ({host_note})",
                f"**Daemon pid**: {daemon_pid}",
                "",
                "**Top 10 pg_stat_activity rows**:",
                "```json",
                json.dumps(top_activity, indent=2, default=str),
                "```",
            ]
            issue_id = create_issue(title, "\n".join(body_lines))
            for e in fresh_escalations:
                dedup[e] = now_epoch
            save_dedup(dedup)

    verdict = "escalate" if escalations else ("warn" if warnings else "no-op")

    snapshot = {
        "run_id": AUTOPILOT_RUN_ID,
        "ts_epoch": now_epoch,
        "ts_utc": start_ts.isoformat(),
        "autopilot_id": AUTOPILOT_ID,
        "host_used": f"{db_host}:{DB_PORT}",
        "host_spec_note": host_note,
        "daemon_alive": alive,
        "daemon_pid": daemon_pid,
        "skip_guard": {
            "prior_run_age_sec": prior_age,
            "min_gap_sec": 100,
            "passed": prior_age is None or prior_age >= 100,
        },
        "metrics": metrics,
        "pool_util_pct": round(utilization_pct, 2),
        "acquired_app_estimate": acquired_app,
        "app_pool_max": APP_POOL_MAX,
        "pg_max_connections": pg_max_conns,
        "judgments": {
            "pool_near_capacity": pool_near_capacity,
            "stuck_tx": stuck_tx,
            "long_tx": long_tx,
            "slow_query_storm": slow_query_storm,
        },
        "escalations": escalations,
        "warnings": warnings,
        "issue_id": issue_id,
        "verdict": verdict,
    }

    snapshot_path = STATE_DIR / f"dbpool-{start_ts.strftime('%Y-%m-%dT%H-%M-%S')}Z.json"
    snapshot_path.write_text(json.dumps(snapshot, indent=2, default=str))
    last_snapshot_path = STATE_DIR / "last-snapshot.json"
    last_snapshot_path.write_text(json.dumps(snapshot, indent=2, default=str))

    save_state({
        **state,
        **snapshot,
        "last_snapshot_path": str(snapshot_path),
    })

    print(json.dumps(snapshot, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
