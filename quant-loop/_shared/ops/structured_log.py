"""JSON structured logger (H6).

One JSON object per line: {"ts", "level", "event", "data"}. Events are
constrained to the EventType enum so downstream log ingestion can rely on a
closed vocabulary instead of free-form strings.

Design references:
- Google SRE Workbook, ch. 17 "Identifying and Recovering from Overload" —
  structured logs as the substrate for alerting.
- "The Twelve-Factor App", XI. Logs — treat logs as event streams (append-only
  lines on stdout / a file), never as managed files with rotation logic in-app.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, TextIO, Union


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class EventType(str, Enum):
    """Closed vocabulary of log event types for the trading stack."""

    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    HEARTBEAT = "heartbeat"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_FILLED = "order_filled"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"
    ALERT = "alert"
    DRIFT = "drift"
    DATA_GAP = "data_gap"
    SUPERVISOR_RESTART = "supervisor_restart"
    ROLLBACK = "rollback"
    ERROR = "error"


@dataclass(frozen=True)
class LogRecord:
    """One immutable structured log line."""

    ts: float                     # unix epoch seconds
    level: str                    # LogLevel value
    event: str                    # EventType value
    data: Mapping[str, Any] = field(default_factory=dict)


def make_record(
    event: Union[EventType, str],
    level: LogLevel = LogLevel.INFO,
    data: Optional[Mapping[str, Any]] = None,
    ts: Optional[float] = None,
) -> LogRecord:
    """Build a LogRecord, validating the event against EventType."""
    event_val = event.value if isinstance(event, EventType) else str(event)
    if event_val not in {e.value for e in EventType}:
        raise ValueError(f"unknown event type: {event_val!r}")
    return LogRecord(
        ts=time.time() if ts is None else float(ts),
        level=level.value,
        event=event_val,
        data=dict(data or {}),
    )


def format_record(record: LogRecord) -> str:
    """Render a record as one JSON line (no trailing newline). Pure."""
    return json.dumps(asdict(record), sort_keys=True, separators=(",", ":"), default=str)


class JsonLogger:
    """Appends one JSON line per event to a file path or text stream."""

    def __init__(self, sink: Union[str, Path, TextIO]):
        self._owns_stream = isinstance(sink, (str, Path))
        if self._owns_stream:
            path = Path(sink)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream: TextIO = path.open("a", encoding="utf-8")
        else:
            self._stream = sink  # type: ignore[assignment]

    def log(
        self,
        event: Union[EventType, str],
        level: LogLevel = LogLevel.INFO,
        data: Optional[Mapping[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> LogRecord:
        record = make_record(event, level, data, ts)
        self._stream.write(format_record(record) + "\n")
        self._stream.flush()
        return record

    def close(self) -> None:
        if self._owns_stream:
            self._stream.close()

    def __enter__(self) -> "JsonLogger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
