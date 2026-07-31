# shadow_book — Extended Spec (P7-EXEC-072)

> Companion to `README.md` (usage) and `INTERFACE.md` (wire contract).
> This document records the design rationale, the alternatives
> considered, and the known failure modes.

## 1. Definition

A *fill event* is one row from the connector's trade log
(`SPEC_live_paper_connector_binance_usdm §4.1`). For each
`client_order_id` the live-paper connector emits between 1 and N fill
events before the order reaches a terminal status. The connector
delivers them on the user-data WS as they happen.

A *shadow* view is the strategy-side reconstruction of every order's
fill history — what the strategy thinks happened. A *live* view is the
venue-truth view — what the venue says happened. They should agree;
when they don't, the divergence is actionable.

The `shadow_book` component maintains the shadow view per
`client_order_id` (running aggregation: total_qty, VWAP, fill count,
terminal status) and per `(symbol, side)` (running net position
projection), journals every fill so the run is recoverable on cold
start, and produces a reconciliation row per `client_order_id` that
appears in either the shadow view or the live view.

## 2. Why this component

Four alternatives considered:

### 2.1. Every observer computes its own shadow view

Each observer (`slippage_attribution_p7exec_083`,
`venue_fill_quality_p7exec_080`,
`pnl_attribution_per_fill_p7exec_089`) folds the stream into its own
per-coid dict. Rejected:

- N sqlite WAL INSERTs per fill (one per observer) instead of one
  shared event log + N in-memory folds.
- N dict updates and N for-loops on the hot path, rather than one
  shared state + N cheap dict reads.
- N cold-start recovery loops instead of one.
- Reconciliation against venue truth becomes a separate per-observer
  diff, each one incomplete.

### 2.2. Rely on the venue alone (`/fapi/v1/allOrders`)

Skip the shadow view entirely; reconcile the connector's local view
against the venue's allOrders history. Rejected because:

- The connector's local view IS the shadow view, but it's an
  implicit one (every observer reads `partial_fill_accumulator` then
  folds for its own needs). Centralising the shadow is the same work
  without the duplication.
- Venue-truth is the *target* of reconciliation, not the *source* of
  the shadow. Without a shadow we can't distinguish "we missed a
  fill" from "we haven't been polled yet".

### 2.3. Make the partial_fill_accumulator own the shadow view

`partial_fill_accumulator_p7exec_056` already maintains a per-coid
running aggregation. Extending it to own the position projection and
the live reconciliation would couple two distinct concerns (per-coid
fill aggregation vs. shadow-vs-live reconciliation). Rejected because:

- Single-responsibility: `partial_fill_accumulator` is per-coid fill
  accounting; `shadow_book` is shadow-vs-live reconciliation. Splitting
  them keeps each one independently testable and replaceable.
- The two have different hot-path budgets and different journal
  shapes. `partial_fill_accumulator` writes one event + one state per
  fill; `shadow_book` writes one event + one order state + one
  position state per fill. They share the live-trade-log source but
  diverge in projection.

### 2.4. External bus (Redis / Kafka)

Use the existing ops infra (Redis, Kafka) as the event bus. Rejected
because:

- Adds an external dependency to the hot path (Redis round-trip is
  100-500us; we are at 130-200us total).
- Requires the connector to depend on a connection manager,
  credentials, reconnection logic. Too much surface for an
  in-process aggregator.
- The MAP-P7 project rule is "local state journaled
  (write-ahead-log / sqlite)" — the intent is in-process journal, not
  external bus.

So: in-process shadow book with sqlite WAL journal, one shared event
log + one shared order projection + one shared position projection +
one shared live-report snapshot, with a cold-path reconciliation
diff. The journal becomes the canonical event log; downstream
observers can subscribe via sqlite triggers or read on demand.

## 3. Hot-path design

The hot path per `on_fill` does:

1. Dict lookup by `client_order_id` (O(1) average, ~50ns).
2. If unknown, hydrate from `shadow_order_states` table (one SELECT,
   ~10-30us cold-cache, ~1us warm-cache).
3. Branch on terminal status:
   - Terminal → journal late event with `LATE_FILL_LIQUIDITY` (one
     INSERT), raise. Order state, position state: NOT mutated.
   - Not terminal → fold fill into order state (two floats, two
     multiplies, one division, one branch — sub-microsecond pure work).
