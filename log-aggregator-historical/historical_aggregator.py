"""Historical error-pattern aggregator.

Scope (Monitor #79, MAP-P9 / SMA-35770):
    Stitch together all rotated ``error-patterns.jsonl*`` files in a directory
    (plus the live file) and produce a per-snapshot timeline + cumulative
    counts + new/resolved pattern sets + regression deltas.

Public surface:
    Snapshot              -- one rotation/live file as a single bag of records
    Aggregate             -- the full report across the timeline
    iter_snapshot_files() -- yield (label, path) pairs from a directory, in
                              chronological order (current file is yielded
                              LAST and labelled "current")
    aggregate_directory() -- read-only convenience that builds an Aggregate
    aggregate_files()     -- same, but for an explicit path list
    main()                -- CLI: ``python3 historical_aggregator.py DIR...``

Design:
    - Pure stdlib. Python 3.8+.
    - Read-only. Never mutates input files.
    - Streams records (does not load whole files into memory beyond parsing
      one file at a time).
    - Skips malformed/blank lines silently (so a corrupted entry does not
      poison the whole run).
    - "Chronological" order is filename-sorted by the lexicographic ordering
      of the backup suffixes (which embed HH-MM timestamps). The live file is
      always last.

Non-goals:
    - Daemon.log parsing (covered by Monitor #99).
    - General multi-format log parsing (covered by Monitor #89).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# File names we look at in a directory. The live file is always last; the rest
# are sorted lexicographically (which puts ``pre-05-30-patrol`` before
# ``pre-13-00-patrol`` and current never has a ``pre-`` prefix).
LIVE_FILE = "error-patterns.jsonl"
BACKUP_PREFIX = "error-patterns.jsonl.pre-"
REGRESSION_FACTOR = 2.0  # count-doubling on a single step flags a regression


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """One JSON line from an error-patterns file."""
    ts: str               # ISO-8601 string as written
    source: str           # e.g. "patrol_self_report", "autopilot_run"
    category: str         # e.g. "api_error", "data_integrity"
    pattern: str          # short symbolic name
    signature: str = ""   # optional long-form signature (may be empty)
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Snapshot:
    """A single file's contribution to the timeline."""
    label: str            # file basename (without directory)
    path: str             # abs path
    line_count: int = 0
    parsed_count: int = 0
    skipped_count: int = 0
    records: list = field(default_factory=list)   # list[Record]
    first_ts: str = ""
    last_ts: str = ""

    def by_category(self) -> Counter:
        c: Counter = Counter()
        for r in self.records:
            c[r.category] += 1
        return c

    def by_source(self) -> Counter:
        c: Counter = Counter()
        for r in self.records:
            c[r.source] += 1
        return c

    def by_pattern(self) -> Counter:
        c: Counter = Counter()
        for r in self.records:
            c[r.pattern] += 1
        return c


