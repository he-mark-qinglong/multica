# INTERFACE — P7-EXEC-056 wire contract

> Companion to `SPEC.md` (rationale) and `README.md` (usage).
> This document pins the public surface so a future `runner.py`
> wire-up can call this component without re-reading the
> implementation.

## 1. FillEvent — input

```python
@dataclass(frozen=True)
class FillEvent:
    ts_ns: int                      # connector wall clock (time.time_ns())
    client_order_id: str            # idempotency / journal key
    trade_id: str                   # venue trade id (idempotency with coid)
    symbol: str                     # e.g. "BTCUSDT"
    side: str                       # "BUY" | "SELL"
    qty: float                      # base asset (e.g. BTC)
    price: float                    # quote asset
    liquidity: str                  # "taker" | "maker"
    commission: float = 0.0
    commission_asset: str = "USDT"
    ts_exchange_ns: int = 0         # 0 = unknown / not provided
```

Fields are all required except `commission`, `commission_asset`,
`ts_exchange_ns`. A `FillEvent` with a missing `client_order_id`
or `trade_id` raises `ValueError` at the connector layer (not in
this component); a missing `qty` or `price` is the caller's bug.

## 2. PartialFillState — output

```python
@dataclass(frozen=True)
class PartialFillState:
    client_order_id: str
    symbol: str
    side: str
    total_qty: float
    avg_price: float                # volume-weighted average
    notional_usd: float             # total_qty * avg_price
    fill_count: int
    first_fill_ts_ns: int
    last_fill_ts_ns: int
    terminal_status: Optional[str]  # None | "FILLED" | "CANCELED" | "EXPIRED" | "REJECTED"
    terminal_ts_ns: Optional[int]
```

`avg_price` is the VWAP across all fills seen so far. On the
first fill, `avg_price = price` and `notional_usd = qty * price`.
`notional_usd` is kept explicit so dashboards can read it
without recomputing.

## 3. Terminal statuses

| status      | when set                                       |
|-------------|------------------------------------------------|
| `None`      | order is still receiving fills                 |
| `FILLED`    | order reached full intended qty                 |
| `CANCELED`  | explicit cancellation (may have residual qty)  |
| `EXPIRED`   | time-in-force expired (GTD, IOC, FOK, GTX)     |
| `REJECTED`  | venue rejected the order; no fills expected     |

Once `terminal_status` is set, the next `on_fill` for that
`client_order_id` journals the late event with
`liquidity = "LATE_FILL_REJECTED"` (preserving the forensic
record) and raises `LateFillRejected`. The state is NOT
mutated by the late fill.

## 4. Idempotency

The journal uses `UNIQUE(client_order_id, trade_id)` on
`partial_fill_events`. A duplicate `on_fill` for the same
`(client_order_id, trade_id)` is silently idempotent: the
existing state is returned and no second journal row is
written. The component does NOT raise on duplicates — that is
the caller's job if they want strict-uniqueness semantics.

This matches the connector's behaviour where a re-sent WS event
from the user-data stream after a reconnect produces the same
`trade_id` for an already-recorded fill.

## 5. Terminal status idempotency

`finalize(coid, terminal_status)` is also idempotent: calling it
twice with the same status returns the existing state. Calling
it with a DIFFERENT terminal status after one is already set is
a no-op (returns existing state) — the journal does NOT overwrite
a finalised state with a contradicting marker. To force a status
change, the caller must clear the state first (deleting the
journal row).

## 6. Persistence model

```
sqlite WAL @ state/partial_fills.sqlite
├── partial_fill_events        # append-only log, one row per fill
│   UNIQUE(client_order_id, trade_id)
│   INDEX(client_order_id, ts_ns)
└── partial_fill_states        # current state per coid (UPSERT)
    PRIMARY KEY(client_order_id)
    INDEX(terminal_status)
```

`journal_mode=WAL`, `synchronous=NORMAL`. Median per-INSERT cost
on a warm journal is in the 60-180us band (see
`evidence/bench_partial_fill_accumulator.json`). WAL flushes
the journal at every commit, so a process crash loses at most
one in-flight transaction.

## 7. Failure modes

| mode                                  | behaviour                                          |
|---------------------------------------|----------------------------------------------------|
| Unknown `client_order_id` on first fill | New state created from the first event; both journal writes happen. |
| Known non-terminal `client_order_id`  | Fold fill, journal both writes.                    |
| Known terminal `client_order_id`      | Journal event with `LATE_FILL_LIQUIDITY`, raise `LateFillRejected`. State unchanged. |
| Duplicate `(coid, trade_id)`          | Return existing state; no journal write.           |
| Unknown `client_order_id` on `finalize` | Raise `KeyError`. State unchanged.                |
| Unknown `terminal_status` value       | Raise `ValueError`.                                |
| Caller double-`finalize` same status  | Return existing state; no journal write.           |
| Caller double-`finalize` different status | Return existing state; no journal write (no contradiction written). |
| Replay after restart                  | `replay(coid)` rebuilds state from events; `known_orders()` enumerates cache keys; cold-start loop calls `replay` for every known coid. |

## 8. Recovery / cold-start

```python
journal = PartialFillJournal(Path("state/partial_fills.sqlite"))
acc = Accumulator(journal)
for coid in _enumerate_coids_from_some_external_source():
    acc.replay(coid)
# on_fill now uses the rehydrated cache.
```

If the caller has no external enumeration source, the cold-start
loop can simply call `replay(coid)` after `fetch_state(coid)`
returns non-None. The journal is the source of truth; the
in-memory dict is the hot-path cache.