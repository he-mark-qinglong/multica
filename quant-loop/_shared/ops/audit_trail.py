"""Runtime audit trail (H20).

Every state transition of the live system — startup, config change,
position open/close, kill switch, shutdown — is appended as one
immutable JSON line to ``audit.jsonl``. Each record captures:

  * **actor** — "auto" (the system decided) or "manual" (a human did);
    attribution of who/what initiated a transition is the first question
    in every incident review.
  * **before / after** — small state summaries (plain dicts) around the
    transition, so the trail alone can answer "what actually changed".
  * **kind + strategy** — what happened and to whom.

Reads are pure queries over the loaded trail: ``tail`` for the most
recent records, ``diff`` for the key-level delta of one record's
before/after summaries.

References:
- SOX / MiFID II record-keeping requirements — reconstructable,
  tamper-evident (append-only) sequence of material system events.
- Google SRE Workbook, ch. 9 "Incident Response" — the audit timeline as
  the backbone of post-incident analysis.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


class TransitionKind(str, Enum):
    """Auditable state transitions."""

    START = "START"                  # process/strategy startup
    CONFIG_CHANGE = "CONFIG_CHANGE"  # configuration mutation (hot reload)
    OPEN = "OPEN"                    # position opened
    CLOSE = "CLOSE"                  # position closed
    KILL = "KILL"                    # kill switch latched
    SHUTDOWN = "SHUTDOWN"            # graceful stop


ACTORS = ("auto", "manual")


@dataclass(frozen=True)
class AuditRecord:
    """One immutable state-transition record.

    Attributes:
        ts: transition time, epoch seconds.
        kind: TransitionKind value.
        actor: "auto" | "manual".
        strategy: affected strategy ("" for system-wide).
        before: state summary before the transition (small dict).
        after: state summary after the transition.
        note: free-form human context (ticket id, reason, ...).
    """

    ts: float
    kind: str
    actor: str
    strategy: str = ""
    before: Mapping[str, Any] = field(default_factory=dict)
    after: Mapping[str, Any] = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        TransitionKind(self.kind)  # fail at construction, not at read time
        if self.actor not in ACTORS:
            raise ValueError(f"actor must be one of {ACTORS}, got {self.actor!r}")

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "AuditRecord":
        return AuditRecord(
            ts=float(d["ts"]),
            kind=str(d["kind"]),
            actor=str(d["actor"]),
            strategy=str(d.get("strategy", "")),
            before=d.get("before") or {},
            after=d.get("after") or {},
            note=str(d.get("note", "")),
        )


def diff_summary(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> Dict[str, Tuple[Any, Any]]:
    """Key-level delta between two state summaries. Pure.

    Returns {key: (old, new)} for keys that were added, removed, or
    changed; removed keys have old value and None as new, added keys the
    reverse. An empty dict means the transition changed nothing visible
    in the summaries.
    """
    diff: Dict[str, Tuple[Any, Any]] = {}
    for key in before.keys() | after.keys():
        old = before.get(key)
        new = after.get(key)
        if key not in before:
            diff[key] = (None, new)
        elif key not in after:
            diff[key] = (old, None)
        elif old != new:
            diff[key] = (old, new)
    return diff


def diff_record(record: AuditRecord) -> Dict[str, Tuple[Any, Any]]:
    """The before->after delta of one record. Pure."""
    return diff_summary(record.before, record.after)


def append_record(path, record: AuditRecord) -> AuditRecord:
    """Append one record as a JSON line; returns the record written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(record.to_json() + "\n")
    return record


def load_trail(path) -> Tuple[AuditRecord, ...]:
    """Load the whole trail; empty tuple if absent. Corrupt lines skipped."""
    path = Path(path)
    if not path.exists():
        return ()
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            records.append(AuditRecord.from_dict(payload))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return tuple(records)


def tail(records: Sequence[AuditRecord], n: int = 10) -> Tuple[AuditRecord, ...]:
    """The newest ``n`` records, oldest-first. Pure."""
    if n < 0:
        raise ValueError("n must be >= 0")
    return tuple(records[-n:]) if n else ()


def query_trail(
    records: Sequence[AuditRecord],
    kind: Optional[str] = None,
    actor: Optional[str] = None,
    strategy: Optional[str] = None,
    start_ts: Optional[float] = None,
    end_ts: Optional[float] = None,
) -> Tuple[AuditRecord, ...]:
    """Filter the trail by kind / actor / strategy / time window. Pure."""
    if kind is not None:
        kind = TransitionKind(kind).value
    out = []
    for r in records:
        if kind is not None and r.kind != kind:
            continue
        if actor is not None and r.actor != actor:
            continue
        if strategy is not None and r.strategy != strategy:
            continue
        if start_ts is not None and r.ts < start_ts:
            continue
        if end_ts is not None and r.ts > end_ts:
            continue
        out.append(r)
    return tuple(out)
