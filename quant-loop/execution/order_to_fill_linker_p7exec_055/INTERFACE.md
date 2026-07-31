# INTERFACE — P7-EXEC-055 wire contract

> Companion to `SPEC.md` (rationale) and `README.md` (usage).
> This document pins the public surface so a future `runner.py`
> wire-up can call this component without re-reading the
> implementation.

## 1. `OrderIntent` — input (intent side)

```python
@dataclass(frozen=True)
class OrderIntent:
    client_order_id: str            # connector-generated; idempotency key
    symbol: str                     # e.g. "BTCUSDT"
    side: str                       # "BUY" | "SELL"
    intended_qty: float             # base asset (e.g. BTC)
    intent_ts_ns: int               # connector wall clock (time.time_ns())
    order_id: int = 0               # venue-assigned; 0 until first ack
    order_type: str = "LIMIT"
    time_in_force: str = "GTC"
    strategy_id: str = ""
    intent_status: str = "PENDING_ACK"
    last_status_ts_ns: int = 0
```

`register_intent(intent)` validates `client_order_id` is non-empty,
`side ∈ {BUY, SELL}`, and `intended_qty > 0`. A missing or empty
`client_order_id` raises `ValueError` here (not at the connector
layer) so the runner catches a malformed intent before it goes
out.

## 2. `FillReport` — input (fill side)

```python
@dataclass(frozen=True)
class FillReport:
    ts_ns: int                      # connector wall clock
    order_id: int                   # venue-assigned (> 0)
    client_order_id: str            # best-effort; "" if venue didn't echo
    trade_id: str                   # venue trade id (idempotency w/ order_id)
    symbol: str
    side: str
    qty: float
    price: float
    cum_filled_qty: float
    avg_fill_price: float
    order_status: str               # NEW | PARTIALLY_FILLED | FILLED | CANCELED | EXPIRED | REJECTED
    commission: float = 0.0
    commission_asset: str = "USDT"
    liquidity: str = "taker"        # taker | maker
    source: str = "WS"              # WS | REST | RECONCILE | TRADES_FOLD
    ts_exchange_ns: int = 0
```

Fields required: `ts_ns`, `order_id > 0`, `trade_id` non-empty,
`qty >= 0`, `price > 0` (price=0 only for status-only events
where the venue didn't quote a fill — rare; raise ValueError if
`price <= 0`), `order_status ∈ ORDER_STATUSES`. A `qty = 0`
event is allowed and represents a non-fill status event
(REJECTED / EXPIRED). The journal writes `notional_usd = 0` in
that case.
A `FillReport` with an empty `client_order_id` is allowed: the
linker will try to match by `order_id` and (failing that) journal
the fill as `is_orphan = 1`.

## 3. `LinkRecord` — output

```python
@dataclass(frozen=True)
class LinkRecord:
    order_id: int
    client_order_id: str            # "" if is_orphan
    trade_id: str
    intent_status_before: str       # PENDING_ACK if orphan
    intent_status_after: str        # PENDING_ACK if orphan
    is_orphan: bool
    ts_ns: int
    source: str
```

The caller (the runner / reconciler) uses `is_orphan=True` as a
signal to enqueue an investigation or trigger a position
reconciliation. **The component never silently drops a fill**;
even orphan fills are journaled with `is_orphan = 1` so the
audit trail is complete.

## 4. Intent status vocabulary

