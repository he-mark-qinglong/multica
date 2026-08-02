"""Structured alerting with pluggable sinks (H5).

Alert rules are pure functions returning Optional[Alert]; an Alerter fans an
alert out to any number of sinks (log file, webhook POST). Kept dependency-free
(stdlib urllib) so it works inside the paper runner without extra packages.

References:
- Google SRE Book, ch. 10 "Practical Alerting" — alert on symptoms, page only
  on user-facing impact; CRITICAL here means "needs a human now".
- Rob Ewaschuk, "My Philosophy on Alerting" (SRE book companion doc).
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence, Tuple


class AlertLevel(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class Alert:
    """One immutable structured alert."""

    ts: float
    level: str                # AlertLevel value
    rule: str                 # rule name that fired, e.g. "drawdown"
    message: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)


# --- Sinks ------------------------------------------------------------------
class AlertSink(Protocol):
    def emit(self, alert: Alert) -> None: ...


class LogFileSink:
    """Appends one JSON alert per line to a file."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, alert: Alert) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(alert.to_json() + "\n")


class WebhookSink:
    """POSTs the alert as JSON to a webhook URL (Slack/Discord-compatible)."""

    def __init__(self, url: str, timeout_sec: float = 5.0):
        self.url = url
        self.timeout_sec = timeout_sec

    def emit(self, alert: Alert) -> None:
        payload = alert.to_json().encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec):
            pass


class Alerter:
    """Dispatches alerts to all sinks; a sink failure never blocks the rest."""

    def __init__(self, sinks: Sequence[AlertSink] = ()):
        self.sinks: Tuple[AlertSink, ...] = tuple(sinks)
        self.failures: int = 0

    def dispatch(self, alert: Alert) -> Alert:
        for sink in self.sinks:
            try:
                sink.emit(alert)
            except Exception:
                self.failures += 1
        return alert

    def evaluate(self, *alerts: Optional[Alert]) -> Tuple[Alert, ...]:
        """Dispatch every non-None rule result; returns what was dispatched."""
        fired = tuple(a for a in alerts if a is not None)
        for a in fired:
            self.dispatch(a)
        return fired


# --- Rules (pure) -----------------------------------------------------------
def check_drawdown(
    peak_equity: float,
    current_equity: float,
    threshold_pct: float,
    now: Optional[float] = None,
) -> Optional[Alert]:
    """CRITICAL if drawdown from peak exceeds threshold_pct (e.g. 10.0 = 10%)."""
    if peak_equity <= 0:
        return None
    dd_pct = (peak_equity - current_equity) / peak_equity * 100.0
    if dd_pct <= threshold_pct:
        return None
    return Alert(
        ts=time.time() if now is None else float(now),
        level=AlertLevel.CRITICAL.value,
        rule="drawdown",
        message=f"drawdown {dd_pct:.2f}% exceeds threshold {threshold_pct:.2f}%",
        context={
            "peak_equity": peak_equity,
            "current_equity": current_equity,
            "drawdown_pct": dd_pct,
            "threshold_pct": threshold_pct,
        },
    )


def check_kill_switch(
    kill_triggered: bool,
    reason: str = "",
    now: Optional[float] = None,
) -> Optional[Alert]:
    """CRITICAL whenever the runner's kill switch has latched."""
    if not kill_triggered:
        return None
    return Alert(
        ts=time.time() if now is None else float(now),
        level=AlertLevel.CRITICAL.value,
        rule="kill_switch",
        message=f"kill switch triggered: {reason or 'no reason given'}",
        context={"reason": reason},
    )


def check_data_gap(
    last_data_ts: float,
    now_ts: float,
    max_gap_sec: float,
    feed: str = "market_data",
) -> Optional[Alert]:
    """CRITICAL if no data has arrived for more than max_gap_sec seconds."""
    gap = float(now_ts) - float(last_data_ts)
    if gap <= max_gap_sec:
        return None
    return Alert(
        ts=float(now_ts),
        level=AlertLevel.CRITICAL.value,
        rule="data_gap",
        message=f"feed {feed} silent for {gap:.1f}s (limit {max_gap_sec:.1f}s)",
        context={"feed": feed, "gap_sec": gap, "max_gap_sec": max_gap_sec},
    )
