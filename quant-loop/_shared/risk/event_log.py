"""Risk event audit log (D20).

Every risk-relevant event — a breached limit, a kill-switch latch, a
margin call, an exchange liquidation, a live-vs-backtest drift breach, a
missed heartbeat — is appended as one immutable JSON line to an audit
file. Events are frozen dataclasses: once written they are never edited,
which is what makes the file usable as evidence in a post-mortem.

Querying is a pure in-memory filter over the loaded events, so the same
code path serves live tailing and offline analysis.

References:
- Basel Committee on Banking Supervision, "Principles for the Sound
  Management of Operational Risk" (2011) — loss/event data collection as
  the basis of operational-risk control.
- Google SRE Book, ch. 15 "Postmortem Culture" — blameless postmortems
  need a complete, unedited incident timeline.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple


class RiskEventType(str, Enum):
    """Kinds of auditable risk events."""

    LIMIT_BREACH = "LIMIT_BREACH"      # position/loss/vol limit crossed
    KILL_SWITCH = "KILL_SWITCH"        # kill switch latched (manual or auto)
    MARGIN_CALL = "MARGIN_CALL"        # margin ratio crossed maintenance level
    LIQUIDATION = "LIQUIDATION"        # forced liquidation observed/executed
    DRIFT = "DRIFT"                    # live-vs-backtest drift beyond tolerance
    HEARTBEAT = "HEARTBEAT"            # heartbeat missed / process presumed dead


@dataclass(frozen=True)
class RiskEvent:
    """One immutable risk event.

    Attributes:
        ts: event time, epoch seconds.
        event_type: RiskEventType value.
        strategy: originating strategy ("" for account/platform-wide).
        severity: free-form severity, e.g. "INFO" | "WARN" | "CRITICAL".
        message: human-readable one-liner.
        context: structured details (numbers, ids) for later analysis.
    """

    ts: float
    event_type: str
    strategy: str = ""
    severity: str = "WARN"
    message: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate eagerly: a typo'd type must fail at construction, not at
        # query time when the log has already been written.
        RiskEventType(self.event_type)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "RiskEvent":
        return RiskEvent(
            ts=float(d["ts"]),
            event_type=str(d["event_type"]),
            strategy=str(d.get("strategy", "")),
            severity=str(d.get("severity", "WARN")),
            message=str(d.get("message", "")),
            context=d.get("context") or {},
        )


def append_event(path, event: RiskEvent) -> RiskEvent:
    """Append one event as a JSON line; returns the event written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(event.to_json() + "\n")
    return event


def load_events(path) -> Tuple[RiskEvent, ...]:
    """Load all events from a jsonl file; empty tuple if the file is absent.

    Corrupt lines are skipped rather than fatal: an audit log must stay
    readable even after a torn write during a crash.
    """
    path = Path(path)
    if not path.exists():
        return ()
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            events.append(RiskEvent.from_dict(payload))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(events)


def filter_events(
    events: Sequence[RiskEvent],
    event_type: Optional[str] = None,
    strategy: Optional[str] = None,
    start_ts: Optional[float] = None,
    end_ts: Optional[float] = None,
) -> Tuple[RiskEvent, ...]:
    """Filter events by type / strategy / time window [start_ts, end_ts]. Pure.

    ``event_type`` may be a RiskEventType or its string value; None means
    "no constraint" for every parameter.
    """
    if event_type is not None:
        event_type = RiskEventType(event_type).value
    out = []
    for e in events:
        if event_type is not None and e.event_type != event_type:
            continue
        if strategy is not None and e.strategy != strategy:
            continue
        if start_ts is not None and e.ts < start_ts:
            continue
        if end_ts is not None and e.ts > end_ts:
            continue
        out.append(e)
    return tuple(out)


def query_events(
    path,
    event_type: Optional[str] = None,
    strategy: Optional[str] = None,
    start_ts: Optional[float] = None,
    end_ts: Optional[float] = None,
) -> Tuple[RiskEvent, ...]:
    """Load + filter in one call."""
    return filter_events(
        load_events(path),
        event_type=event_type,
        strategy=strategy,
        start_ts=start_ts,
        end_ts=end_ts,
    )