| status            | when set                                              |
|-------------------|-------------------------------------------------------|
| `PENDING_ACK`     | intent registered, venue has not yet returned an orderId |
| `ACKED`           | venue returned an orderId (or the FillReport's status was NEW) |
| `PARTIALLY_FILLED`| at least one FillReport received; status not terminal  |
| `FILLED`          | FillReport with `order_status == "FILLED"`             |
| `CANCELED`        | FillReport with `order_status == "CANCELED"`           |
| `EXPIRED`         | FillReport with `order_status == "EXPIRED"`            |
| `REJECTED`        | FillReport with `order_status == "REJECTED"`           |

Once a terminal status is set (`FILLED` / `CANCELED` / `EXPIRED` /
`REJECTED`), subsequent `on_fill_report` calls do NOT downgrade
the intent. A late `CANCELED` arriving after `FILLED` is silently
ignored — the journal row is written for forensics but the
intent state stays `FILLED`.

## 5. Order-status vocabulary (Binance USD-M)

| status            | drives intent transition to |
|-------------------|-----------------------------|
| `NEW`             | `ACKED`                     |
| `PARTIALLY_FILLED`| `PARTIALLY_FILLED`          |
| `FILLED`          | `FILLED` (terminal)         |
| `CANCELED`        | `CANCELED` (terminal)       |
| `EXPIRED`         | `EXPIRED` (terminal)        |
| `REJECTED`        | `REJECTED` (terminal)       |

## 6. Idempotency

| operation                   | idempotency key            | behaviour on duplicate            |
|-----------------------------|----------------------------|-----------------------------------|
| `register_intent`           | `client_order_id`          | Coalesce mutable fields; honour durable `intent_status`. Does NOT rewind `PENDING_ACK → FILLED` to `PENDING_ACK`. |
| `bind_order_id`             | `client_order_id` × `order_id` | Returns existing intent unchanged. |
| `bind_order_id` (conflict)  | `order_id` → different coid | Raises `OrderIdAlreadyBound`.     |
| `on_fill_report`            | `(order_id, trade_id)`     | Returns the existing `LinkRecord` if the fill was previously journaled. The journal's `INSERT OR IGNORE` is the canonical idempotency guard. |
| `on_fill_report` (orphan)   | `(order_id, trade_id)`     | First call journals with `is_orphan = 1`; subsequent duplicates are still silently idempotent. |

## 7. Exceptions

| exception                 | when raised                                          | caller action              |
|---------------------------|------------------------------------------------------|----------------------------|
| `IntentMismatch`          | `FillReport.symbol` ≠ bound intent's symbol OR `FillReport.side` ≠ bound intent's side | HALT or log + reconcile. Journal row IS written before raising. |
| `UnknownClientOrderId`    | `bind_order_id` / `fetch_intent` on a coid with no intent (neither in cache nor journal) | The connector never asked for that order; this is a real bug. HALT. |
| `OrderIdAlreadyBound`     | `bind_order_id` finds the orderId already bound to a different coid | Venue or connector bug; HALT and investigate. |
| `ValueError`              | Input validation failure (empty coid, bad side, ≤0 qty, unknown status) | Caller bug; surface to connector. |

The component never raises for **orphan** fills — those are
journaled and returned as a `LinkRecord` with `is_orphan = True`.

## 8. Persistence model

```
sqlite WAL @ state/order_to_fill.sqlite
├── order_intents                # durable record of every intent we sent out
│   PRIMARY KEY(client_order_id)
│   INDEX(order_id)
│   INDEX(intent_status)
├── order_fill_links             # append-only log, one row per fill
│   UNIQUE(order_id, trade_id)   # idempotency guard
│   INDEX(client_order_id, ts_ns)
│   INDEX(order_id, ts_ns)
│   INDEX(is_orphan)
└── order_status_events          # append-only log of venue status updates
    UNIQUE(order_id, ts_ns, status)
    INDEX(order_id, ts_ns)
```

`journal_mode=WAL`, `synchronous=NORMAL`. Per-call cost on a warm
journal is in the 80-220us band (see
`evidence/bench_order_to_fill_linker.json`). WAL flushes the
journal at every commit, so a process crash loses at most one
in-flight transaction.

## 9. Failure modes

| mode                                              | behaviour                                          |
|---------------------------------------------------|----------------------------------------------------|
| Happy path: intent → bind → FillReport            | Linked. `LinkRecord.is_orphan = False`.            |
| Intent side / FillReport side mismatch            | Journal row written, then `IntentMismatch` raised. |
| Intent symbol / FillReport symbol mismatch        | Journal row written, then `IntentMismatch` raised. |
| Duplicate `(order_id, trade_id)`                  | `INSERT OR IGNORE` returns rowcount=0; `LinkRecord` reflects the **previously persisted** intent status. |
| FillReport with `order_status = FILLED` after terminal | Intent stays in terminal status. Journal row written with `cum_filled_qty_after` reflecting post-fill state. |
| Orphan FillReport (no bound intent)               | Journaled with `is_orphan = 1`, `client_order_id = ""`. `LinkRecord.is_orphan = True`. No exception. |
| Replay after restart                              | `recover_pending()` rebuilds the cache from `order_intents`; orphan fills remain in the journal for a future reconciler. |
| `bind_order_id` on unknown coid                   | Raises `UnknownClientOrderId`.                     |
| `bind_order_id` to an orderId bound elsewhere     | Raises `OrderIdAlreadyBound`.                     |

## 10. Recovery / cold-start

```python
journal = OrderToFillJournal(Path("state/order_to_fill.sqlite"))
linker = Linker(journal)
linker.recover_pending()  # rebuilds the cache from the journal
# on_fill_report now uses the rehydrated cache.
```

If the linker is constructed fresh and the runner calls
`on_fill_report` BEFORE `recover_pending()`, the linker falls
back to a single SELECT against the journal per fill (still
O(1) per call, just slower on a cold cache). `recover_pending`
is the recommended warm-up step at boot time.

## 11. Wire-up snippet for `runner.py`

```python
# in execution/runner.py (additive; runner.py may not exist yet
# in the sma-36506 checkout — see SPEC §10).
from execution.order_to_fill_linker_p7exec_055 import (
    Linker, OrderIntent, FillReport, OrderToFillJournal,
    IntentMismatch, UnknownClientOrderId, OrderIdAlreadyBound,
)

class ExecutionRunner:
    def __init__(self, ...):
        self.fill_linker_journal = OrderToFillJournal(
            state_dir / "order_to_fill.sqlite"
        )
        self.fill_linker = Linker(self.fill_linker_journal)
        self.fill_linker.recover_pending()

    def on_intent(self, intent_dict: dict):
        # called BEFORE sending the order to the venue.
        intent = OrderIntent(
            client_order_id=intent_dict["client_order_id"],
            symbol=intent_dict["symbol"],
            side=intent_dict["side"],
            intended_qty=float(intent_dict["quantity"]),
            intent_ts_ns=time.time_ns(),
            order_type=intent_dict.get("order_type", "LIMIT"),
            time_in_force=intent_dict.get("time_in_force", "GTC"),
            strategy_id=intent_dict.get("strategy_id", ""),
        )
        return self.fill_linker.register_intent(intent)

    def on_ack(self, ack_dict: dict):
        # called when the venue returns an orderId.
        coid = ack_dict["client_order_id"]
        order_id = int(ack_dict["order_id"])
        return self.fill_linker.bind_order_id(coid, order_id)

    def on_fill(self, report_dict: dict):
        # called for every ORDER_TRADE_UPDATE or REST fill ack.
        report = FillReport(
            ts_ns=time.time_ns(),
            order_id=int(report_dict["order_id"]),
            client_order_id=report_dict.get("client_order_id", ""),
            trade_id=str(report_dict["trade_id"]),
            symbol=report_dict["symbol"],
            side=report_dict["side"],
            qty=float(report_dict["qty"]),
            price=float(report_dict["price"]),
            cum_filled_qty=float(report_dict.get("cum_filled_qty", 0.0)),
            avg_fill_price=float(report_dict.get("avg_fill_price", 0.0)),
            order_status=report_dict["status"],
            commission=float(report_dict.get("commission", 0.0)),
            commission_asset=report_dict.get("commission_asset", "USDT"),
            liquidity=report_dict.get("liquidity", "taker"),
            source=report_dict.get("source", "WS"),
        )
        record = self.fill_linker.on_fill_report(report)
        if record.is_orphan:
            self.log.warning("orphan fill: %s", record)
        return record
```

The wire contract is pinned here so the runner wire-up is
mechanical and the journal schema does not need to change.