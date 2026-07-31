# db-pool anomaly monitor (Monitor #98, MAP-P9 / SMA-35869)

> Statistical anomaly detection over the multica db-pool-monitor JSON
> snapshots.  Companion to the rule-based monitor in
> `db-pool-monitor/run.py`.

## Why

The rule-based monitor only flags absolute thresholds (stuck tx > 0, slow
queries > N, pool utilization > 75%).  It misses **relative** deviations —
e.g. the `idle` connection count dropping from a 14-snapshot median of 14
to 7 even though 7 is still well below `pg_max_connections=100`.  Such a
shift usually means a long-running workload is consuming connections that
would otherwise sit idle, and we want to surface it before the absolute
threshold is reached.

## Method

Modified z-score (Iglewicz & Hoaglin 1993) per metric:

```
modified_z = 0.6745 * (x - median(window)) / MAD(window)
```

A metric is flagged when `|modified_z| >= 3.5` (the recommended outlier
threshold).  When MAD is zero (flat baseline) the detector falls back to
mean ± stddev; if both are zero, any deviation from the constant is
flagged with `method=constant`.  The detector stays silent until at
least 8 baseline samples are available (warm-up).

## Components

| file | role |
| --- | --- |
| `scripts/db_pool_anomaly_detect.py` | detector entrypoint + library |
| `scripts/test_db_pool_anomaly_detect.py` | unit tests (stdlib unittest, 7 cases) |
| `ops-reports/db-pool-anomaly-findings.json` | findings JSON (latest run) |
| `ops-reports/db-pool-anomaly-report.md` | findings rendered as Markdown |
| `ops-reports/db-pool-anomaly-summary.txt` | one-line summary for log shipping |

## CLI

```
scripts/db_pool_anomaly_detect.py --help

  --snapshots-dir DIR   defaults to /home/smark/multica/db-pool-monitor
  --window N            rolling baseline size (default 12)
  --threshold Z         flag threshold (default 3.5)
  --min-samples N       warm-up size (default 8)
  --json                emit findings JSON on stdout
  --md                  emit Markdown on stdout
  --out PATH            write findings JSON to PATH
  --summary PATH        write one-line summary to PATH
  --metrics K1 K2 ...   restrict to a subset of metric keys
```

Exit code 0 = no anomalies, 1 = anomalies, 2 = bad input.

## Findings on the 2026-07-14 → 2026-07-17 series

`scripts/db_pool_anomaly_detect.py --snapshots-dir /Users/mark/multica/db-pool-monitor --md`

```
Scanned 42 snapshots, 7 flagged.
idle: 7
active: 1
```

All 7 findings were tagged `verdict_recorded: no-op` or `HEALTHY_NOOP` by
the rule-based monitor — the statistical detector caught signals the
thresholds missed.  Notable cases:

- `2026-07-16T16:09:39` — `idle` jumped from a median 13 to 25
  (modified_z = 8.09), while `active` stayed normal; the connection pool
  was filling up on idle connections (a leak or a long-lived worker).
- `2026-07-16T21:37:16` — `active` flipped from a constant 1 to 3, with
  MAD = 0 (constant baseline → `method=constant`, modified_z = inf).

## Tests

`scripts/test_db_pool_anomaly_detect.py -v` covers:

1. stable series → no findings
2. one huge spike → flagged
3. constant-baseline spike → flagged via MAD==0 fallback
4. warm-up below `min_samples` → silent
5. corrupt JSON in directory → skipped, valid snapshots loaded
6. markdown report shape → headers + per-metric counts
7. realistic-shape series with a rule-immune spike → flagged

All 7 tests pass (1 intentionally skipped unless `--evidence` is passed).

## Caveats

- Snapshot cadence matters. The current run has gaps of hours between
  samples, so a 12-sample window covers ~12 hours at typical density.
  During heavy-crunch periods the rolling baseline may lag a regime
  change and produce transient false positives.
- `oldest_tx_age_sec` is `null` when no idle-in-tx connections exist;
  the detector skips nulls.  If we want to monitor transaction age
  itself, pair this script with a separate watcher that polls
  `pg_stat_activity` directly.
- This monitor complements, does not replace, the rule-based monitor.
  Keep both wired.

## Related

- Parent project: SMA-35770 — MAP-P9 Monitoring & Observability
- Source monitor: `db-pool-monitor/run.py` (writes the snapshots)
- Companion monitor: `scripts/quant_disk_quota_alert.sh` (state-transition
  pattern for stateful alerts)