4. Fold fill into the `(symbol, side)` position projection (same
   pure-work envelope).
5. INSERT into `shadow_fill_events` with `ON CONFLICT DO NOTHING`
   on `(coid, trade_id)` — duplicate is silent idempotent. The
   returned row count tells us whether the event was novel or a
   duplicate; on duplicate, neither UPSERT runs.
6. If novel: UPSERT into `shadow_order_states` and UPSERT into
   `shadow_position_states` (two INSERT … ON CONFLICT DO UPDATE).

All three writes run inside sqlite's per-connection serialization
mode (`isolation_level=None`), which is sufficient for the
in-process case.

Total budget for the not-terminal case: ~150us median, ~250us p99 on
a warm journal with `wal_autocheckpoint=10000` (see
`evidence/bench_shadow_book.json`).

The hot-path is NOT instrumented with logging or metrics — the bench
script measures them externally. In production, the runner wraps the
call in its own metrics layer (`latency_metrics_p7exec_078`).

## 4. Late-fill semantics

Why journal the late event instead of silently dropping it:

- The constraint is **NEVER silently drop fills**. A late fill is
  still a fill — the venue reports it; the connector's user-data
  stream delivers it; the connector passes it to the shadow book.
  Dropping it on the floor would corrupt the audit trail.
- The constraint does NOT mean **always accept the fill**. After a
  terminal status the order's state is final; the qty and avg_price
  must not change.

So: journal the late event with a sentinel `liquidity`
(`LATE_FILL_LIQUIDITY`), do not update either the order state or the
position projection, raise. The connector catches the exception,
logs, and continues. The journal row stays so a forensic audit can
see "venue sent this ack 30 seconds after we observed FILLED —
investigate".

## 5. Reconciliation semantics

The reconciler is a pure function `reconcile(shadow_orders,
live_reports) -> List[ReconciliationRow]`. It does NOT touch the
journal; both sides are inputs.

For each `client_order_id` that appears in either side, emit a row
with:

- `shadow` and `live` populated according to which side the coid
  appeared in (one may be None).
- `only_in_shadow` / `only_in_live` flags set accordingly.
- `qty_diff = shadow.total_qty - live.total_qty` (with the missing
  side contributing 0).
- `avg_price_diff`, `fill_count_diff` analogous.
- `status_match = (shadow.terminal_status == live.terminal_status)`,
  with `None == None → True` (matches
  `partial_fill_accumulator_p7exec_056` convention).

Coids present in neither side are filtered out before emission. The
reconciler does NOT suppress divergence by threshold — that's the
drift alert layer's job (`recon_drift_alert`, sibling recon family).
The shadow book emits every divergence so the alert layer can apply
its own threshold policy.

## 6. Recovery / cold-start

The journal is the source of truth. Cold start:

1. Open the journal (the bootstrap is idempotent).
2. Hydrate in-memory cache. Three options:
   - Caller enumerates `client_order_id`s from some external source
     (e.g. positions log, `position_reconciler` table) and calls
     `book.replay_order(coid)` for each.
   - Run `SELECT DISTINCT client_order_id FROM shadow_fill_events`
     and replay each.
   - Enumerate `shadow_order_states` directly via
     `journal.fetch_all_orders()` and replay each.
3. From this point, `on_fill` uses the rehydrated cache. The
   `fetch_order` call inside `on_fill` is a fallback for the lazy
   path.
4. Position projections hydrate on the same cold start via
   `book.replay_position(symbol, side)` for each known `(symbol,
   side)` tuple.

The journal survives process crashes (WAL +
`synchronous=NORMAL`) but not a `kill -9` of sqlite itself
(acceptable for in-process state; not for the canonical trade log,
which is also journaled by the connector in `trades.jsonl`).

## 7. Known limitations

1. **Single-threaded per coid.** The component has no internal lock.
   The connector's user-data WS delivers events serially per coid,
   so this matches reality. A caller that multiplexes fills across
   threads must wrap `on_fill` in their own mutex.

2. **No cross-side netting.** BUY and SELL on the same symbol are
   tracked as separate position projections. The cross-side net
   position is the P&L attribution layer's job
   (`pnl_attribution_per_fill_p7exec_089`), not ours.