@dataclass
class Aggregate:
    """Full historical report."""
    root: str                          # directory scanned (or "explicit")
    snapshots: list = field(default_factory=list)            # list[Snapshot]
    cumulative_by_category: Counter = field(default_factory=Counter)
    cumulative_by_source: Counter = field(default_factory=Counter)
    cumulative_by_pattern: Counter = field(default_factory=Counter)
    new_patterns: list = field(default_factory=list)          # appeared in latest snapshot only (or first-seen anywhere)
    resolved_patterns: list = field(default_factory=list)     # disappeared from latest snapshot
    regressions: list = field(default_factory=list)           # pattern growth-doubling events
    earliest_ts: str = ""
    latest_ts: str = ""

    def totals(self) -> dict:
        return {
            "snapshots": len(self.snapshots),
            "lines_total": sum(s.line_count for s in self.snapshots),
            "parsed_total": sum(s.parsed_count for s in self.snapshots),
            "skipped_total": sum(s.skipped_count for s in self.snapshots),
            "unique_patterns": len(self.cumulative_by_pattern),
            "unique_categories": len(self.cumulative_by_category),
            "unique_sources": len(self.cumulative_by_source),
        }

    def as_dict(self) -> dict:
        return {
            "root": self.root,
            "totals": self.totals(),
            "snapshots": [
                {
                    "label": s.label,
                    "lines": s.line_count,
                    "parsed": s.parsed_count,
                    "skipped": s.skipped_count,
                    "first_ts": s.first_ts,
                    "last_ts": s.last_ts,
                    "by_category": dict(s.by_category()),
                    "by_source": dict(s.by_source()),
                    "top_patterns": dict(s.by_pattern().most_common(5)),
                }
                for s in self.snapshots
            ],
            "cumulative_by_category": dict(self.cumulative_by_category.most_common()),
            "cumulative_by_source": dict(self.cumulative_by_source.most_common()),
            "cumulative_top_patterns": dict(self.cumulative_by_pattern.most_common(20)),
            "new_patterns_in_latest": list(self.new_patterns),
            "resolved_patterns_in_latest": list(self.resolved_patterns),
            "regressions": self.regressions,
            "earliest_ts": self.earliest_ts,
            "latest_ts": self.latest_ts,
        }


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def iter_snapshot_files(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield (label, path) for every error-patterns file under ``root``.

    Order: rotated backups first (chronological by filename), then the live
    file last. Skips anything that doesn't match the expected naming.
    """
    if not root.is_dir():
        return
    backups: list[tuple[str, Path]] = []
    live: tuple[str, Path] | None = None
    for entry in sorted(root.iterdir()):
        if entry.name == LIVE_FILE and entry.is_file():
            live = (LIVE_FILE, entry)
        elif entry.name.startswith(BACKUP_PREFIX) and entry.is_file():
            backups.append((entry.name, entry))
    backups.sort(key=lambda kv: kv[0])  # lex order = chronological for these prefixes
    yield from backups
    if live is not None:
        yield live


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_one(line: str) -> Record | None:
    """Parse one JSONL line into a Record, or None if it should be skipped."""
    s = line.strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    ts = obj.get("ts", "")
    source = obj.get("source", "")
    category = obj.get("category", "")
    pattern = obj.get("pattern", "")
    if not (ts and source and category and pattern):
        return None
    signature = obj.get("signature", "") or ""
    return Record(
        ts=str(ts),
        source=str(source),
        category=str(category),
        pattern=str(pattern),
        signature=str(signature),
        raw=obj,
    )


def parse_snapshot(path: Path) -> Snapshot:
    """Read one file into a Snapshot. Tolerant to bad/blank lines."""
    snap = Snapshot(label=path.name, path=str(path))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        snap.skipped_count = 1
        snap.first_ts = f"<read-error: {exc}>"
        return snap
    for line in text.splitlines():
        snap.line_count += 1
        if not line.strip():
            snap.skipped_count += 1
            continue
        rec = _parse_one(line)
        if rec is None:
            snap.skipped_count += 1
            continue
        snap.records.append(rec)
        snap.parsed_count += 1
        if not snap.first_ts:
            snap.first_ts = rec.ts
        snap.last_ts = rec.ts
    return snap


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _detect_regressions(snapshots: list[Snapshot]) -> list[dict]:
    """Flag any pattern whose count doubled or more between two adjacent snapshots."""
    out: list[dict] = []
    if len(snapshots) < 2:
        return out
    prev_counts: Counter = snapshots[0].by_pattern()
    prev_label = snapshots[0].label
    for cur in snapshots[1:]:
        cur_counts = cur.by_pattern()
        for pat, cur_n in cur_counts.items():
            prev_n = prev_counts.get(pat, 0)
            if prev_n == 0:
                # Newly-appearing patterns are surfaced via new_patterns_in_latest
                continue
            if cur_n >= prev_n * REGRESSION_FACTOR and cur_n - prev_n >= 1:
                out.append({
                    "pattern": pat,
                    "from_label": prev_label,
                    "to_label": cur.label,
                    "from_count": prev_n,
                    "to_count": cur_n,
                    "factor": round(cur_n / prev_n, 2),
                })
        prev_counts = cur_counts
        prev_label = cur.label
    return out


def _diff_sets(snapshots: list[Snapshot]) -> tuple[list[str], list[str]]:
    """(new_patterns_in_latest, resolved_patterns_in_latest)."""
    if not snapshots:
        return [], []
    latest = snapshots[-1]
    if len(snapshots) == 1:
        # No "prior" to diff against -- treat all as new.
        return sorted({r.pattern for r in latest.records}), []
    prior_patterns = {r.pattern for s in snapshots[:-1] for r in s.records}
    latest_patterns = {r.pattern for r in latest.records}
    new = sorted(latest_patterns - prior_patterns)
    resolved = sorted(prior_patterns - latest_patterns)
    return new, resolved


def aggregate_snapshots(snapshots: list[Snapshot], root: str = "<explicit>") -> Aggregate:
    agg = Aggregate(root=root)
    agg.snapshots = list(snapshots)
    for s in snapshots:
        agg.cumulative_by_category.update(s.by_category())
        agg.cumulative_by_source.update(s.by_source())
        agg.cumulative_by_pattern.update(s.by_pattern())
        if s.first_ts and (not agg.earliest_ts or s.first_ts < agg.earliest_ts):
            agg.earliest_ts = s.first_ts
        if s.last_ts and (not agg.latest_ts or s.last_ts > agg.latest_ts):
            agg.latest_ts = s.last_ts
    agg.new_patterns, agg.resolved_patterns = _diff_sets(snapshots)
    agg.regressions = _detect_regressions(snapshots)
    return agg


def aggregate_directory(root: Path) -> Aggregate:
    snaps = [parse_snapshot(p) for _, p in iter_snapshot_files(root)]
    return aggregate_snapshots(snaps, root=str(root))


def aggregate_files(paths: Iterable[Path]) -> Aggregate:
    snaps = [parse_snapshot(p) for p in paths]
    return aggregate_snapshots(snaps, root="<explicit>")


# ---------------------------------------------------------------------------
# Pretty-printer (for human output)
# ---------------------------------------------------------------------------


def _ascii_table(rows: list[tuple], headers: tuple[str, ...]) -> str:
    """Render rows with | separators and a simple width-fit. rows is a list of tuples (stringified)."""
    str_rows = [tuple(str(c) for c in r) for r in rows]
    widths = [len(h) for h in headers]
    for r in str_rows:
        for i, c in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(c))
    sep = "-+-".join("-" * w for w in widths)
    out = []
    out.append(" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    out.append(sep)
    for r in str_rows:
        cells = []
        for i, c in enumerate(r):
            w = widths[i] if i < len(widths) else len(c)
            cells.append(c.ljust(w))
        out.append(" | ".join(cells))
    return "\n".join(out)


def render_report(agg: Aggregate) -> str:
    lines: list[str] = []
    t = agg.totals()
    lines.append("# Historical error-pattern aggregator -- Monitor #79")
    lines.append(f"root          : {agg.root}")
    lines.append(f"snapshots     : {t['snapshots']}")
    lines.append(f"lines (raw)   : {t['lines_total']}")
    lines.append(f"parsed        : {t['parsed_total']}")
    lines.append(f"skipped       : {t['skipped_total']}")
    lines.append(f"unique cats   : {t['unique_categories']}")
    lines.append(f"unique srcs   : {t['unique_sources']}")
    lines.append(f"unique pats   : {t['unique_patterns']}")
    lines.append(f"earliest_ts   : {agg.earliest_ts or '-'}")
    lines.append(f"latest_ts     : {agg.latest_ts or '-'}")
    lines.append("")

    # Per-snapshot timeline
    lines.append("## Snapshot timeline (oldest -> latest)")
    rows = []
    for s in agg.snapshots:
        rows.append((s.label, s.line_count, s.parsed_count, s.skipped_count, s.first_ts, s.last_ts))
    lines.append(_ascii_table(rows, ("label", "lines", "parsed", "skipped", "first_ts", "last_ts")))
    lines.append("")

    # Cumulative top categories
    lines.append("## Cumulative by category (top 10)")
    rows = [(k, v) for k, v in agg.cumulative_by_category.most_common(10)]
    lines.append(_ascii_table(rows, ("category", "count")) if rows else "(none)")
    lines.append("")

    # Cumulative top sources
    lines.append("## Cumulative by source (top 10)")
    rows = [(k, v) for k, v in agg.cumulative_by_source.most_common(10)]
    lines.append(_ascii_table(rows, ("source", "count")) if rows else "(none)")
    lines.append("")

    # Cumulative top patterns
    lines.append("## Cumulative top patterns (top 20)")
    rows = [(k, v) for k, v in agg.cumulative_by_pattern.most_common(20)]
    lines.append(_ascii_table(rows, ("pattern", "count")) if rows else "(none)")
    lines.append("")

    # New / resolved
    lines.append("## New patterns in latest snapshot")
    lines.append(", ".join(agg.new_patterns) if agg.new_patterns else "(none)")
    lines.append("")
    lines.append("## Resolved patterns in latest snapshot")
    lines.append(", ".join(agg.resolved_patterns) if agg.resolved_patterns else "(none)")
    lines.append("")

    # Regressions
    lines.append("## Regressions (count doubled+ from prior snapshot)")
    if agg.regressions:
        rows = [(r["pattern"], r["from_label"], r["to_label"], r["from_count"], r["to_count"], f"x{r['factor']}")
                for r in agg.regressions]
        lines.append(_ascii_table(rows, ("pattern", "from", "to", "before", "after", "factor")))
    else:
        lines.append("(none)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="historical_aggregator",
        description="Aggregate rotated error-patterns.jsonl files (Monitor #79).",
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="One or more directories to scan, or one or more jsonl files. "
             "Directories are scanned for 'error-patterns.jsonl' and "
             "'error-patterns.jsonl.pre-*' files.",
    )
    p.add_argument("--output", choices=("text", "json"), default="text")
    p.add_argument(
        "--state-dir",
        default=None,
        help="If set, write a JSON snapshot of the full report under this dir.",
    )
    args = p.parse_args(argv)

    snapshots: list[Snapshot] = []
    roots: list[str] = []
    for arg in args.paths:
        pth = Path(arg)
        if pth.is_dir():
            snaps_here = [parse_snapshot(p) for _, p in iter_snapshot_files(pth)]
            snapshots.extend(snaps_here)
            roots.append(str(pth))
        elif pth.is_file():
            snapshots.append(parse_snapshot(pth))
            roots.append(pth.name)
        else:
            print(f"warning: skipping non-existent path: {arg}", file=sys.stderr)
    agg = aggregate_snapshots(snapshots, root=", ".join(roots) if roots else "<empty>")

    if args.output == "json":
        payload = json.dumps(agg.as_dict(), indent=2, sort_keys=True)
        print(payload)
    else:
        print(render_report(agg))

    if args.state_dir:
        sd = Path(args.state_dir)
        sd.mkdir(parents=True, exist_ok=True)
        out = sd / "historical-aggregate.json"
        out.write_text(json.dumps(agg.as_dict(), indent=2, sort_keys=True))
        print(f"wrote {out}", file=sys.stderr)

    return 0


def main(argv: list[str] | None = None) -> int:
    return _cli(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
