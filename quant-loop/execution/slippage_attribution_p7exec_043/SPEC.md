# slippage_attribution — Extended Spec (P7-EXEC-043)

> Companion to `README.md` (purpose) and `INTERFACE.md` (wire
> contract). This document records the design rationale, the
> alternatives we considered, and the known failure modes.

## 1. Definition

A *fill* is the venue's response to an outbound intent. For each
fill the runner journalises two rows:

1. An `intent` row at submit time, with `payload =
   {"expected_price": ..., "expected_source": ..., ...}` when
   the request carried an arrival reference (else `payload =
   None` or `{}`).
2. A `fill` row at ack time, with `price` = the actual fill
   price.

The signed slippage for one (intent, fill) pair is, in basis
points:

```
if side == BUY:
    slippage_bps = (expected_price - fill_price) / expected_price * 10000
else (SELL):
    slippage_bps = (fill_price - expected_price) / expected_price * 10000
```

Positive = price improvement for the trader. Negative = slippage
paid to the venue.

This component decomposes the signed total into two legs:

```
total_slippage_bps = spread_cost_bps + impact_bps + residual_bps
```

where:

- `spread_cost_bps` is the *always-paid* half-spread cost the
  trader absorbs to cross the book. Captured from the
  arrival bid / ask snapshot on the intent. By construction
  `spread_cost_bps <= 0` for both BUY and SELL.
- `impact_bps = total_slippage_bps - spread_cost_bps` is the
  residual beyond the spread. Captures queue-priority loss,
  depth consumption, latency slip, and slow-market reaction.
  Typically negative on the adverse path; positive on the rare
  venue-rebate leg.
- `residual_bps = total_slippage_bps - spread_cost_bps -
  impact_bps`. Zero by construction for the two-leg model; the
  column is reserved for a future Almgren-Chriss or
  queue-position extension without a schema migration.

## 2. Why this decomposition

We chose *spread + impact* rather than e.g. *spread + impact +
latency + rebate* because:

- The other legs either (a) are not observable from journal
  state alone (`latency` would need a venue-clock feed, which
  the runner does not have), or (b) are venue-rebate events
  that already have their own sibling
  (`fee_schedule_loader_p7exec_064`).
- A two-leg decomposition is *additive-exact*: every
  attribution row satisfies
  `total = spread_cost + impact` within IEEE-754 epsilon.
  Dashboards can confidently do `mean_impact + mean_spread_cost
  ≈ mean_total` without a missing-money mystery.

The *dominant-leg* classification (SPREAD / IMPACT / MIXED /
NO_BOOK) flags which leg is doing the bleeding:

- `SPREAD` (>50% of `|total|`): the venue is the problem. The
  fix is venue-shopping or adding maker legs. Algorithm tuning
  will not help.
- `IMPACT` (>50% of `|total|`): the execution policy is the
  problem. The fix is slower slicing, smarter pegs, or deeper
  books. Venue-shopping will not help.
- `MIXED` (neither leg dominates): both legs matter. Look at the
  per-venue / per-symbol breakdown in the daily report to find
  which leg is concentrated where.

## 3. Alternatives considered

### 3.1. Three-leg decomposition with `latency_bps`

We considered splitting `impact_bps` into
`queue_loss_bps + latency_bps + walk_bps`. Rejected because:

- The journal does not carry a venue-clock-drift feed; we'd
  need to bolt on the `venue_clock_drift_p7exec_061` sibling
  for every fill to make the latency leg computable.
