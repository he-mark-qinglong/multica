# Historical Error-Pattern Aggregator (Monitor #79)

Part of the MAP-P9 Monitoring & Observability project ([SMA-35770](mention://issue/4dbf9350-4e0b-4bd5-a308-7886a97e352c)). Sibling tools:

- **Monitor #89** (`/Users/mark/multica/log-aggregator/`) — general multi-format parser + CLI.
- **Monitor #99** — daemon.log + current-jsonl snapshot.

**This tool (Monitor #79)** stitches together every rotated
`error-patterns.jsonl.pre-*` backup plus the live `error-patterns.jsonl`,
producing a per-snapshot timeline, cumulative counts (category / source /
pattern), **new** and **resolved** pattern sets in the latest snapshot, and
**regression** events for any pattern whose count doubled or more between two
adjacent snapshots.

Pure stdlib, Python 3.8+, no deps, read-only.

## Install

Nothing to install. Drop the directory where you like.

## CLI

```
python3 historical_aggregator.py DIR_OR_FILE... [--output text|json]
                                      [--state-dir DIR]
```

Examples:

```bash
# Aggregate every error-patterns.jsonl* under ~/.multica/
python3 historical_aggregator.py ~/.multica/

# Same, but emit JSON and a state-dir snapshot
python3 historical_aggregator.py ~/.multica/ --output json --state-dir ./out

# Aggregate a single ad-hoc file
python3 historical_aggregator.py /path/to/one.jsonl
```

Exit code: always 0 (the tool never mutates inputs and never raises on
malformed lines). Warnings about non-existent paths go to stderr.

## API

```python
from historical_aggregator import (
    Snapshot, Aggregate, Record,
    iter_snapshot_files, parse_snapshot,
    aggregate_directory, aggregate_files, aggregate_snapshots,
)
```

`iter_snapshot_files(root)` yields `(label, path)` pairs in
chronological order (rotated backups in lexicographic filename order, then the
live file last). Use it for streaming pipelines.

`aggregate_directory(root)` returns an `Aggregate` for the whole timeline.

## What it computes

- **Per-snapshot statistics**: total lines, parsed, skipped, first/last timestamps.
- **Cumulative counts**: by category, by source, by pattern (top-N exposed).
- **New patterns in latest**: pattern strings present in the latest snapshot
  but absent from any prior snapshot.
- **Resolved patterns in latest**: pattern strings absent from the latest
  snapshot but present in at least one prior snapshot.
- **Regressions**: between adjacent snapshots, any pattern whose count is at
  least 2× the prior count (with ≥1 absolute increase) is surfaced with both
  snapshots' labels and counts.

Skipped lines (blank, non-JSON, non-dict, missing required fields) are counted
but never raise, so a single corrupted entry cannot fail the whole run.

## Scope limits

- Read-only. Never modifies input files. Never starts/stops daemons.
- No network, no auth, no daemon side-effects. L1-ops scope only.
- Lex-sort ordering of backup filenames assumes filenames embed
  zero-padded HH-MM-of-patrol in their suffix. If a non-patrol file with the
  same prefix is added, it will be included in chronological position based
  on its name.
- The "new/resolved" definition is pattern-level (string equality). Sub-pattern
  drift inside a single pattern string is not detected.
