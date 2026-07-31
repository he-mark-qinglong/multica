"""Log aggregator — collect, parse, bucket and summarize log lines.

Public surface (used by run.py and tests):
    LogRecord            — dataclass for a parsed log line
    LogParser            — formats: plain, syslog, jsonl, python-logging
    aggregate(...)       — group records into buckets and produce a summary
    Summary              — dataclass returned by aggregate
    iter_log_files(...)  — walk a directory or glob and yield readable files

Design notes:
- Streaming-first: parsers accept any iterable of bytes/str so callers can
  pipe a file handle without loading everything into memory.
- Bucketing is pure and side-effect-free; `aggregate` does no I/O.
- Aggregation is bucketed by (level, source) and by minute bucket so callers
  can answer "errors/sec over the last 5m" without re-parsing.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Known severity levels, ordered most -> least severe. Anything outside this
# set is bucketed as "OTHER".
LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "TRACE", "OTHER")


@dataclass(frozen=True)
class LogRecord:
    ts: float            # unix epoch seconds (UTC)
    level: str           # uppercase severity (CRITICAL/ERROR/WARNING/INFO/DEBUG/TRACE/OTHER)
    source: str          # logical source label, e.g. file basename or app name
    message: str         # free-form payload (trimmed)
    raw: str = ""        # original line, preserved for debugging

    def bucket_minute(self) -> int:
        """Return the floor-aligned UTC minute this record belongs to."""
        return int(self.ts // 60) * 60


@dataclass
class Summary:
    total: int = 0
    parsed: int = 0
    unparsed: int = 0
    by_level: Counter = field(default_factory=Counter)
    by_source: Counter = field(default_factory=Counter)
    by_level_source: dict = field(default_factory=lambda: defaultdict(Counter))
    by_minute_level: dict = field(default_factory=lambda: defaultdict(Counter))
    earliest_ts: float | None = None
    latest_ts: float | None = None
    duration_sec: float = 0.0
    parse_errors_sample: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "parsed": self.parsed,
            "unparsed": self.unparsed,
            "by_level": dict(self.by_level),
            "by_source": dict(self.by_source),
            "by_level_source": {k: dict(v) for k, v in self.by_level_source.items()},
            "by_minute_level": {k: dict(v) for k, v in self.by_minute_level.items()},
            "earliest_ts": self.earliest_ts,
            "latest_ts": self.latest_ts,
            "duration_sec": round(self.duration_sec, 3),
            "parse_error_rate": round(self.unparsed / self.total, 4) if self.total else 0.0,
            "parse_errors_sample": list(self.parse_errors_sample),
        }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Syslog RFC3164-ish: "Aug  3 10:14:22 host app[123]: message"
_SYSLOG_RE = re.compile(
    r"^(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?P<source>\S+?)(?:\[(?P<pid>\d+)\])?:\s*(?P<msg>.*)$"
)

# Python logging default: "2024-08-03 10:14:22,123 INFO app.module: msg"
_PYLOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:[,.]\d+)?)\s+"
    r"(?P<level>[A-Z]+)\s+(?P<source>[\w./-]+):\s*(?P<msg>.*)$"
)

# ISO 8601 with optional timezone: 2024-08-03T10:14:22Z / +08:00 / .123
_ISO_TS_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
    r"\s+(?P<level>[A-Z]+)\s+(?P<msg>.*)$"
)

# Generic level prefix: "... LEVEL ... message"
_LEVEL_PREFIX_RE = re.compile(
    r"(?P<level>\b(?:CRITICAL|FATAL|ERROR|ERR|WARN|WARNING|INFO|DEBUG|TRACE|NOTICE)\b)",
    re.IGNORECASE,
)


def _normalize_level(level: str | None) -> str:
    if not level:
        return "OTHER"
    lvl = level.upper().strip()
    aliases = {
        "ERR": "ERROR",
        "FATAL": "CRITICAL",
        "WARN": "WARNING",
        "NOTICE": "INFO",
    }
    lvl = aliases.get(lvl, lvl)
    return lvl if lvl in LEVELS else "OTHER"


def _parse_syslog_ts(raw: str) -> float | None:
    """Parse 'Aug  3 10:14:22' style timestamps. Year is assumed = current year.

    Syslog timestamps omit the year; we resolve the ambiguity by trying the
    current year and, if that places the record in the future by more than
    one day, we slide it back one month (typical "yesterday / today" syslog
    convention) and, failing that, one year.

    Returns None if parsing fails so the caller can record the record with ts=0.
    """
    now = datetime.now(timezone.utc)
    for adjustment in ((0, 0), (0, -1), (-1, 0)):
        y_off, m_off = adjustment
        try:
            year = now.year + y_off
            month_guess = now.month + m_off
            # Wrap negative months into previous year.
            while month_guess <= 0:
                month_guess += 12
                year -= 1
            dt = datetime.strptime(f"{year} {month_guess} {raw}", "%Y %m %b %d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            # If this puts the record more than 1 day in the future, keep trying.
            if dt.timestamp() - now.timestamp() > 86400:
                continue
            return dt.timestamp()
        except Exception:
            continue
    return None


def _parse_iso_ts(raw: str) -> float | None:
    """Parse ISO-8601 / python-logging timestamps."""
    raw = raw.replace(",", ".")
    # python-logging uses 'YYYY-MM-DD HH:MM:SS,fff' (no tz) — treat as UTC.
    try:
        if "T" not in raw and "+" not in raw and "Z" not in raw:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f")
        elif raw.endswith("Z"):
            dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ" if "." in raw else "%Y-%m-%dT%H:%M:%SZ")
        else:
            # python fromisoformat handles 'YYYY-MM-DDTHH:MM:SS.fff+HH:MM' and the
            # 'YYYY-MM-DDTHH:MM:SS+HHMM' form in 3.11+.
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


class LogParser:
    """Line-oriented parser that tries JSON, syslog, python-logging, then plain."""

    def __init__(self, default_source: str = "unknown") -> None:
        self.default_source = default_source

    def parse_line(self, line: str, source_hint: str | None = None) -> LogRecord:
        raw = line.rstrip("\n")
        if not raw.strip():
            return LogRecord(ts=0.0, level="OTHER", source=source_hint or self.default_source,
                             message="", raw=raw)

        # JSON lines ({"ts": ..., "level": ..., "message": ...})
        if raw.startswith("{") and raw.endswith("}"):
            try:
                obj = json.loads(raw)
                ts = _coerce_ts(obj.get("ts") or obj.get("timestamp") or obj.get("time"))
                level = _normalize_level(obj.get("level") or obj.get("severity"))
                source = str(obj.get("source") or obj.get("logger") or obj.get("app") or source_hint or self.default_source)
                message = str(obj.get("message") or obj.get("msg") or "")
                return LogRecord(ts=ts or 0.0, level=level, source=source,
                                 message=message, raw=raw)
            except json.JSONDecodeError:
                pass

        # python-logging style
        m = _PYLOG_RE.match(raw)
        if m:
            ts = _parse_iso_ts(m.group("ts"))
            return LogRecord(
                ts=ts or 0.0,
                level=_normalize_level(m.group("level")),
                source=m.group("source") or source_hint or self.default_source,
                message=m.group("msg").strip(),
                raw=raw,
            )

        # ISO 8601 + level + message
        m = _ISO_TS_RE.match(raw)
        if m:
            ts = _parse_iso_ts(m.group("ts"))
            return LogRecord(
                ts=ts or 0.0,
                level=_normalize_level(m.group("level")),
                source=source_hint or self.default_source,
                message=m.group("msg").strip(),
                raw=raw,
            )

        # Syslog style
        m = _SYSLOG_RE.match(raw)
        if m:
            ts = _parse_syslog_ts(m.group("ts"))
            level_match = _LEVEL_PREFIX_RE.search(m.group("msg"))
            level = _normalize_level(level_match.group("level") if level_match else None)
            return LogRecord(
                ts=ts or 0.0,
                level=level,
                source=m.group("source") or source_hint or self.default_source,
                message=m.group("msg").strip(),
                raw=raw,
            )

        # Generic level-prefix fallback (no timestamp)
        level_match = _LEVEL_PREFIX_RE.search(raw)
        if level_match:
            return LogRecord(
                ts=0.0,
                level=_normalize_level(level_match.group("level")),
                source=source_hint or self.default_source,
                message=raw.strip(),
                raw=raw,
            )

        return LogRecord(
            ts=0.0,
            level="OTHER",
            source=source_hint or self.default_source,
            message=raw.strip(),
            raw=raw,
        )


def _coerce_ts(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Numeric string (epoch seconds or millis)
        try:
            v = float(value)
            return v / 1000.0 if v > 1e12 else v
        except ValueError:
            return _parse_iso_ts(value)
    return None


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(records: Iterable[LogRecord], sample_errors: int = 5) -> Summary:
    """Aggregate an iterable of LogRecords into a Summary.

    Empty lines are counted in `total` and `unparsed` only if their raw
    content is empty after strip. Otherwise they pass through as level=OTHER.
    """
    s = Summary()
    for r in records:
        s.total += 1
        if not r.message and not r.raw.strip():
            s.unparsed += 1
            continue
        # An "unparsed" record is one we couldn't timestamp AND couldn't classify
        is_unparsed = (r.ts == 0.0 and r.level == "OTHER" and not r.raw.strip())
        if is_unparsed:
            s.unparsed += 1
            if len(s.parse_errors_sample) < sample_errors:
                s.parse_errors_sample.append(r.raw[:200])
            continue
        s.parsed += 1
        s.by_level[r.level] += 1
        s.by_source[r.source] += 1
        s.by_level_source[r.level][r.source] += 1
        if r.ts > 0:
            s.by_minute_level[r.bucket_minute()][r.level] += 1
            if s.earliest_ts is None or r.ts < s.earliest_ts:
                s.earliest_ts = r.ts
            if s.latest_ts is None or r.ts > s.latest_ts:
                s.latest_ts = r.ts

    if s.earliest_ts is not None and s.latest_ts is not None:
        s.duration_sec = max(0.0, s.latest_ts - s.earliest_ts)

    return s


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------

def _glob_paths(pattern: str) -> list[Path]:
    """Glob that works with both relative and absolute patterns.

    Python 3.8's pathlib.Path.glob does not accept absolute patterns; this
    wrapper uses Path.parent + Path.name when needed.
    """
    p = Path(pattern)
    if p.is_absolute() or any(ch in pattern for ch in "*?["):
        # Split into parent + leaf for Path.glob
        parent = p.parent if str(p.parent) else "."
        try:
            return [Path(x) for x in sorted(Path(parent).glob(p.name))]
        except OSError:
            return []
    return [Path(x) for x in sorted(Path(".").glob(pattern))]


def iter_log_files(paths: Iterable[str], recursive: bool = True) -> Iterator[tuple[str, str]]:
    """Yield (source_label, file_path) for every readable log file.

    `paths` may be:
      - a single file path
      - a directory (recurses if recursive=True)
      - a glob pattern (e.g. "/var/log/*.log")

    source_label is the file basename (without extension) so downstream
    bucketing has a stable, human-friendly key.
    """
    seen: set[str] = set()
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            files = [p]
        elif p.is_dir():
            if recursive:
                files = [x for x in p.rglob("*") if x.is_file()]
            else:
                files = [x for x in p.iterdir() if x.is_file()]
        else:
            # Treat as glob
            files = _glob_paths(str(p))
        for f in files:
            if not f.is_file():
                continue
            real = str(f.resolve())
            if real in seen:
                continue
            seen.add(real)
            label = f.stem if f.is_file() else f.name
            yield label, str(f)


def stream_records(paths: Iterable[str], recursive: bool = True) -> Iterator[LogRecord]:
    """Yield LogRecords for every line in every file under `paths`."""
    parser = LogParser()
    for label, path in iter_log_files(paths, recursive=recursive):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    yield parser.parse_line(line, source_hint=label)
        except OSError as exc:
            # Surface the error as a synthetic record so it shows up in stats.
            yield LogRecord(
                ts=time.time(),
                level="ERROR",
                source=label,
                message=f"failed to open {path}: {exc}",
                raw=str(exc),
            )


# ---------------------------------------------------------------------------
# Aggregate-from-paths convenience
# ---------------------------------------------------------------------------

def aggregate_paths(paths: Iterable[str], recursive: bool = True) -> tuple[Summary, dict]:
    """Stream + aggregate in one call. Returns (summary, file_inventory).

    file_inventory maps source_label -> {path, size_bytes, line_count}.
    """
    file_inventory: dict[str, dict] = {}
    parsed_stream: list[LogRecord] = []  # we need to count lines per file too

    for label, path in iter_log_files(paths, recursive=recursive):
        size = os.path.getsize(path) if os.path.exists(path) else 0
        line_count = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                parser = LogParser()
                for line in fh:
                    line_count += 1
                    parsed_stream.append(parser.parse_line(line, source_hint=label))
        except OSError as exc:
            parsed_stream.append(LogRecord(
                ts=time.time(),
                level="ERROR",
                source=label,
                message=f"failed to open {path}: {exc}",
                raw=str(exc),
            ))
        file_inventory[label] = {"path": path, "size_bytes": size, "line_count": line_count}

    summary = aggregate(parsed_stream)
    return summary, file_inventory