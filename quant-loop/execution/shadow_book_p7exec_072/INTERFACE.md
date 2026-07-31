# INTERFACE — P7-EXEC-072 wire contract

> Companion to `SPEC.md` (rationale) and `README.md` (usage). This
> document pins the public surface so a future `runner.py` wire-up can
> call this component without re-reading the implementation.

## 1. ShadowFillEvent — input (hot path)

```python
@dataclass(frozen=True)
class ShadowFillEvent:
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
    strategy_id: Optional[str] = None  # optional strategy tag
```

Required fields: `ts_ns`, `client_order_id`, `trade_id`, `symbol`,
`side`, `qty`, `price`, `liquidity`. A `ShadowFillEvent` with a
missing `client_order_id` or `trade_id` raises `ValueError` at the
connector layer (not in this component); a missing `qty` or `price` is
the caller's bug.

`strategy_id` is optional. When set, the journal row carries it so the
reconciler can group divergence by strategy. When unset, the field is
`None`.

## 2. ShadowOrderState — output (per coid)

```python
@dataclass(frozen=True)
class ShadowOrderState:
    client_order_id: str
    symbol: str = ""
    side: str = ""
    strategy_id: Optional[str] = None
    total_qty: float = 0.0
    avg_price: float = 0.0          # volume-weighted average
    notional_usd: float = 0.0       # total_qty * avg_price
    fill_count: int = 0
    first_fill_ts_ns: int = 0
    last_fill_ts_ns: int = 0
    terminal_status: Optional[str]  # None | "FILLED" | "CANCELED" | "EXPIRED" | "REJECTED"
    terminal_ts_ns: Optional[int]
```

`avg_price` is the VWAP across all fills seen so far. On the first
fill, `avg_price = price` and `notional_usd = qty * price`.
`notional_usd` is kept explicit so dashboards can read it without
recomputing.

## 3. ShadowPositionState — output (per symbol/side)

```python
@dataclass(frozen=True)
class ShadowPositionState:
    symbol: str
    side: str                       # "BUY" | "SELL"
    net_qty: float = 0.0
    gross_qty: float = 0.0
    avg_price: float = 0.0
    notional_usd: float = 0.0
    fill_count: int = 0
    last_fill_ts_ns: int = 0
```

Aggregates every fill for the `(symbol, side)` bucket across all
`client_order_id` values. BUY and SELL on the same symbol are tracked
separately — cross-side netting is the P&L attribution layer's job
(`pnl_attribution_per_fill_p7exec_089`), not ours.

`net_qty` and `gross_qty` are equal for a single-side accumulation;
the distinction is reserved for a future asymmetric case (e.g.
quantity reversals on partial fills) without a schema change.

## 4. LiveOrderReport — input (cold path, reconciliation)

```python
@dataclass(frozen=True)
class LiveOrderReport:
    client_order_id: str
    symbol: str = ""
    side: str = ""
    total_qty: float = 0.0
    avg_price: float = 0.0
    fill_count: int = 0
    terminal_status: Optional[str] = None
    terminal_ts_ns: Optional[int] = None
    received_at_ns: int = 0
```

One row per order from the live order history (e.g. one row per
`GET /fapi/v1/allOrders` result). `received_at_ns` is overwritten
with the connector wall clock at call time by `record_live_reports`
unless already non-zero.

`terminal_status` must be one of `TERMINAL_STATUSES` if non-None;
otherwise `record_live_reports` raises `UnknownLiveReport`.

## 5. ReconciliationRow — output (cold path, per coid)

```python
@dataclass(frozen=True)
class ReconciliationRow:
    client_order_id: str
    symbol: str
    side: str
    shadow: Optional[ShadowOrderState]
    live: Optional[LiveOrderReport]
    only_in_shadow: bool
    only_in_live: bool
    qty_diff: float                # shadow.total_qty - live.total_qty
    avg_price_diff: float          # shadow.avg_price - live.avg_price
    fill_count_diff: int           # shadow.fill_count - live.fill_count
    status_match: bool
```

One row per `client_order_id` that appears in EITHER side. Diff
semantics: the missing side's contribution to the diffs is zero.
`status_match` is True iff `shadow.terminal_status ==
live.terminal_status` (None == None is True, matches
`partial_fill_accumulator_p7exec_056` convention).

The reconciler emits a row for every out-of-scope coid too
(shadow-only with no terminal status, or live-only with no terminal
status). Threshold-based suppression lives in `recon_drift_alert`, not
here.

## 6. Terminal statuses

