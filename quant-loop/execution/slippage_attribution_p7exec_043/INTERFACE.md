# slippage_attribution — Interface Contract

> P7-EXEC-043. Pair this file with `README.md` (purpose) and
> `SPEC.md` (design rationale). This document records the stable
> type / function contract the runner and downstream cron
> dashboards depend on.

## Component roles

This folder ships two sibling components:

1. **`SlippageAttributionClassifier`** — additive live observer
   wired to `ExecutionRunner.register_on_fill(...)` (same hook
   as `MakerTakerClassifier` P7-EXEC-081). Persists one row
   per fill to the additive `slippage_attribution_fills`
   table and emits IMPACT-WARN / RECOVERED observations
   when the trailing-window mean impact crosses the configured
   threshold.

2. **`SlippageAttributionReport`** — cold-path periodic
   aggregator (same pattern as `SlippageReport` P7-EXEC-050).
   Reads `slippage_attribution_fills` for one UTC day, computes
   one `DailyAttributionReport`, persists to
   `slippage_attribution_daily_reports`.

The runner's hot path is unchanged whether the classifier is
wired up or not — the observer only runs after the canonical
`fills` row has been journaled (additive P7-EXEC-081 ordering).

## Construction

```python
from execution.runner import OrderJournal
from execution.slippage_attribution_p7exec_043 import (
    SlippageAttributionClassifier,
    SlippageAttributionReport,
    AttributionThresholds,
)

journal = OrderJournal("/var/lib/multica/orders.db")
classifier = SlippageAttributionClassifier(journal=journal)
report = SlippageAttributionReport(journal=journal, min_sample=5)
```

`SlippageAttributionClassifier.__init__(journal, thresholds=...)`
runs `bootstrap_journal(journal)` on construction so a cold-start
journal carries the additive tables immediately. The classifier
also accepts a custom `:class:`AttributionThresholds` for the
trailing-window WARN logic.

`SlippageAttributionReport.__init__(journal, min_sample=5)`
also runs `bootstrap_journal(journal)`. `min_sample` is the
headline-stable threshold (default `5`); the day's report is
emitted regardless of fill count, and the `stable` flag is
`True` iff `n_fills_with_book >= min_sample`.

## Public surface

### Pure helpers (zero I/O)

#### `half_spread_bps(arrival_bid, arrival_ask) -> float`

Return the absolute half-spread in basis points.
Raises `ValueError` on non-positive bid / ask or crossed book.

#### `spread_cost_bps(*, side, arrival_bid, arrival_ask) -> float`

Signed half-spread cost for a marketable order. Always
`<= 0` (trader always pays the half-spread to cross).
Raises `ValueError` on malformed inputs.

#### `total_slippage_bps(*, side, expected_price, fill_price) -> float`

Canonical signed slippage formula (mirrors
`venue_fill_quality_p7exec_080.slippage_bps`). Positive =
improvement, negative = slippage.

#### `attribute_fill(record: FillRecord) -> AttributionRow`

Decompose a `FillRecord` into its `AttributionRow`. Two-leg
decomposition: `total = spread_cost + impact`; `residual` is
reserved for future extensions and is zero by construction.

When `arrival_bid` or `arrival_ask` is missing or invalid the
row is `classification=NO_BOOK`, the spread / impact legs are
zero, and `total_slippage_bps` is still reported.

### Observer

#### `SlippageAttributionClassifier.on_fill(request, ack, journal, ts_ns) -> ComponentResult`

Post-fill hook. Persists the attribution row, updates the
rolling mean impact per symbol, and emits a WARN / RECOVERED
row when the threshold is crossed. Returns a
`ComponentResult` with an `observation` dict carrying the
per-fill decomposition.

The `observation` dict shape:

```python
{
    "slippage_attribution_row_id": int,
    "classification": "SPREAD" | "IMPACT" | "MIXED" | "NO_BOOK" | "NO_ARRIVAL",
    "total_slippage_bps": float,
    "spread_cost_bps": float,
    "impact_bps": float,
    "residual_bps": float,
    # When a threshold crossing fires:
    "slippage_attribution_warn": {
        "symbol": str, "severity": "WARN" | "RECOVERED",
        "observed_mean_impact_bps": float,
        "threshold_bps": float,
        "window_s": float, "n_samples": int,
    } | None,
}
```

### Aggregator

#### `SlippageAttributionReport.compute_day(day_utc, *, now_ns=None) -> DailyAttributionReport`

`day_utc` MUST be `'YYYY-MM-DD'` (ISO 8601). Reads the
additive table for the UTC-day window; returns the immutable
report carrying headline statistics, per-venue / per-symbol
breakdowns, and provenance fields.

#### `SlippageAttributionReport.record(report) -> int`

Persist `report` to `slippage_attribution_daily_reports`.
Idempotent on `day_utc` via the `UNIQUE(day_utc)` constraint.
Returns the row id.

#### `SlippageAttributionReport.fetch(day_utc) -> DailyAttributionReport | None`

Fetch a persisted report by day. Returns `None` if no row
exists for that day.

### Schema

`bootstrap_journal(journal)` is idempotent and creates three
additive tables:

- `slippage_attribution_fills` — one row per fill (per-coid
  unique).
- `slippage_attribution_events` — one row per WARN / RECOVERED
  observation emitted by the classifier.
- `slippage_attribution_daily_reports` — one row per UTC day
  (per-day unique) persisted by the aggregator.

Call `bootstrap_journal(journal)` directly if you only consume
the aggregator without wiring the classifier.

## Wire it in

```python
from execution.runner import (
    ExecutionRunner, OrderJournal, OutboundTransport,
)
from execution.slippage_attribution_p7exec_043 import (
    SlippageAttributionClassifier,
)

journal = OrderJournal("/var/lib/multica/orders.db")
transport = OutboundTransport(callable_send=lambda req: {"ok": True, "price": 100.0})
runner = ExecutionRunner(journal=journal, transport=transport)
runner.register_on_fill(SlippageAttributionClassifier(journal=journal))
```

The observer is independent from every other P7-EXEC observer;
registering it does not affect `MakerTakerClassifier` /
`LatencyTracker` / `ThrottleBreachAlert` / etc.

## What this component does NOT do

- It does **not** rewrite or alter the canonical `fills` row.
  The observer only writes to its additive table.
- It does **not** participate in the pre-request / block path.
  It is `register_on_fill` only; the runner's request-side
  hot path is unchanged.
- It does **not** silently coerce missing book snapshots to a
  synthetic value. Missing arrival bid / ask ⇒
  `classification=NO_BOOK`, persistent in the journal.
- It does **not** aggregate across venues by `paper_sim_fills`
  rows — that table is owned by `slippage_paper_simulation`
  (P7-EXEC-045). Live-fills attribution is the only signal.