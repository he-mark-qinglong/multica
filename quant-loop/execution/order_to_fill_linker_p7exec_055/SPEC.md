# order_to_fill_linker — Extended Spec (P7-EXEC-055)

> Companion to `README.md` (usage) and `INTERFACE.md` (wire
> contract). This document records the design rationale, the
> alternatives considered, and the known failure modes.

## 1. Definition

An *intent* is a strategy's request to the connector: "I want to
buy 0.010 BTC at 67123.4 GTC." The connector wraps it with a
`client_order_id`, sends it to the venue, and gets back an
`orderId` from the matching engine. The venue then streams
`ORDER_TRADE_UPDATE` events (or returns fills inside a REST GET
response), each carrying `(orderId, tradeId, qty, price,
cum_filled_qty, order_status)`.

`client_order_id` and `orderId` are two different identifiers
for the same trade:

- `client_order_id` is connector-generated, deterministic, and
  survives across process restarts. It is what the strategy
  uses to refer to its own order.
- `orderId` is venue-assigned, returned only after the matching
  engine accepts the order. It is what the venue uses to refer
  to the order on the user-data stream.

A *FillReport* arrives identified by **either** of these — the
WS user-data stream sometimes carries `orderId` but echoes our
`origClientOrderId` only on the first update of a session, and
REST acks always echo both. After a WS reconnect the new
session loses the `origClientOrderId` echo and we get
`orderId`-only updates.

The *linker* is the component that joins the two views: every
incoming FillReport is journaled once, tagged with the
`client_order_id` of the originating intent (or marked
`is_orphan` if no intent is known), and the intent's status is
advanced through `PENDING_ACK → ACKED → PARTIALLY_FILLED → FILLED`
accordingly.

## 2. Why this component

Three alternatives considered:

### 2.1. Every observer does its own join

Each observer (`partial_fill_accumulator`,
`venue_fill_quality`, `pnl_attribution_per_fill`) walks back from
the trade-log row to the order-status row to the intent row,
re-doing the join every time. Rejected:

- Three identical sqlite WAL SELECTs per fill.
- Three separate journal writes for the (order_id → coid) mapping.
- Three cold-start recovery loops instead of one.

### 2.2. The connector computes the join

The connector's hot path is already saturated by the
order-dispatch + kill-switch + sizing pipeline (§1 + §2 + §3
of the live-paper spec). Adding a per-fill (order_id → coid)
mapping would couple the connector to the linker's data shapes.
The connector owns order lifecycle, not order-intent bookkeeping.

### 2.3. Use the connector's existing order-status row directly

The connector already writes one order-status row per state
update. Rejected because:

- The connector's row does NOT carry the `strategy_id`, `tags`,
  `intended_strategy_role` — those are strategy-side metadata
  the connector strips before sending to the venue.
- The connector's row is an append-only log; it does not expose
  the current intent status (it would need a stateful cursor).
- We want a per-coid *intent* state machine that lives across
  process restarts; the connector's row is best-effort and
  rebuilt on every restart.

So: in-process linker with sqlite WAL journal, one INSERT into
`order_fill_links` per fill, one UPSERT into `order_intents`
per status transition. The shared journal becomes the canonical
intent↔fill mapping; observers can read on demand.

## 3. Hot-path design

The hot path per `on_fill_report` does:

1. Validate the report (one branch, sub-microsecond).
2. Resolve the bound intent: cache lookup → journal fallback
   (one sqlite SELECT if the cache is cold, ~10-30us).
3. Branch on orphan vs bound:
   - **Bound** → validate side / symbol agreement, INSERT into
     `order_fill_links`, apply status transition, UPSERT into
     `order_intents` (only if the transition changed the
     status), append to `order_status_events`.
   - **Orphan** → INSERT into `order_fill_links` with
     `is_orphan = 1` and `client_order_id = ""`. No UPSERT into
     `order_intents` (no intent to update).
4. Build the `LinkRecord` and return it.

Total budget for the bound path: ~150us median, ~250us p99 on a
warm journal (see `evidence/bench_order_to_fill_linker.json`).
The orphan path is cheaper (one INSERT only).

The hot-path is NOT instrumented with logging or metrics — the
bench script measures them externally. In production, the runner
wraps the call in its own metrics layer
(`latency_metrics_p7exec_078`).

## 4. Why never drop fills

The MAP-P7 constraint is **NEVER silently drop fills**. The
linker honours this in three ways:

1. **Orphan fills are journaled, not dropped.** A FillReport
   whose `orderId` is not bound to any registered intent lands
   in `order_fill_links` with `is_orphan = 1`. A downstream
   `position_reconciler` can pick them up later, after the
   intent is registered (e.g. on the next restart).
2. **Mismatch fills are journaled BEFORE raising.** If a fill's
   side / symbol disagrees with the bound intent, the journal
   row is written first; only then does the linker raise
   `IntentMismatch`. The runner may choose to swallow the
   exception, but the audit trail is preserved.
