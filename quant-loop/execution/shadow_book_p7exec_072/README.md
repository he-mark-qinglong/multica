# shadow_book — P7-EXEC-072

The strategy-side reconstruction ("shadow") of every order's fill
history, maintained alongside the venue-truth ("live") view surfaced by
the connector (see `SPEC_live_paper_connector_binance_usdm.md §4.1`
for the live trade log). Produces a per-`client_order_id` and
per-`symbol` reconciliation row so a downstream drift alert (e.g.
`recon_drift_alert`, sibling recon family) can act on the divergence
without re-deriving it.

## Use

```python
from pathlib import Path
from execution.shadow_book_p7exec_072 import (
    ShadowBook, ShadowBookJournal, ShadowFillEvent,
    LiveOrderReport, reconcile,
)

journal = ShadowBookJournal(Path("state/shadow_book.sqlite"))
book = ShadowBook(journal)

# Hot path: every fill event from the connector.
event = ShadowFillEvent(
    ts_ns=time.time_ns(),
    client_order_id="vpvr_btc_long_20260725T1430_abc12",
    trade_id="t-12345",
    symbol="BTCUSDT",
    side="BUY",
    qty=0.010,
    price=67123.4,
    liquidity="taker",
    strategy_id="vpvr_btc_long",
)
order_state = book.on_fill(event)
print(order_state.total_qty, order_state.avg_price, order_state.fill_count)
# → 0.01 67123.4 1

# More fills arrive ...
order_state2 = book.on_fill(ShadowFillEvent(
    ts_ns=time.time_ns(),
    client_order_id="vpvr_btc_long_20260725T1430_abc12",
    trade_id="t-12350",
    symbol="BTCUSDT", side="BUY", qty=0.005, price=67130.0,
    liquidity="taker", strategy_id="vpvr_btc_long",
))
print(order_state2.avg_price)  # → 67125.6

# Position projection: aggregates every fill across every coid for
# (BTCUSDT, BUY).
pos = book.snapshot_position("BTCUSDT", "BUY")
print(pos.net_qty, pos.avg_price, pos.fill_count)

# Order reached terminal status.
book.finalize_order("vpvr_btc_long_20260725T1430_abc12", "FILLED")

# A late fill arrives — exception raised, event still journaled with
# LATE_FILL_LIQUIDITY for forensics.
try:
    book.on_fill(ShadowFillEvent(
        ts_ns=time.time_ns(),
        client_order_id="vpvr_btc_long_20260725T1430_abc12",
        trade_id="t-12399", symbol="BTCUSDT", side="BUY",
        qty=0.001, price=67140.0, liquidity="taker",
        strategy_id="vpvr_btc_long",
    ))
except LateShadowFillRejected as e:
    print("late fill:", e)
```

## Cold-path: reconciliation against venue truth

```python
# Once per minute, the runner fetches /fapi/v1/allOrders and feeds
# each row as a LiveOrderReport. record_live_reports persists them
# to the journal; reconcile() returns one ReconciliationRow per
# client_order_id that appears in either side.
live_reports = []
for raw_row in connector.fetch_all_orders():
    live_reports.append(LiveOrderReport(
        client_order_id=raw_row["clientOrderOrderId"],
        symbol=raw_row["symbol"],
        side=raw_row["side"],
        total_qty=float(raw_row["executedQty"]),
        avg_price=float(raw_row["avgPrice"]),
        fill_count=int(raw_row["cumQty"]),
        terminal_status=raw_row["status"],
        terminal_ts_ns=raw_row["updateTime"] * 1_000_000,
        received_at_ns=time.time_ns(),
    ))
book.record_live_reports(live_reports)

for row in book.reconcile():
    if row.only_in_live:
        # venue reported an order we never saw — likely a missed fill
        alert_queue.put(("MISSED_FILL", row))
    elif row.only_in_shadow:
        # we saw an order the venue hasn't reported yet — likely
        # user-data WS lag; reconcile again in 5s
        alert_queue.put(("WS_LAG", row))
    elif not row.status_match or abs(row.qty_diff) > 1e-9:
        # divergence — drift alert layer decides threshold
        alert_queue.put(("DRIFT", row))
```

## Constraints (MAP-P7)

- Hot path < 250us per `on_fill` (median well under; see
  `evidence/bench_shadow_book.json`).
- Every fill lands in `shadow_fill_events` — NEVER silently dropped.
- Local state journaled via sqlite WAL (`PRAGMA wal_autocheckpoint=10000`
  raises the checkpoint threshold so the per-fill critical section
  never blocks on a WAL merge).
- Folder suffix `_p7exec_072` only (never `_v1` / `_v2`).
- Pure helpers only — no I/O at module level.

## See also

- `SPEC.md` — extended design doc, alternatives, failure modes.
- `INTERFACE.md` — wire contract (event schemas, terminal statuses,
  idempotency rules, reconciliation semantics).
- `test_shadow_book.py` — unit tests (no pytest dep).
- `test_smoke.py` — end-to-end smoke vs synthetic connector feed +
  synthetic live snapshot.
- `bench_shadow_book.py` — latency benchmark, writes JSON to
  `evidence/bench_shadow_book.json`.