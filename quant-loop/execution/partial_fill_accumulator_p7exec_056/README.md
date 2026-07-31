# partial_fill_accumulator — P7-EXEC-056

Aggregate partial-fill events for a single `client_order_id` into
a running state (cumulative qty, VWAP price, fill count, terminal
status) and journal every event so the run is recoverable on cold
start.

## Use

```python
from pathlib import Path
from execution.partial_fill_accumulator_p7exec_056 import (
    Accumulator, FillEvent, PartialFillJournal, LateFillRejected,
)

journal = PartialFillJournal(Path("state/partial_fills.sqlite"))
acc = Accumulator(journal)

event = FillEvent(
    ts_ns=time.time_ns(),
    client_order_id="vpvr_btc_long_20260725T1430_abc12",
    trade_id="t-12345",
    symbol="BTCUSDT",
    side="BUY",
    qty=0.010,
    price=67123.4,
    liquidity="taker",
)
state = acc.on_fill(event)
print(state.total_qty, state.avg_price, state.fill_count)
# → 0.01 67123.4 1

# More fills arrive ...
state2 = acc.on_fill(FillEvent(
    ts_ns=time.time_ns(),
    client_order_id="vpvr_btc_long_20260725T1430_abc12",
    trade_id="t-12350",
    symbol="BTCUSDT", side="BUY", qty=0.005, price=67130.0,
    liquidity="taker",
))
print(state2.avg_price)  # → 67125.6

# Order reached terminal status
final = acc.finalize("vpvr_btc_long_20260725T1430_abc12", "FILLED")
print(final.total_qty, final.fill_count)  # → 0.015 2

# A late fill arrives — exception raised, event still journaled.
try:
    acc.on_fill(FillEvent(
        ts_ns=time.time_ns(),
        client_order_id="vpvr_btc_long_20260725T1430_abc12",
        trade_id="t-12399", symbol="BTCUSDT", side="BUY",
        qty=0.001, price=67140.0, liquidity="taker",
    ))
except LateFillRejected as e:
    print("late fill:", e)  # forensically journaled with LATE_FILL_LIQUIDITY
```

## Constraints (MAP-P7)

- Hot path < 250us per `on_fill` (median well under; see
  `evidence/bench_partial_fill_accumulator.json`).
- Every fill lands in `partial_fill_events` — NEVER silently dropped.
- Folder suffix `_p7exec_NNN` only (never `_v1` / `_v2`).
- Pure helpers only — no I/O at module level.

## See also

- `SPEC.md` — extended design doc, alternatives, failure modes.
- `INTERFACE.md` — wire contract (FillEvent schema, terminal
  statuses, idempotency rules).
- `test_partial_fill_accumulator.py` — unit tests (no pytest dep).
- `test_smoke.py` — end-to-end smoke vs synthetic connector feed.
- `bench_partial_fill_accumulator.py` — latency benchmark.