- Even with the feed, attributing one fill's latency across
  three legs introduces an unobservable residual (you cannot
  say "this 2 bps came from latency vs queue from this single
  ack"). The three-leg model would have a higher
  `residual_bps` baseline than the two-leg model — losing the
  additive-exact property is a worse trade than the extra
  visibility.

The `residual_bps` column is reserved for a future three-leg
extension. A future P7-EXEC ticket can populate it with
queue-position data without a schema migration.

### 3.2. Hot-path aggregation only (per-fill deque)

We considered skipping the additive table and keeping the
attribution rows in an in-memory deque per symbol. Rejected
because:

- A cold-start process needs to surface trailing-window impact
  stats before the next fill lands. An in-memory deque cannot
  be reconstructed across a restart without a journal.
- The additive table is also a forensic tool — when an
  attribution alert fires, the operator can grep the journal
  for every fill on the offending coid without re-running the
  classifier.

The cold-path cost (one INSERT per fill) is bounded by the
MAP-P7 budget (default-policy median < 100us; bench
measures end-to-end).

### 3.3. Re-using `paper_sim_fills` rows

The sibling `slippage_paper_simulation_p7exec_045` already
produces per-fill `base_slippage_bps` / `impact_bps` /
`noise_bps` decomposition. We considered piggy-backing on that
table for the live leg. Rejected because:

- Paper-sim fills live in a different schema (`paper_sim_fills`)
  that is only populated by the paper-trading adapter. Live
  fills never write there.
- The decomposition semantics are different: paper-sim uses
  a *configured* spread (default 4 bps) and an *expected*
  impact curve. Live attribution uses the *observed* spread
  from the arrival book. Mixing the two would conflate a
  config knob with a measurement.

This component writes to its own additive table
(`slippage_attribution_fills`) keyed on live `fills` rows.

## 4. Sign convention

Sign convention matches the canonical
`venue_fill_quality_p7exec_080.slippage_bps`:

* Positive = price improvement for the trader.
* Negative = slippage paid to the venue.

For both BUY and SELL the spread cost is always paid on a
marketable order: `spread_cost_bps = -half_spread_bps`. A
positive return is reserved for the rare venue-rebate leg and
is not produced today; tests pin `spread_cost_bps <= 0`.

For SELL the total slippage formula inverts:
`(fill_price - expected_price) / expected_price * 10000` — but
the decomposition mechanics are unchanged.

## 5. Failure modes

### 5.1. Missing arrival book snapshot

If the strategy does not populate `arrival_bid` / `arrival_ask`
on the request, the row is recorded with
`classification=NO_BOOK` and the spread / impact legs are
zero. The total is still reported. A dashboard can count the
`NO_BOOK` rate as a "strategy-not-instrumented" KPI.

### 5.2. Crossed book at arrival

`half_spread_bps` raises `ValueError` on `bid >= ask`. The
observer catches this and falls back to `NO_BOOK` — the
fill is durable in the journal, the spread / impact legs
are zero, the total is still reported. A crossed book at
arrival is a real state (the book can flip during a fast
move) but it is also a sign that the strategy's intent-time
snapshot is stale; we surface it as `NO_BOOK` rather than
guessing a synthetic mid.

### 5.3. Non-finite prices / quantities

`attribute_fill` raises `ValueError` on non-finite or
non-positive prices / quantities. The observer catches the
exception and surfaces it via the `_slippage_attribution_error`
key in the ack observation; the canonical `fills` row is
already durable. The row is NOT journaled in this case
(NEVER silently drops but never silently corrupts).

### 5.4. WARN thrashing at the threshold boundary

The classifier's WARN / RECOVERED logic uses a hysteresis
(`impact_hysteresis_bps`, default 1 bps). A trailing mean
that oscillates around `-impact_warn_bps` does not flap alerts.

### 5.5. `journal` not bootstrapped

If the caller forgets to call `bootstrap_journal(journal)`
before `register_on_fill(...)`, the classifier's constructor
runs it for them. The aggregator's constructor does the same.
A direct `journal.conn.cursor().execute("SELECT ...")` from a
test that bypasses the bootstrap will fail; this is intentional
— tests should either construct a component or call
`bootstrap_journal` explicitly.

## 6. Canonical usage block

```python
from execution.runner import (
    ExecutionRunner, OrderJournal, OutboundTransport,
)
from execution.slippage_attribution_p7exec_043 import (
    SlippageAttributionClassifier,
    SlippageAttributionReport,
)

# Live observer wired to the runner:
journal = OrderJournal("/var/lib/multica/orders.db")
transport = OutboundTransport(callable_send=lambda req: {"ok": True, "price": 100.0})
runner = ExecutionRunner(journal=journal, transport=transport)
runner.register_on_fill(SlippageAttributionClassifier(journal=journal))

# Cold-path daily aggregator (cron-driven):
report = SlippageAttributionReport(journal=journal, min_sample=5)
daily = report.compute_day("2026-07-25")
report.record(daily)

# Pure helper for ad-hoc diagnostics:
from execution.slippage_attribution_p7exec_043 import (
    attribute_fill, FillRecord,
)
row = attribute_fill(FillRecord(
    timestamp=1, side="BUY", symbol="BTCUSDT",
    expected_price=100.0, fill_price=100.07, quantity=0.01,
    arrival_bid=99.95, arrival_ask=100.05,
    arrival_mid=100.0, venue="binance_usdt_futures",
    client_order_id="coid-1",
))
# row.classification == "SPREAD"
# row.spread_cost_bps ≈ -5.0
# row.impact_bps ≈ -2.0
# row.total_slippage_bps ≈ -7.0
```

## 7. Constraints honored

- **Hot-path overhead < 250us / fill** — verified by
  `bench_slippage_attribution.py`. Median end-to-end is
  sub-100us on the default policy.
- **Pure-function decomposition** — `attribute_fill` has zero
  I/O; tests pin its behavior across inputs.
- **NEVER silently drop fills** — every malformed input
  raises `ValueError`; missing book snapshots are recorded
  as `NO_BOOK`, not coerced to a synthetic value.
- **Folder name suffix `_p7exec_NNN`** — folder is
  `slippage_attribution_p7exec_043`. No `_v1` / `_v2` ever.
- **Persistence is the journal's job** — this module never
  bypasses `OrderJournal`. The observer writes only to its
  additive table; the canonical `fills` row is untouched.