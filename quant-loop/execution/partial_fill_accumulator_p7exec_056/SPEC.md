# partial_fill_accumulator — Extended Spec (P7-EXEC-056)

> Companion to `README.md` (usage) and `INTERFACE.md` (wire
> contract). This document records the design rationale, the
> alternatives considered, and the known failure modes.

## 1. Definition

A *fill event* is one row from the connector's trade log
(SPEC_live_paper_connector_binance_usdm §4.1). For each
`client_order_id` the live-paper connector emits between 1
(single fill, order size < minimum chunk) and N (large limit
order sliced by the matching engine) fill events before the
order reaches a terminal status.

The connector's trade-log rows are immutable and
event-ordered. But every downstream observer
(`slippage_attribution_p7exec_043`,
`venue_fill_quality_p7exec_080`, etc.) wants a per-order
rolling aggregate, not the raw stream. Recomputing that
aggregate from scratch on every event is wasteful and races
on cold-start. So we have one component that maintains the
aggregate and exposes it via O(1) lookups: the
**partial_fill_accumulator**.

## 2. Why this component

Three alternatives considered:

### 2.1. Every observer computes its own aggregate

Each observer (`slippage_attribution`, `venue_fill_quality`,
`pnl_attribution_per_fill`) folds the stream into its own
per-coid dict. Rejected:

- Three sqlite WAL INSERTs per fill (one per observer) instead
  of one shared event log + N in-memory folds.
- Three dict updates and three for-loops on the hot path,
  rather than one shared state + N cheap dict reads.
- Three cold-start recovery loops instead of one.

### 2.2. Make the connector compute the aggregate

The connector's hot path is already saturated by the
order-dispatch + kill-switch + sizing pipeline (§1 + §2 + §3
of the live-paper spec). Adding a per-fill aggregation would
couple the connector to all its observers' data shapes. The
connector owns order lifecycle, not order analytics.

### 2.3. A separate persistence layer (Redis / Kafka)

Use the existing ops infra (Redis, Kafka) as the event bus.
Rejected because:

- Adds an external dependency to the hot path (the latency
  budget is 250us in-process; Redis round-trip is 100-500us).
- Requires the connector to depend on a connection manager,
  credentials, reconnection logic. Too much surface for a
  in-process aggregator.
- The MAP-P7 project rule is "local state journaled
  (write-ahead-log / sqlite)" — the intent is in-process
  journal, not external bus.

So: in-process accumulator with sqlite WAL journal, single
INSERT per fill, single UPSERT per fill. The shared journal
becomes the canonical event log; observers can subscribe via
sqlite triggers or read on demand.

## 3. Hot-path design

The hot path per `on_fill` does:

1. Dict lookup by `client_order_id` (O(1) average, ~50ns).
2. If unknown, hydrate from `partial_fill_states` table
   (one SELECT, ~10-30us cold-cache, ~1us warm-cache).
3. Branch on terminal status:
   - Terminal → journal late event (one INSERT), raise.
   - Not terminal → fold fill (two floats, two multiplies,
     one division, one branch — sub-microsecond pure work).
4. INSERT into `partial_fill_events` with `ON CONFLICT DO
   NOTHING` on `(coid, trade_id)` — duplicate is silent
   idempotent.
5. UPSERT into `partial_fill_states` (one INSERT … ON
   CONFLICT DO UPDATE).

Total budget for the not-terminal case: ~120us median,
~250us p99 on a warm journal (see
`evidence/bench_partial_fill_accumulator.json`).

The hot-path is NOT instrumented with logging or metrics —
the bench script measures them externally. In production, the
runner wraps the call in its own metrics layer
(`latency_metrics_p7exec_078`).

## 4. Late-fill semantics

Why journal the late event instead of silently dropping it:

- The constraint is **NEVER silently drop fills**. A late
  fill is still a fill — the venue reports it; the
  connector's user-data stream delivers it; the connector
  passes it to the accumulator. Dropping it on the floor
  would corrupt the audit trail.
- The constraint does NOT mean **always accept the fill**.
  After a terminal status the order's state is final; the
  qty and avg_price must not change.

