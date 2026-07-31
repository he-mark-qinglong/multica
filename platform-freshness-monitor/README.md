# platform-freshness-monitor

MAP-P9 monitor #82 — `SMA-35770-082` / `SMA-35853`.

Detects when key multica platform Postgres tables stop receiving writes. Complements
[`status-page-monitor`](../status-page-monitor/) (workspace / autopilot / issue-flow)
and [`data-outage` runbook](../docs/runbooks/data-outage.md) (strategy-side
`run_metric` ingest).

## Probes (6 tables, per-table thresholds)

| ID | Table | warn | escalate |
|---|---|---|---|
| comment | `comment` | 10m | 1h |
| activity_log | `activity_log` | 10m | 1h |
| autopilot_run | `autopilot_run` | 20m | 2h |
| artifact | `artifact` | 4h | 24h |
| webhook_delivery | `webhook_delivery` | 30m | 4h |
| task_usage | `task_usage` | 4h | 24h |

## Run

```bash
python3 /Users/mark/multica/platform-freshness-monitor/run.py
# subset of probes:
python3 /Users/mark/multica/platform-freshness-monitor/run.py --probe-only comment,autopilot_run
# silent (file output only):
python3 /Users/mark/multica/platform-freshness-monitor/run.py --quiet
```

## Self-test

```bash
python3 /Users/mark/multica/platform-freshness-monitor/test_run.py
```

## Layout

```
platform-freshness-monitor/
├── README.md
├── run.py                  # ~330 lines, stdlib only
├── test_run.py             # 5 self-tests (import / evaluate / humanize / subset / live)
├── last-snapshot.json      # most recent
├── state.json              # cumulative
├── dedup-state.json        # last verdict per probe
└── snapshot-<UTC>.json     # timestamped history
```

## Runbook

See [`docs/runbooks/platform-freshness-monitor.md`](../docs/runbooks/platform-freshness-monitor.md).