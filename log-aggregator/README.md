# log-aggregator (SMA-35770-089 — Monitor #89)

A small, dependency-free log aggregator for the multica workspace. Streams log
files from one or more paths, parses the most common line formats, and emits
a structured per-source / per-level summary — useful for the
"Monitoring & Observability" wing of the multica roadmap
([SMA-35770 / MAP-P9](https://multica.example/issues/SMA-35770)).

This module is infrastructure, not product. It exists so other monitors
(dashboards, alerts, anomaly detectors) can answer questions like
"how many CRITICALs in the last 5 minutes", "errors/sec by source",
"fill rate of unparseable lines" without re-parsing raw logs themselves.

## What it does

- Walks files/directories/globs and yields one `LogRecord` per line.
- Parses (in order): JSON-lines, python-logging (`YYYY-MM-DD HH:MM:SS,fff LEVEL source: msg`),
  ISO-8601 + level + message, RFC3164-ish syslog, level-prefix fallback.
- Normalizes severity: `ERR → ERROR`, `WARN → WARNING`, `FATAL → CRITICAL`, `NOTICE → INFO`,
  anything else → `OTHER`.
- Buckets records by:
  - level
  - source
  - (level, source) pair
  - floor-aligned UTC minute (for time-windowed error rates)
- Reports: total / parsed / unparsed lines, parse-error rate, earliest and
  latest timestamps, observed duration, per-source line counts and sizes.

## Layout

```
log-aggregator/
├── README.md                 — this file
├── log_aggregator.py         — library (LogParser, Summary, aggregate, helpers)
├── run.py                    — CLI entrypoint
├── tests/
│   ├── __init__.py
│   └── test_log_aggregator.py — unittest suite (14 tests)
├── sample_logs/              — toy data, used by tests + for evidence runs
│   ├── app.log               — python-logging style
│   ├── daemon.log            — syslog style
│   └── strategy_events.jsonl — JSON-lines
└── state/                    — snapshots written here at runtime
    ├── last-summary.json     — most recent summary (overwritten)
    └── summary-<ts>.json     — timestamped history
```

## CLI

```bash
python3 run.py --paths sample_logs                       # default: sample_logs
python3 run.py --paths /var/log/multica /var/log/postgres
python3 run.py --paths '/var/log/**/*.log' --no-recursive
python3 run.py --paths sample_logs --quiet                # only writes files
python3 run.py --paths sample_logs --output /tmp/s.json
```

`--paths` accepts any mix of files, directories, and glob patterns.
Everything else is inferred from the workspace layout.

## Library API

```python
from log_aggregator import LogParser, aggregate, stream_records, aggregate_paths

# One-shot: parse a directory of logs into a Summary
summary, inventory = aggregate_paths(["/var/log/multica"])
print(summary.by_level, summary.by_minute_level)

# Streaming: for huge logs
for rec in stream_records(["/var/log/postgres"]):
    if rec.level == "CRITICAL":
        forward(rec)
```

## Tests

```bash
cd /Users/mark/multica/log-aggregator
python3 -m unittest tests.test_log_aggregator -v
```

Current coverage: 14 tests covering parser, aggregation, file inventory,
glob handling, and end-to-end CLI run.

## Design choices

- **No third-party deps.** Pure Python 3.8 stdlib. The multica daemon host
  runs Python 3.8 today and pulling in `orjson` / `python-json-logger` for a
  200-line aggregator is a worse trade than a small regex.
- **Streaming-first.** Files are opened lazily and parsed line-by-line. The
  in-memory list only exists inside `aggregate_paths` (used by tests and CLI
  to also build the per-file inventory; for arbitrary large sources call
  `stream_records` directly).
- **Bucketing by minute is UTC, floor-aligned.** This matches how Grafana /
  Loki "step=1m" buckets work; downstream monitors can plug straight in.
- **No network calls.** The aggregator never reaches out — it reads local
  files only. Forwarding the summary to a remote dashboard is the caller's
  problem (see TODOs).

## Scope limits (intentional)

This is **Monitor #89 (log aggregator)** inside MAP-P9 — it intentionally does NOT:

- ship an HTTP server (out of scope; consumes another parent issue).
- tail files (a `log tailer` is a separate concern; this aggregator snaps.
  Re-running at a fixed cadence gives effectively the same result for
  batch-style dashboards).
- ship alerts (alerting is Monitor #88 and adjacent issues).
- ship dashboards (separate parent issue).

If your monitor wants one of the above, create or claim the matching MAP-P9
issue rather than overloading this one.

## Operational notes

- Default state directory: `/Users/mark/multica/log-aggregator/state/`.
- The aggregator is safe to re-run on the same paths repeatedly; it is
  idempotent.
- For Tokyo-server logs, scp/sync the snapshot back, or run on-host. The
  collector does no remote I/O.