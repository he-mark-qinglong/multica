## Anomaly scan

Scanned **42** snapshots, **7** flagged.

| ts_utc | file | metric | value | baseline_median | modified_z | method |
|---|---|---|---:|---:|---:|---|
| 2026-07-15T17:29:48.685318+00:00 | dbpool-2026-07-15T17-29-48Z.json | idle | 7 | 14.00 | -4.72 | mad |
| 2026-07-15T20:35:14.933871484Z | dbpool-2026-07-15T20-35-14Z.json | idle | 7 | 14.00 | -4.72 | mad |
| 2026-07-16T14:52:32Z | dbpool-2026-07-16T22-52-32Z.json | idle | 25 | 11.50 | 4.55 | mad |
| 2026-07-16T15:09:22Z | dbpool-2026-07-16T15-09-22Z.json | idle | 21 | 12.50 | 3.82 | mad |
| 2026-07-16T16:09:39+00:00 | dbpool-2026-07-16T16-09-39Z.json | idle | 25 | 13.00 | 8.09 | mad |
| 2026-07-16T16:27:36+00:00 | dbpool-2026-07-16T16-27-36Z.json | idle | 21 | 13.00 | 5.40 | mad |
| 2026-07-16T21:37:16+00:00 | dbpool-2026-07-16T21-37-16Z.json | idle | 23 | 13.50 | 4.27 | mad |
| 2026-07-16T21:37:16+00:00 | dbpool-2026-07-16T21-37-16Z.json | active | 3 | 1.00 | inf | constant |

### Per-metric counts
- `idle`: 7
- `active`: 1
