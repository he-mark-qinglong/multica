# Data freshness dashboard — ops runbook

> One-page ops guide for `scripts/freshness_dashboard.py`. Companion to
> `AGENTS.md` §6 (canonical spec) and `scripts/missing_bar_detector.py`
> (structural completeness, §5). If a question is "is the file present
> and well-formed?" → use §5. If it's "is the latest bar new enough to
> drive a live strategy?" → use this runbook.

## TL;DR

```bash
# From quant-loop/ — produces data/freshness/freshness-{snapshot.json,dashboard.html}.
python3 scripts/freshness_dashboard.py \
    --workspace . \
    --json-out data/freshness/freshness-snapshot.json \
    --html-out data/freshness/freshness-dashboard.html

# Open the HTML report.
xdg-open data/freshness/freshness-dashboard.html   # Linux
open       data/freshness/freshness-dashboard.html # macOS
```

Exit `0` = every expected cell present and within budget. Exit `1` = at
least one cell is stale or missing — investigate before the next
strategy run.

## When to run

* **Pre-flight** before any backtest that assumes "data is current"
  (anything using `open_time == now() - bar_interval`).
* **Cron** on a 5-min cadence via the `graph-janitor` / `dispatch`
  autopilots, or wired into `validation/gates.py` as the G0 freshness
  gate. The script is cheap (parquet tail-read, no full scan).
* **Post-incident** after any refresh-job failure, to confirm which
  cells are stale and which will need a manual backfill.

## How to read the dashboard

The HTML renders four blocks, top to bottom:

1. **Summary chips** — `fresh: N`, `stale: N`, `missing: N`,
   `symlink: N`, `unknown: N`. If `stale > 0` or `missing > 0` the
   exit code is `1`; investigate every chip in those two colours.
2. **Per-interval breakdown** — how many cells of each status per
   timeframe (1m, 15m, 1h, …). Useful for "is this a one-symbol
   issue or systemic?".
3. **Per-cell table** — every audited row with status, last bar ISO,
   age, budget, symlink target (if any), and a free-text note.
   Click a header to sort? No — there is no JS. Open the JSON
   snapshot for programmatic access.
4. **Audit-by-replication** — the literal `find` invocation that
   built the matrix. Re-run it yourself and confirm the file count
   matches the dashboard's `files_seen`.

## Reading the JSON snapshot

```bash
# Which cells are stale right now?
jq '.reports[] | select(.status == "stale")
     | {symbol: .symbol, interval: .interval, age_ms, budget_ms,
        last_bar_iso, note}' \
     data/freshness/freshness-snapshot.json

# Which expected cells are missing?
jq -r '.reports[] | select(.status == "missing") | .path' \
     data/freshness/freshness-snapshot.json
```

`budget_ms` is always set when the interval is known — `null` only
appears for cells where the filename doesn't parse (see "Unknown
files" below).

## Triage flowchart

```
status == fresh
    -> nothing to do

status == stale
    -> Was the refresh job scheduled? Did it succeed?
       - Yes -> rerun it.
       - No  -> schedule it, then rerun.
    -> After rerun, expect `fresh`. If still `stale`,
       investigate exchange-side rate limits / outages.

status == missing (size > 1 KB expected, file absent)
    -> Was the file ever present? Check git log / backups.
    -> If yes, restore from backup.
    -> If no, this is a known gap — file a tracking issue and
       update the EXPECTED_OHLCV / EXPECTED_FUNDING list in
       scripts/freshness_dashboard.py.

status == symlink
    -> Confirm the target via `readlink -f <path>`.
    -> If the target is wrong (the SMA-34855 BTCUSDT_4h bug),
       remove the symlink and write a real file. Document the
       intent in AGENTS.md §2.

status == missing (size < 1 KB)
    -> Stub file. Remove it; the dashboard will then report
       `missing` with "path does not exist" instead, which is
       the right state until the real file lands.

status == unknown
    -> Filename didn't match any known bucket / interval
       pattern. Either:
       (a) a new strategy was added with a non-standard
           filename — add a regex to `classify()`; or
       (b) the file is genuinely not an OHLCV / funding
           series — drop it from the §1 ``find`` filter.
```

## Known false-positive corner

`BTCUSDT_4h.parquet` is documented as a symlink to `BTCUSD_4h.parquet`
(AGENTS.md §2.1, known). The dashboard reports `status=symlink` but
exits `0` if every other cell is `fresh`. This is intentional: the
dashboard must stay visible (so the symlink doesn't recur silently)
but not hard-fail (because it's known and documented).

If you add a NEW symlink, the same behaviour applies — verify and
document it, don't remove the entry from the dashboard's
classification.

## Library API (for downstream scripts)

```python
from scripts.freshness_dashboard import (
    audit_freshness, audit_path, classify, enumerate_data_files,
    rollup, render_html, FreshnessReport,
)

# Workspace-wide audit.
reports = audit_freshness(Path("/Users/mark/multica/quant-loop"))

# Single-file audit.
report = audit_path(Path("/Users/mark/multica/quant-loop/live_data/BTCUSDT_15m.parquet"),
                    workspace=Path("/Users/mark/multica/quant-loop"))
if report.status != "fresh":
    log.warning("%s_%s %s age=%s budget=%s",
                report.symbol, report.interval, report.status,
                report.age_ms, report.budget_ms)
```

The library has no required third-party deps beyond `pandas` (which
`missing_bar_detector` already needs); if pandas is missing the
dashboard degrades to "filesystem facts only" and marks every cell
`status=unknown`.

## Tests

```bash
pytest scripts/test_freshness_dashboard.py -v
```

28 synthetic-fixture tests. Run in CI before any change to
`scripts/freshness_dashboard.py` — the budget math and the
exit-code semantics are load-bearing for downstream gates.