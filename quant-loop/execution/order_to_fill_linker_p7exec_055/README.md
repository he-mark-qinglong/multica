# order_to_fill_linker — P7-EXEC-055

Correlate the venue-assigned `orderId` with the
connector-assigned `client_order_id` and the strategy's order
*intent*. Every FillReport is journaled exactly once, tagged
with the originating intent (or flagged `is_orphan` if no intent
was known at the time), and the intent's status is advanced
through `PENDING_ACK → ACKED → PARTIALLY_FILLED → FILLED`.

## Use

```python
from pathlib import Path
from execution.order_to_fill_linker_p7exec_055 import (
    Linker, OrderIntent, FillReport, OrderToFillJournal,
    IntentMismatch, UnknownClientOrderId, OrderIdAlreadyBound,
)

journal = OrderToFillJournal(Path("state/order_to_fill.sqlite"))
linker = Linker(journal)
linker.recover_pending()  # rebuilds cache from journal on cold start

# 1. Register the intent BEFORE sending the order out.
intent = OrderIntent(
    client_order_id="vpvr_btc_long_20260725T1430_abc12",
    symbol="BTCUSDT",
    side="BUY",
    intended_qty=0.010,
    intent_ts_ns=time.time_ns(),
    strategy_id="vpvr_reversion_1m",
)
linker.register_intent(intent)

# 2. When the venue returns an orderId, bind it.
linker.bind_order_id("vpvr_btc_long_20260725T1430_abc12", 412341234)

# 3. On every FillReport (WS ORDER_TRADE_UPDATE or REST ack).
report = FillReport(
    ts_ns=time.time_ns(),
    order_id=412341234,
    client_order_id="vpvr_btc_long_20260725T1430_abc12",
    trade_id="t-12345",
    symbol="BTCUSDT",
    side="BUY",
    qty=0.010,
    price=67123.4,
    cum_filled_qty=0.010,
    avg_fill_price=67123.4,
    order_status="FILLED",
)
record = linker.on_fill_report(report)
print(record.is_orphan, record.intent_status_after)
# → False FILLED

# 4. A FillReport arriving BEFORE the intent is registered
#    (e.g. WS reconnect with a missed history replay) is journaled
#    as an orphan — never silently dropped.
orphan_report = FillReport(
    ts_ns=time.time_ns(),
    order_id=999999999,
    client_order_id="",                # venue didn't echo our coid
    trade_id="t-99999",
    symbol="ETHUSDT",
    side="SELL",
    qty=0.05,
    price=3500.0,
    cum_filled_qty=0.05,
    avg_fill_price=3500.0,
    order_status="FILLED",
    source="WS",
)
orphan_record = linker.on_fill_report(orphan_report)
print(orphan_record.is_orphan, orphan_record.client_order_id)
# → True ""  (still journaled for the next reconciler)
```

## Constraints (MAP-P7)

- Hot path < 250us per `on_fill_report` (median well under; see
  `evidence/bench_order_to_fill_linker.json`).
- Every fill lands in `order_fill_links` — NEVER silently
  dropped (orphan fills included).
- Folder suffix `_p7exec_NNN` only (never `_v1` / `_v2`).
- Pure helpers only — no I/O at module level.

## See also

- `SPEC.md` — extended design doc, alternatives, failure modes.
- `INTERFACE.md` — wire contract (OrderIntent / FillReport /
  LinkRecord shapes, intent status vocabulary, idempotency
  rules, runner wire-up snippet).
- `test_order_to_fill_linker.py` — unit tests (no pytest dep).
- `test_smoke.py` — end-to-end smoke vs synthetic fill journal.
- `bench_order_to_fill_linker.py` — latency benchmark.