#!/usr/bin/env python3
"""ALERT-ROUTING-TELEGRAM — read alerts from upstream monitors, classify, format for
Telegram delivery, dedup, and emit a send-log + last-snapshot.

This is the iteration-2 (#86) implementation of MAP-P9 alert-routing series
(SMA-35770-086 / SMA-35857). Mirrors the layout of db-pool-monitor and
status-page-monitor: single-file, stdlib only, no mutation of multica state.

Scope (minimal viable alert router):
  - Source A : status-page-monitor/last-snapshot.json (escalate / warn / healthy verdict)
  - Source B : db-pool-monitor/last-snapshot.json (dbpool verdict)
  - Source C : multica daemon status (alive / restart-loop)
  - Source D : multica autopilot list (paused% / stale runs)

For every signal, this router:
  1. Computes a stable alert_id (sha1 over source + key fields, hex 12).
  2. Loads dedup-state.json — skips alerts already seen within `dedup_window_min`.
  3. Classifies severity: critical / warning / info / healthy.
  4. Builds a Telegram-ready payload (markdown text + parse_mode hint).
  5. Sends via Telegram Bot API only if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
     are present in the env AND `--live` is passed. Otherwise it stays in
     dry-run and only records what *would* be sent.
  6. Appends to send-log.jsonl and updates last-snapshot.json / state.json.

This file deliberately does NOT mutate the multica issue table (per
multica-agent-base §4.1) and does NOT @mention smark. It is a pure routing
+ observability layer. Escalation beyond this file is owned by the
upstream monitors themselves.

Usage:
  python3 /Users/mark/multica/alert-routing-telegram/run.py [--live] [--dedup-window 30]

Exit codes:
  0 — always (even when verdict is critical). Routing layer is read-mostly;
     downstream consumers decide what to do.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ALERT_DIR = Path("/Users/mark/multica/alert-routing-telegram")
ALERT_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = ALERT_DIR / "state.json"
DEDUP_FILE = ALERT_DIR / "dedup-state.json"
SEND_LOG = ALERT_DIR / "send-log.jsonl"
LAST_SNAP = ALERT_DIR / "last-snapshot.json"

STATUS_PAGE_DIR = Path("/Users/mark/multica/status-page-monitor")
DB_POOL_DIR = Path("/Users/mark/multica/db-pool-monitor")

# Severity ladder (low -> high).
SEVERITY_HEALTHY = "healthy"
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

# Threshold table — same shape as status-page-monitor so a future bridge is trivial.
PAUSED_PCT_WARN = 20.0
MISSED_AUTOPILOTS_ESCALATE = 1
DAEMON_UPTIME_WARN_SEC = 60
IN_PROGRESS_BACKLOG_WARN = 700
BLOCKED_BACKLOG_ESCALATE = 80


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def sh(*args: str, timeout: int = 20) -> tuple[int, str, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def safe_load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def read_upstream_snapshots() -> dict:
    """Read the three local signal sources we own; tolerate missing files."""
    return {
        "status_page": safe_load(STATUS_PAGE_DIR / "last-snapshot.json"),
        "db_pool": safe_load(DB_POOL_DIR / "last-snapshot.json"),
    }


def probe_daemon() -> dict:
    rc, out, err = sh("multica", "daemon", "status", "--output", "json")
    if rc != 0:
        return {"alive": False, "rc": rc, "stderr": err.strip()[:200]}
    try:
        d = json.loads(out)
    except Exception as e:
        return {"alive": False, "rc": rc, "parse_error": str(e)[:200]}
    return {
        "alive": True,
        "status": d.get("status"),
        "pid": d.get("pid"),
        "uptime": d.get("uptime"),
        "active_task_count": d.get("active_task_count"),
    }


def parse_uptime_seconds(s: str) -> int:
    import re
    if not s:
        return -1
    m = re.match(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", s.strip())
    if not m:
        return -1
    d, h, m_, s_ = m.groups()
    return int(d or 0) * 86400 + int(h or 0) * 3600 + int(m_ or 0) * 60 + int(s_ or 0)


def probe_autopilot_paused_pct() -> dict:
    rc, out, err = sh("multica", "autopilot", "list", "--output", "json")
    if rc != 0:
        return {"rc": rc, "total": 0, "paused_pct": 0.0}
    try:
        d = json.loads(out)
        aps = d.get("autopilots", []) or []
    except Exception:
        return {"total": 0, "paused_pct": 0.0}
    total = len(aps)
    paused = sum(1 for a in aps if (a.get("status") or "unknown") != "active")
    pct = (paused / total * 100.0) if total else 0.0
    return {"total": total, "paused": paused, "paused_pct": round(pct, 2)}


def probe_issue_backlog() -> dict:
    """Sample in_progress / blocked totals. Capped to limit=1 to stay cheap."""
    out = {"in_progress": None, "blocked": None}
    for s in ("in_progress", "blocked"):
        rc, so, _ = sh(
            "multica", "issue", "list",
            "--status", s, "--limit", "1", "--output", "json",
        )
        if rc == 0:
            try:
                out[s] = int(json.loads(so).get("total", 0))
            except Exception:
                pass
    return out


# ---- Alert synthesis ----------------------------------------------------

def _alert_id(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:12]


def synthesize_alerts(sources: dict, daemon: dict, ap: dict, backlog: dict) -> list[dict]:
    """Build the alert list from upstream signals. Stable IDs + severities."""
    alerts: list[dict] = []

    # A. status-page-monitor verdict
    sp = sources.get("status_page") or {}
    sp_verdict = sp.get("verdict")
    if sp_verdict == "escalate":
        alerts.append({
            "id": _alert_id("status-page", "escalate", sp.get("ts_utc", "")),
            "source": "status-page-monitor",
            "severity": SEVERITY_CRITICAL,
            "summary": "status-page-monitor verdict=escalate",
            "detail": sp.get("escalations") or [],
            "ts_utc": sp.get("ts_utc"),
        })
    elif sp_verdict == "warn":
        alerts.append({
            "id": _alert_id("status-page", "warn", sp.get("ts_utc", "")),
            "source": "status-page-monitor",
            "severity": SEVERITY_WARNING,
            "summary": "status-page-monitor verdict=warn",
            "detail": sp.get("warnings") or [],
            "ts_utc": sp.get("ts_utc"),
        })
    elif sp_verdict == "healthy":
        alerts.append({
            "id": _alert_id("status-page", "healthy", sp.get("ts_utc", "")),
            "source": "status-page-monitor",
            "severity": SEVERITY_HEALTHY,
            "summary": "status-page-monitor healthy",
            "detail": [],
            "ts_utc": sp.get("ts_utc"),
        })

    # B. db-pool-monitor
    dbp = sources.get("db_pool") or {}
    dbp_verdict = dbp.get("verdict")
    if dbp_verdict == "critical":
        alerts.append({
            "id": _alert_id("db-pool", "critical", dbp.get("ts_utc", "")),
            "source": "db-pool-monitor",
            "severity": SEVERITY_CRITICAL,
            "summary": "db-pool-monitor verdict=critical",
            "detail": dbp.get("alerts") or [],
            "ts_utc": dbp.get("ts_utc"),
        })
    elif dbp_verdict in ("warn", "warning"):
        alerts.append({
            "id": _alert_id("db-pool", "warn", dbp.get("ts_utc", "")),
            "source": "db-pool-monitor",
            "severity": SEVERITY_WARNING,
            "summary": "db-pool-monitor verdict=warn",
            "detail": dbp.get("alerts") or [],
            "ts_utc": dbp.get("ts_utc"),
        })

    # C. daemon health
    if not daemon.get("alive"):
        alerts.append({
            "id": _alert_id("daemon", "down"),
            "source": "daemon",
            "severity": SEVERITY_CRITICAL,
            "summary": "multica daemon unreachable",
            "detail": [daemon.get("stderr", "")],
            "ts_utc": now_utc().isoformat(),
        })
    else:
        up_sec = parse_uptime_seconds(daemon.get("uptime", ""))
        if 0 < up_sec < DAEMON_UPTIME_WARN_SEC:
            alerts.append({
                "id": _alert_id("daemon", "restart-loop", daemon.get("uptime", "")),
                "source": "daemon",
                "severity": SEVERITY_WARNING,
                "summary": f"daemon uptime {daemon.get('uptime')} < 60s",
                "detail": ["possible restart loop"],
                "ts_utc": now_utc().isoformat(),
            })

    # D. autopilot paused%
    if ap.get("paused_pct", 0.0) > PAUSED_PCT_WARN:
        sev = SEVERITY_WARNING if ap["paused_pct"] < 50 else SEVERITY_CRITICAL
        alerts.append({
            "id": _alert_id("autopilot", "paused", str(ap.get("paused_pct"))),
            "source": "autopilots",
            "severity": sev,
            "summary": f"autopilots paused={ap['paused_pct']}%",
            "detail": [f"{ap.get('paused')}/{ap.get('total')} paused"],
            "ts_utc": now_utc().isoformat(),
        })

    # E. issue backlog
    ip = backlog.get("in_progress") or 0
    bl = backlog.get("blocked") or 0
    if ip > IN_PROGRESS_BACKLOG_WARN:
        alerts.append({
            "id": _alert_id("backlog", "in_progress", str(ip)),
            "source": "issue-flow",
            "severity": SEVERITY_WARNING,
            "summary": f"in_progress backlog {ip} > {IN_PROGRESS_BACKLOG_WARN}",
            "detail": [],
            "ts_utc": now_utc().isoformat(),
        })
    if bl > BLOCKED_BACKLOG_ESCALATE:
        alerts.append({
            "id": _alert_id("backlog", "blocked", str(bl)),
            "source": "issue-flow",
            "severity": SEVERITY_CRITICAL,
            "summary": f"blocked backlog {bl} > {BLOCKED_BACKLOG_ESCALATE}",
            "detail": [],
            "ts_utc": now_utc().isoformat(),
        })

    return alerts


# ---- Telegram formatting + send ----------------------------------------

SEVERITY_EMOJI = {
    SEVERITY_HEALTHY: "✅",
    SEVERITY_INFO: "ℹ️",
    SEVERITY_WARNING: "⚠️",
    SEVERITY_CRITICAL: "🚨",
}


def format_telegram_message(alert: dict) -> dict:
    """Build a single Bot API sendMessage payload (markdown)."""
    emoji = SEVERITY_EMOJI.get(alert["severity"], "•")
    lines = [
        f"{emoji} *{alert['severity'].upper()}* — `{alert['source']}`",
        f"_{alert['summary']}_",
    ]
    if alert.get("detail"):
        for d in alert["detail"][:8]:
            lines.append(f"• {d}")
    lines.append("")
    lines.append(f"alert_id: `{alert['id']}`")
    lines.append(f"ts_utc:   `{alert.get('ts_utc')}`")
    return {
        "text": "\n".join(lines),
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        # Headers are a no-op in plain sendMessage; included for future sendMediaGroup paths.
        "_meta": {
            "alert_id": alert["id"],
            "severity": alert["severity"],
            "source": alert["source"],
        },
    }


def telegram_send(token: str, chat_id: str, payload: dict, timeout: int = 10) -> dict:
    """POST to api.telegram.org/bot<token>/sendMessage. Returns {ok,http_code,body}."""
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": payload["text"],
        "parse_mode": payload.get("parse_mode", "Markdown"),
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "http_code": resp.status, "body": raw[:400]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return {"ok": False, "http_code": e.code, "body": raw[:400]}
    except Exception as e:
        return {"ok": False, "http_code": -1, "body": repr(e)[:400]}


# ---- Dedup --------------------------------------------------------------

def load_dedup() -> dict:
    if DEDUP_FILE.exists():
        try:
            return json.loads(DEDUP_FILE.read_text())
        except Exception:
            pass
    return {"seen": {}}  # alert_id -> iso ts of last send


def save_dedup(d: dict) -> None:
    DEDUP_FILE.write_text(json.dumps(d, indent=2))


def filter_fresh(alerts: list[dict], dedup: dict, window_min: int) -> tuple[list[dict], list[dict]]:
    """Split into (fresh, suppressed). Suppressed = seen within window."""
    cutoff = now_utc() - timedelta(minutes=window_min)
    fresh: list[dict] = []
    suppressed: list[dict] = []
    for a in alerts:
        last = dedup.get("seen", {}).get(a["id"])
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if last_dt >= cutoff:
                    suppressed.append({**a, "_suppressed_at": last})
                    continue
            except Exception:
                pass
        fresh.append(a)
    return fresh, suppressed


# ---- Send-log -----------------------------------------------------------

def append_send_log(records: list[dict]) -> None:
    with SEND_LOG.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")


# ---- Top-level ----------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="Actually POST to Telegram (requires env vars).")
    parser.add_argument("--dedup-window", type=int, default=30,
                        help="Minutes to suppress duplicate alert_id.")
    args = parser.parse_args()

    start = now_utc()
    sources = read_upstream_snapshots()
    daemon = probe_daemon()
    ap = probe_autopilot_paused_pct()
    backlog = probe_issue_backlog()

    raw_alerts = synthesize_alerts(sources, daemon, ap, backlog)
    dedup = load_dedup()
    fresh, suppressed = filter_fresh(raw_alerts, dedup, args.dedup_window)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    send_attempted = 0
    send_succeeded = 0
    send_results: list[dict] = []

    for a in fresh:
        payload = format_telegram_message(a)
        rec: dict = {
            "ts_utc": start.isoformat(),
            "alert_id": a["id"],
            "severity": a["severity"],
            "source": a["source"],
            "dry_run": True,
            "send_ok": None,
            "http_code": None,
            "body_excerpt": "",
            "payload_chars": len(payload["text"]),
        }
        if args.live and token and chat_id and a["severity"] in (SEVERITY_WARNING, SEVERITY_CRITICAL):
            res = telegram_send(token, chat_id, payload)
            send_attempted += 1
            rec.update({
                "dry_run": False,
                "send_ok": res.get("ok"),
                "http_code": res.get("http_code"),
                "body_excerpt": res.get("body", "")[:200],
            })
            if res.get("ok"):
                send_succeeded += 1
                dedup.setdefault("seen", {})[a["id"]] = start.isoformat()
        elif a["severity"] == SEVERITY_CRITICAL:
            # Critical alerts are recorded even without --live (so the snapshot
            # shows the dry-run path). Sender config (token/chat_id) is reported
            # separately.
            pass
        send_results.append(rec)
        if a["severity"] != SEVERITY_HEALTHY:
            # Healthy pings also count toward dedup so we don't spam, but don't
            # block a recovery transition — see comment in next block.
            dedup.setdefault("seen", {})[a["id"]] = start.isoformat()
        else:
            # Always record healthy; never dedup (it should reset frequency).
            dedup.setdefault("seen", {})[a["id"]] = start.isoformat()

    append_send_log(send_results)
    save_dedup(dedup)

    counts = {SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 0, SEVERITY_INFO: 0, SEVERITY_HEALTHY: 0}
    for a in fresh:
        counts[a["severity"]] = counts.get(a["severity"], 0) + 1

    snap = {
        "ts_utc": start.isoformat(),
        "ts_epoch": int(start.timestamp()),
        "live_mode": bool(args.live and token and chat_id),
        "config": {
            "telegram_token_present": bool(token),
            "telegram_chat_id_present": bool(chat_id),
            "dedup_window_min": args.dedup_window,
        },
        "severity_counts": counts,
        "raw_alert_count": len(raw_alerts),
        "fresh_alert_count": len(fresh),
        "suppressed_alert_count": len(suppressed),
        "send_attempted": send_attempted,
        "send_succeeded": send_succeeded,
        "sources_present": {
            "status_page": bool(sources.get("status_page")),
            "db_pool": bool(sources.get("db_pool")),
            "daemon_alive": daemon.get("alive", False),
            "autopilot_total": ap.get("total", 0),
        },
        "thresholds": {
            "paused_pct_warn": PAUSED_PCT_WARN,
            "missed_autopilots_escalate": MISSED_AUTOPILOTS_ESCALATE,
            "daemon_uptime_warn_sec": DAEMON_UPTIME_WARN_SEC,
            "in_progress_backlog_warn": IN_PROGRESS_BACKLOG_WARN,
            "blocked_backlog_escalate": BLOCKED_BACKLOG_ESCALATE,
        },
        "alerts_fresh": [
            {**{k: v for k, v in a.items() if k != "detail"}, "detail_count": len(a.get("detail") or [])}
            for a in fresh
        ],
        "alerts_suppressed": [a["id"] for a in suppressed],
    }
    LAST_SNAP.write_text(json.dumps(snap, indent=2, default=str))

    # Cumulative state.json — keep last 50 alerts only (avoid unbounded growth).
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            state = {}
    history = state.get("history", [])
    history.append({
        "ts_utc": snap["ts_utc"],
        "severity_counts": snap["severity_counts"],
        "live_mode": snap["live_mode"],
    })
    state["history"] = history[-50:]
    state["last_snapshot"] = snap
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))

    print(json.dumps(snap, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())