| status      | when set                                       |
|-------------|------------------------------------------------|
| `None`      | order is still receiving fills                 |
| `FILLED`    | order reached full intended qty                 |
| `CANCELED`  | explicit cancellation (may have residual qty)  |
| `EXPIRED`   | time-in-force expired (GTD, IOC, FOK, GTX)     |
| `REJECTED`  | venue rejected the order; no fills expected     |

Once `terminal_status` is set, the next `on_fill` for that
`client_order_id` journals the late event with
`liquidity = "LATE_FILL_REJECTED"` (preserving the forensic record)
and raises `LateShadowFillRejected`. The order state is NOT mutated
by the late fill; the position state is NOT mutated.

## 7. Idempotency

The journal uses `UNIQUE(client_order_id, trade_id)` on
`shadow_fill_events`. A duplicate `on_fill` for the same
`(client_order_id, trade_id)` is silently idempotent: the existing
order state is returned and no second journal row is written. The
position projection is also NOT mutated on a duplicate.

This matches the connector's behaviour where a re-sent WS event from
the user-data stream after a reconnect produces the same `trade_id`
for an already-recorded fill.

## 8. Terminal status idempotency

`finalize_order(coid, terminal_status)` is also idempotent: calling it
twice with the same status returns the existing state. Calling it
with a DIFFERENT terminal status after one is already set is a no-op
(returns existing state) — the journal does NOT overwrite a finalised
state with a contradicting marker.

## 9. Persistence model

```
sqlite WAL @ state/shadow_book.sqlite
├── shadow_fill_events        # append-only log, one row per fill
│   UNIQUE(client_order_id, trade_id)
│   INDEX(client_order_id, ts_ns)
│   INDEX(symbol, ts_ns)
├── shadow_order_states       # current state per coid (UPSERT)
│   PRIMARY KEY(client_order_id)
│   INDEX(symbol, side)
│   INDEX(terminal_status)
├── shadow_position_states    # current net position per (symbol, side)
│   PRIMARY KEY(symbol, side)
└── live_order_reports        # venue-truth snapshot (UPSERT)
    PRIMARY KEY(client_order_id)
    INDEX(symbol)
```

`journal_mode=WAL`, `synchronous=NORMAL`,
`wal_autocheckpoint=10000`. The autocheckpoint threshold is raised
from the sqlite default (~4MB) to ~40MB so the per-fill critical
section never blocks on a WAL merge; the runner's recon tick (or a
separate low-priority thread) calls `PRAGMA wal_checkpoint(PASSIVE)`
periodically.

Median per-`on_fill` cost on a warm journal with the default 50-coid
spread is in the 130-200us band (see `evidence/bench_shadow_book.json`).
WAL flushes the journal at every commit, so a process crash loses at
most one in-flight transaction.

## 10. Failure modes

| mode                                  | behaviour                                          |
|---------------------------------------|----------------------------------------------------|
| Unknown `client_order_id` on first fill | New order + new position projection; three journal writes. |
| Known non-terminal `client_order_id`  | Fold fill into order + position; three journal writes. |
| Known terminal `client_order_id`      | Journal event with `LATE_FILL_LIQUIDITY` for order state snapshot, position unchanged; raise `LateShadowFillRejected`. |
| Duplicate `(coid, trade_id)`          | Return existing order state; no journal writes.     |
| Unknown `client_order_id` on `finalize_order` | Raise `KeyError`. State unchanged.        |
| Unknown `terminal_status` value       | Raise `ValueError`.                                |
| Caller double-`finalize_order` same status | Return existing state; no journal write.      |
| Caller double-`finalize_order` different status | Return existing state; no journal write. |
| Unknown `terminal_status` on `LiveOrderReport` | `record_live_reports` raises `UnknownLiveReport`. |
| Replay after restart                  | `replay_order(coid)` rebuilds state from events; `known_orders()` enumerates cache keys; cold-start loop calls `replay_order` for every known coid + `replay_position` for every (symbol, side). |
| Reconciliation: `qty_diff != 0`       | Emits row with non-zero qty_diff (drift alert acts). |
| Reconciliation: `only_in_live`        | Emits row with only_in_live=True (likely missed fill). |
| Reconciliation: `only_in_shadow`      | Emits row with only_in_shadow=True (likely WS lag). |

## 11. Recovery / cold-start

```python
journal = ShadowBookJournal(Path("state/shadow_book.sqlite"))
book = ShadowBook(journal)
for coid in _enumerate_coids_from_some_external_source():
    book.replay_order(coid)
# on_fill now uses the rehydrated cache.
```

If the caller has no external enumeration source, the cold-start loop
can simply call `replay_order(coid)` after `snapshot_order(coid)`
returns non-None. The journal is the source of truth; the in-memory
dicts are the hot-path cache.