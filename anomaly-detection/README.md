# anomaly-detection (SMA-35770-088 — Monitor #88)

A small, dependency-free anomaly detector for time-series metrics in the
multica workspace. Consumes a JSONL stream of metric points, runs a
configurable bank of detectors side by side, and emits structured anomaly
records. Inputs come from anywhere (Prometheus exporters, app emitters,
log aggregators, the `log-aggregator` Monitor #89).

This module is **infrastructure, not product**. It exists so other monitors
(dashboards, alert routers, status pages) can answer questions like
"which metric is currently drifting", "which point looks anomalous vs.
its history", "give me a severity breakdown of last hour's anomalies"
without each one reimplementing windowed statistics.

## What it does

- Reads JSONL metric streams from one or more paths (files, dirs, globs).
- Accepts these timestamp formats: epoch seconds, epoch milliseconds,
  ISO-8601 (`2024-08-03T10:14:22Z`).
- Normalizes field names (`name` ⇄ `metric` ⇄ `metric_name`,
  `value` ⇄ `v`, `ts` ⇄ `timestamp` ⇄ `t`, `labels` ⇄ `tags`).
- Runs a bank of detectors per (metric name, label set). Six detector
  families are included; only five are wired into the default bank
  (`bounds` is opt-in because it requires a domain-specific band).
- Cooldown-suppresses repeat fires on the same (metric, detector) within
  `--cooldown-sec` (default 60s) so dashboards don't get spammed.
- Skips the first `min_samples` points per metric as a training phase.
- Writes structured `AnomalyReport` to `<state-dir>/last-report.json` and
  a timestamped snapshot for history.

## Detector families

| Detector           | Statistic                | Anomaly signal                              |
|--------------------|--------------------------|---------------------------------------------|
| `zscore`           | rolling mean / std       | `|(x-mean)/std| > z_threshold`              |
| `robust_zscore`    | rolling median / MAD     | `0.6745 * |x-median| / MAD > z_threshold`  |
| `ewma_zscore`      | exponentially-weighted   | same score, half-life-based decay           |
| `iqr`              | Tukey fences             | outside `[Q1 - k*IQR, Q3 + k*IQR]`          |
| `rate_of_change`   | rolling σ(Δ)             | `|(Δ - mean(Δ)) / std(Δ)| > z_threshold`    |
| `bounds`           | static `[low, high]`     | value outside band                          |

`bounds` is opt-in; the others are wired into `default_detector_bank()`.

## Layout

```
anomaly-detection/
├── README.md                        — this file
├── anomaly_detector.py              — library (detectors, AnomalyDetector, parser)
├── run.py                           — CLI entrypoint
├── tests/
│   ├── __init__.py
│   ├── test_anomaly_detector.py     — unittest suite (31 tests)
│   ├── run_metrics.py               — concrete-metrics benchmark
│   └── last_metrics.json            — captured by run_metrics.py
├── sample_metrics/                  — toy JSONL data, used by tests + run.py
│   ├── cpu_pct.jsonl
│   ├── api_ms.jsonl
│   ├── trade_volume.jsonl
│   └── db_pool.jsonl
└── state/                           — snapshots written here at runtime
    ├── last-report.json
    └── report-<ts>.json
```

## CLI

```bash
python3 run.py --paths sample_metrics
python3 run.py --paths /var/log/multica/*.jsonl
python3 run.py --paths sample_metrics --detectors zscore:window=80,z_threshold=2.5
python3 run.py --paths sample_metrics --quiet                # only writes files
python3 run.py --paths sample_metrics --output /tmp/r.json
```

`--paths` accepts files, directories, or glob patterns. `--detectors` is a
repeatable list of `<name>[:key=val,key=val]` specs; the bank runs in that
order.

## Library API

```python
from anomaly_detector import (
    MetricPoint, AnomalyDetector, ZScoreDetector,
    RobustZScoreDetector, EWMAZScoreDetector, IQRDetector,
    RateOfChangeDetector, BoundsDetector,
    detect_stream,
)

detectors = [
    ZScoreDetector(window=50, z_threshold=3.0),
    RobustZScoreDetector(window=50, z_threshold=3.5),
    EWMAZScoreDetector(halflife=20, z_threshold=3.0),
]
runner = AnomalyDetector(detectors=detectors, cooldown_sec=60.0, min_samples=10)

for rec in stream_metrics(["/var/log/multica/*.jsonl"]):
    runner.update(rec)

print(runner.report.as_dict())
# {
#   "total_points": ..., "anomaly_count": ..., "suppressed": {...},
#   "by_severity": {...}, "by_detector": {...}, "by_metric": {...},
#   "anomalies": [{"ts": ..., "name": ..., "detector": ..., ...}, ...]
# }
```

## Tests

```bash
cd /Users/mark/multica/anomaly-detection
python3 -m unittest tests.test_anomaly_detector -v
```

31 tests cover parsing, severity bands, every detector family
(outlier / training / no-fire / degenerate-window cases), cooldown
suppression, per-label key isolation, file I/O, and the run.py
end-to-end CLI.

## Concrete metrics

```bash
python3 tests/run_metrics.py
```

Captures three scenarios (E2E on `sample_metrics/`, 100k-point stress
with default bank, per-detector flag matrix on a known spike) and writes
results to `tests/last_metrics.json`. See that file or the run output
for current numbers.

## Design choices

- **No third-party deps.** Pure Python 3.8 stdlib. The multica daemon host
  runs Python 3.8; pulling in `numpy` / `scipy` for windowed stats is the
  wrong trade for a 1000-LOC detector.
- **Per-(metric, label-set) state.** History and EWMA caches are keyed by
  `(name, sorted-labels)`, so two `cpu_pct` metrics with different hosts
  never contaminate each other.
- **Streaming-first.** Detectors and the runner operate on point streams
  without requiring the full series in memory. The only bounded memory
  is the `history_size` window per metric (auto-sized to the largest
  detector window in the bank).
- **Honest severity mapping.** Same numeric scale for all detectors:
  `|score| >= 3.0 -> WARNING`, `|score| >= 8.0 -> CRITICAL`. The
  IQR / RoC / Bounds detectors normalize to this scale before mapping.
- **No network, no subprocess.** The library is local-files-only. Forwarding
  anomalies to a remote dashboard is the caller's problem (separate issue).

## Scope limits (intentional)

This is **Monitor #88 (anomaly detection)** inside MAP-P9 — it intentionally does NOT:

- store anomalies to a database (separate DB-pool-monitor / PG parent issue).
- emit alerts directly (alert routing is Monitor #87 / Monitor #84 /
  adjacent issues — this module is the producer, not the dispatcher).
- run continuously (this monitor is batch-style; for tail-mode wire it to
  a periodic cron and re-run).
- ship a web UI (separate parent issue).

If your monitor wants one of the above, create or claim the matching
MAP-P9 issue rather than overloading this one.

## Operational notes

- Default state directory: `/Users/mark/multica/anomaly-detection/state/`.
- The detector is safe to re-run on the same paths repeatedly; it is
  effectively idempotent on the order of input points.
- For Tokyo-server metrics, scp/sync the JSONL files back, or run on-host.
  The collector does no remote I/O.