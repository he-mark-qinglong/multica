"""Heartbeat writer + timeout watcher (H14, H15).

The strategy process periodically writes a small JSON beat file
({"ts", "state", ...}); a separate watcher checks the file's freshness and
raises a CRITICAL alert when the beat goes stale — the classic dead-man's
switch for detecting wedged or dead trading processes.

References:
- Nygard, "Release It!", ch. 5 "Stability Patterns" — heartbeat / dead-man
  switch for detecting hung processes that still hold resources.
- Google SRE Book, ch. 6 — white-box vs black-box monitoring; the beat file
  is the white-box complement to external process checks.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from _shared.ops.alerting import Alert, AlertLevel


def write_beat(
    path,
    state: str = "running",
    ts: Optional[float] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> float:
    """Atomically write a heartbeat file; returns the beat timestamp.

    Atomic via tmp-file + rename so the watcher never reads a torn write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    beat_ts = time.time() if ts is None else float(ts)
    payload = {"ts": beat_ts, "state": state, **dict(extra or {})}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)
    return beat_ts


def read_beat(path) -> Optional[Mapping[str, Any]]:
    """Read the beat file; None if missing or corrupt."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "ts" not in payload:
        return None
    return payload


@dataclass(frozen=True)
class HeartbeatStatus:
    """Result of one freshness check."""

    alive: bool
    age_sec: float          # seconds since last beat; +inf if never seen
    last_ts: Optional[float]
    state: str              # beat's self-reported state; "missing" if unreadable
    timeout_sec: float


def check_heartbeat(
    path,
    timeout_sec: float,
    now: Optional[float] = None,
) -> HeartbeatStatus:
    """Pure freshness check: alive iff the last beat is within timeout_sec."""
    now_ts = time.time() if now is None else float(now)
    beat = read_beat(path)
    if beat is None:
        return HeartbeatStatus(
            alive=False, age_sec=float("inf"), last_ts=None,
            state="missing", timeout_sec=float(timeout_sec),
        )
    last_ts = float(beat["ts"])
    age = now_ts - last_ts
    return HeartbeatStatus(
        alive=age <= timeout_sec,
        age_sec=age,
        last_ts=last_ts,
        state=str(beat.get("state", "unknown")),
        timeout_sec=float(timeout_sec),
    )


def heartbeat_alert(
    status: HeartbeatStatus,
    process: str = "strategy",
    now: Optional[float] = None,
) -> Optional[Alert]:
    """Map a stale status to a CRITICAL alert; None when alive (H15)."""
    if status.alive:
        return None
    ts = time.time() if now is None else float(now)
    if status.last_ts is None:
        msg = f"process {process} has never written a heartbeat"
    else:
        msg = (
            f"process {process} heartbeat stale: {status.age_sec:.1f}s "
            f"since last beat (timeout {status.timeout_sec:.1f}s)"
        )
    return Alert(
        ts=ts,
        level=AlertLevel.CRITICAL.value,
        rule="heartbeat_timeout",
        message=msg,
        context={
            "process": process,
            "age_sec": status.age_sec,
            "last_ts": status.last_ts,
            "state": status.state,
            "timeout_sec": status.timeout_sec,
        },
    )


@dataclass(frozen=True)
class HeartbeatWatcher:
    """Convenience wrapper combining check_heartbeat + heartbeat_alert."""

    path: Path
    timeout_sec: float
    process: str = "strategy"

    def check(self, now: Optional[float] = None) -> Optional[Alert]:
        status = check_heartbeat(self.path, self.timeout_sec, now)
        return heartbeat_alert(status, self.process, now)