3. **Late fills carry no semantic content.** They are journaled for
   forensics only. The downstream observers SHOULD filter
   `WHERE liquidity != 'LATE_FILL_REJECTED'` on their own queries if
   they care about clean fills.

4. **No schema migration story.** This component is v0.1.0 and the
   schema may evolve. A future migration will be in-place
   `ALTER TABLE` with a version column. Not implemented in v0.1.0.

5. **WAL autocheckpoint tuning.** The default sqlite threshold
   (~4MB) introduces multi-millisecond p99 spikes because the
   in-line checkpoint merges the WAL into the main DB. We raise the
   threshold to ~40MB (`wal_autocheckpoint=10000`) so the per-fill
   critical section never blocks on the merge. The runner's recon
   tick (or a separate low-priority thread) is responsible for
   calling `PRAGMA wal_checkpoint(PASSIVE)` periodically. If the
   runner forgets, the WAL grows unboundedly until the process
   exits. This is acceptable for the paper-trading v1 deployment.

6. **Live-report trust.** `LiveOrderReport` rows are taken at face
   value from the connector's fetch. If the connector fetches
   `allOrders` with stale data (the venue caches by update time),
   the divergence is real (and surfaced), but it's the runner's job
   to detect staleness in the fetch, not ours.

## 8. Out of scope (deliberate, for v0.1.0)

- Cross-process sharing (Redis, Kafka). See §2.4 above.
- Cross-side netting. See §7.2.
- Schema versioning / migration.
- Multi-threaded concurrency (see §7.1).
- Threshold-based suppression of divergence (lives in
  `recon_drift_alert`).
- Per-strategy aggregation beyond the `strategy_id` tag on each
  fill event. Aggregating per-strategy across fills is the strategy
  P&L layer's job.
- Optional pre-existing `execution/runner.py` integration (currently
  the runner does not exist on the workdir branch — see §9).

## 9. Runner.py status (2026-07-26)

The issue SMA-36243 calls for extending `execution/runner.py` to
host the new `ExecutionRunner` subclass. As of this component's first
commit, `runner.py` does NOT exist — only stale
`__pycache__/runner.cpython-*.pyc` files remain. The wire-up is:

```python
# in execution/runner.py
from execution.shadow_book_p7exec_072 import (
    ShadowBook, ShadowBookJournal, ShadowFillEvent,
    LiveOrderReport, ReconciliationRow,
)

class ExecutionRunner:
    def __init__(self, ...):
        self.shadow_journal = ShadowBookJournal(
            state_dir / "shadow_book.sqlite"
        )
        self.shadow_book = ShadowBook(self.shadow_journal)

    def on_fill(self, fill_row: dict):
        # translate connector row → ShadowFillEvent, then:
        self.shadow_book.on_fill(ShadowFillEvent(
            ts_ns=fill_row["ts_ns"],
            client_order_id=fill_row["client_order_id"],
            trade_id=fill_row["trade_id"],
            symbol=fill_row["symbol"],
            side=fill_row["side"],
            qty=fill_row["qty"],
            price=fill_row["price"],
            liquidity=fill_row["liquidity"],
            commission=fill_row.get("commission", 0.0),
            commission_asset=fill_row.get("commission_asset", "USDT"),
            ts_exchange_ns=fill_row.get("ts_exchange_ns", 0),
            strategy_id=fill_row.get("strategy_id"),
        ))

    def on_terminal_status(self, coid: str, status: str, ts_ns: int):
        self.shadow_book.finalize_order(coid, status, ts_ns=ts_ns)

    def on_recon_tick(self):
        # Fetch /fapi/v1/allOrders, build LiveOrderReport list, then:
        reports = [LiveOrderReport(...) for raw in fetch_all_orders()]
        self.shadow_book.record_live_reports(reports)
        for row in self.shadow_book.reconcile():
            if row.only_in_live or not row.status_match:
                # escalate to drift alert
                drift_queue.put(("DRIFT", row))
```

The wire contract (ShadowFillEvent shape, terminal statuses,
idempotency rules, reconciliation semantics) is pinned in
`INTERFACE.md` so the wire-up is mechanical and the journal schema
does not need to change.