3. **Duplicate fills are silent-idempotent, not dropped.** A
   duplicate `(order_id, trade_id)` returns the previously
   persisted state. The journal row IS already there (the
   `INSERT OR IGNORE` is the canonical idempotency guard).

The journal row exists even when the component raises — there
is no path through `on_fill_report` that does not write at
least one row to `order_fill_links`.

## 5. Idempotency

The journal uses `UNIQUE(order_id, trade_id)` on
`order_fill_links`. A duplicate `on_fill_report` for the same
`(order_id, trade_id)` is silently idempotent: the existing
link row is left untouched, and the returned `LinkRecord`
reflects the **previously persisted** intent status (not the
"would-have-been" status). This matches the connector's
behaviour where a re-sent WS event after a reconnect produces
the same `(order_id, trade_id)` for an already-recorded fill.

`bind_order_id` is also idempotent on the `(client_order_id,
order_id)` pair: re-binding the same orderId to the same coid
returns the existing intent unchanged. Re-binding the same
orderId to a **different** coid raises `OrderIdAlreadyBound`.

`register_intent` is idempotent on `client_order_id`: re-
registering the same coid updates the symbol/side/qty fields
but does NOT reset `intent_status` (a re-register after the
venue has already FILLED will not rewind the intent back to
`PENDING_ACK`).

## 6. Recovery / cold-start

The journal is the source of truth. Cold start:

1. Open the journal (the bootstrap is idempotent).
2. Call `linker.recover_pending()` — walks `order_intents` and
   rebuilds the in-memory cache.
3. From this point, `on_fill_report` uses the rehydrated cache.
   The journal fallback in `_resolve_intent_for_report` is a
   belt-and-braces guard for the lazy path.

The journal survives process crashes (WAL + synchronous=NORMAL)
but not a `kill -9` of sqlite itself (acceptable for in-process
state; not for the canonical trade log, which is also journaled
by the connector in `trades.jsonl`).

## 7. Known limitations

1. **Single-threaded per linker instance.** The component has
   no internal lock. The connector's user-data WS delivers
   events serially, so this matches reality. A caller that
   multiplexes fills across threads must wrap `on_fill_report`
   in their own mutex.

2. **Orphan fills carry no semantic content.** They are
   journaled for forensics only. The downstream reconciler
   SHOULD iterate `WHERE is_orphan = 1` after every restart and
   attempt to bind them to a newly-registered intent that shares
   the same `order_id`.

3. **No schema migration story.** This component is v0.1.0 and
   the schema may evolve. A future migration will be in-place
   `ALTER TABLE` with a version column on `order_intents`. Not
   implemented in v0.1.0.

4. **WAL durability vs process crash.** A `synchronous=NORMAL`
   WAL loses at most the in-flight transaction. The connector's
   canonical trade log (`trades.jsonl`) remains the durable
   record; the linker is the hot-path cache rebuilt from that
   log on cold start.

## 8. Out of scope (deliberate, for v0.1.0)

- Cross-process sharing (Redis, Kafka). See §2.3 above.
- Schema versioning / migration.
- Multi-threaded concurrency (see §7.1).
- Reconciliation of orphan fills against the position log. This
  is the next step (P7-EXEC-058 `position_reconciler`), but it
  is out of scope here. The linker only journals; it does not
  reconcile.

## 9. Failure modes

| mode                                              | behaviour                                          |
|---------------------------------------------------|----------------------------------------------------|
| Unknown `order_id` on first FillReport            | Journaled as `is_orphan = 1`. Returned `LinkRecord.is_orphan = True`. No exception. |
| Known bound intent, side mismatch                 | Journal row written, then `IntentMismatch` raised. |
| Duplicate `(order_id, trade_id)`                  | Silent idempotent. Returns existing state. |
| FillReport with terminal status after terminal     | Intent status is NOT downgraded. Journal row written for forensics. |
| `bind_order_id` on unknown coid                   | Raises `UnknownClientOrderId`.                     |
| `bind_order_id` to an orderId bound elsewhere     | Raises `OrderIdAlreadyBound`.                     |
| Replay after restart                              | `recover_pending()` rebuilds the cache.            |
| Bad input (empty coid, bad side, ≤0 qty, unknown status) | `ValueError` raised before any journal write. |

## 10. Runner.py status (2026-07-26)

The issue SMA-36243 (parallel to this one) calls for extending
`execution/runner.py` to host the new `ExecutionRunner`
subclass. As of this component's first commit, `runner.py` does
NOT exist on branch `sma-36506` — only stale
`__pycache__/runner.cpython-310.pyc` files remain.

Therefore this component ships as a **self-contained package**
with its own journal, and the `INTERFACE.md` §11 wire-up snippet
documents the additive wiring for when `runner.py` is
re-introduced. The wire contract (FillReport shape, intent
status vocabulary, idempotency rules) is pinned in
`INTERFACE.md` so the wire-up is mechanical and the journal
schema does not need to change.