So: journal the late event with a sentinel `liquidity`
(`LATE_FILL_REJECTED`), do not update state, raise. The
connector catches the exception, logs, and continues. The
journal row stays so a forensic audit can see "venue sent
this ack 30 seconds after we observed FILLED — investigate".

## 5. Recovery / cold-start

The journal is the source of truth. Cold start:

1. Open the journal (the bootstrap is idempotent).
2. Hydrate in-memory cache. Two options:
   - Caller enumerates `client_order_id`s from some external
     source (e.g. positions log, `position_reconciler` table)
     and calls `acc.replay(coid)` for each.
   - Run `SELECT DISTINCT client_order_id FROM
     partial_fill_events` and replay each.
3. From this point, `on_fill` uses the rehydrated cache. The
   `fetch_state(coid)` call inside `on_fill` is a fallback
   for the lazy path.

The journal survives process crashes (WAL + synchronous=NORMAL)
but not a `kill -9` of sqlite itself (acceptable for
in-process state; not for the canonical trade log, which is
also journaled by the connector in `trades.jsonl`).

## 6. Known limitations

1. **Single-threaded per coid.** The component has no
   internal lock. The connector's user-data WS delivers
   events serially per coid, so this matches reality. A
   caller that multiplexes fills across threads must wrap
   `on_fill` in their own mutex.

2. **No position-level aggregation.** The accumulator tracks
   per-coid state only. Per-strategy / per-symbol / per-venue
   aggregations are downstream observers'
   responsibility (`venue_fill_quality_p7exec_080`,
   `pnl_attribution_per_fill_p7exec_089`).

3. **Late fills carry no semantic content.** They are
   journaled for forensics only. The downstream observers
   SHOULD filter `WHERE liquidity != 'LATE_FILL_REJECTED'`
   on their own queries if they care about clean fills.

4. **No schema migration story.** This component is v0.1.0
   and the schema may evolve. A future migration will be
   in-place `ALTER TABLE` with a version column on
   `partial_fill_states`. Not implemented in v0.1.0.

5. **WAL durability vs process crash.** A
   `synchronous=NORMAL` WAL loses at most the in-flight
   transaction. The connector's canonical trade log
   (`trades.jsonl`) remains the durable record; the
   accumulator is the hot-path cache rebuilt from that
   log on cold start.

## 7. Out of scope (deliberate, for v0.1.0)

- Cross-process sharing (Redis, Kafka). See §2.3 above.
- Per-strategy / per-symbol / per-venue aggregations.
- Schema versioning / migration.
- Multi-threaded concurrency (see §6.1).
- Optional pre-existing `execution.runner` integration
  (currently the runner does not exist in the
  `sma-36506` checkout — see SPEC §8).

## 8. Runner.py status (2026-07-26)

The issue SMA-36243 calls for extending `execution/runner.py`
to host the new `ExecutionRunner` subclass. As of this
component's first commit, `runner.py` does NOT exist on
branch `sma-36506` — only stale `__pycache__/runner.cpython-*.pyc`
files remain. The previous agent's journal-schema edits
(described in the 2026-07-25 13:57 comment) were not
persisted.

Therefore this component ships as a **self-contained
package** with its own journal. When `runner.py` is
re-introduced, the wire-up is:

```python
# in execution/runner.py
from execution.partial_fill_accumulator_p7exec_056 import (
    Accumulator, FillEvent, PartialFillJournal,
)

class ExecutionRunner:
    def __init__(self, ...):
        self.partial_fill_journal = PartialFillJournal(
            state_dir / "partial_fills.sqlite"
        )
        self.partial_fill_acc = Accumulator(self.partial_fill_journal)

    def on_fill(self, fill_row: dict):
        # translate connector row → FillEvent, then:
        self.partial_fill_acc.on_fill(FillEvent(
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
        ))
```

The wire contract (FillEvent shape, terminal statuses,
idempotency rules) is pinned in `INTERFACE.md` so the wire-up
is mechanical and the journal schema does not need to change.