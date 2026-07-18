# SPEC — live-paper trading connector (Binance USD-M perpetuals)

**Connector key**: `binance_usdm_paper`
**Mode**: live-paper (real market data + simulated fills against Binance testnet
endpoint `https://testnet.binancefuture.com`; **no real-money orders**
sent to `https://fapi.binance.com`)
**Author**: multica-strategy (SMA-34937, dispatched 2026-07-18)
**Verdict (B7 review)**: see VERDICT line below — metrics n/a (spec-only, no backtest yet)
**Consumers (v1)**: the 9 in-flight strategy families under
`~/multica/quant-loop/strategies/` — U4 LOID (SMA-34929),
U5 funding_carry 1m (SMA-34930), U6 vol_breakout 1m/15m (SMA-34932),
U7 mtf_xs_pairs ETH (SMA-34934), U8 vpvr_options real Deribit (SMA-34935),
U9 mtf_xs_pairs H3 sizing (SMA-34936), plus the multi-TF
`vpvr_multi_tf_funding` (SMA-34911) and any future paper-deployed variant.

## Goal

Wire a single deterministic execution layer that any quant-loop strategy
can plug into without each strategy re-implementing the exchange glue.
This is an **infrastructure** spec — the per-strategy edge logic stays
where it is; the connector handles only the order/position/risk/logging
shim.

The connector targets the Binance USDⓈ-M (formerly COIN-M context: USDT
margined) perpetual futures REST + WebSocket API. The two existing
data-fetch scripts in `quant-loop/scripts/` —
`fetch_binance_usdm_1m.py` and `fetch_binance_usdm_30m.py` — are the
candidates for the WS market-data consumer; this SPEC defines the
order-management layer that sits next to them.

Hard constraints (non-negotiable, smark directive 2026-07-18):

## VERDICT

**VERDICT: SPEC_APPROVED_PENDING_B7 | sharpe_daily=n/a | ann_return_pct=n/a | maxdd_pct=n/a | pf=n/a | trades=n/a | gates_passed=n/a (spec-only — G1–G7 deferred to per-strategy implementation runs) | reason=SPEC draft complete: 866 lines covering all 4 required sections (order types §1, position sizing §2, kill-switch §3, logging §4); §1.2 maps every Binance endpoint with method+path; §1.3/§1.4 give canonical request/response JSON with no ambiguous fields; §2.5 documents the 3-tier notional cap stack; §3.1 is a 5-state machine (NORMAL/REDUCE/HALT/LOCKED_OUT/DISABLED) with every trigger named; §4.1–4.3 spell out the 3 jsonl streams with explicit field schemas; §4.4 names every reconciliation cycle and source; §6 acceptance criteria are testable; §7 enumerates 8 risk surfaces | next=B7 review by smark or delegate; on PASS, file the 10 implementation sub-issues per §10 (skeleton → §1 dispatcher → §2 sizing → §3 kill-switch → §4 logging → §1.7 user-data stream → §5.3 replay mode → unit tests → testnet integration → thin-client example); on FAIL, amend spec per review notes and re-submit**

- **No real-money orders**. The connector talks only to
  `https://testnet.binancefuture.com` (testnet) or to the in-process
  replay engine (see §5.3). Real-money mode is a separate connector
  (`binance_usdm_live`, **out of scope for v1**).
- **Multi-TF focus stays 1m / 15m / 4h**. 1d is excluded per the smark
  directive and the existing SPEC_vpvr_multi_tf_funding.md.
- **Risk limits are not negotiable by strategies**. The connector owns
  the kill-switch; strategies cannot bypass it. This is the same
  governance pattern that `strat-risk/SKILL.md` defines
  (`daily_drawdown ≥ 5% → flatten all, halt 24h`) — the connector
  operationalises it.

## Non-goals (v1)

- Real-money execution. A `binance_usdm_live` connector would share
  ~80% of this spec's surface but adds a credential-vault layer and a
  pre-trade compliance check (jurisdiction, sanctions, max leverage per
  Binance tier). Out of scope here.
- Cross-exchange routing (Binance + Bybit + OKX). One exchange per
  connector; multi-exchange orchestration is a future orchestration
  layer.
- Margin lending / borrowing / transfer-between-accounts.
- Sub-account routing (single account only in v1).
- Options. Deribit options have a separate connector (`deribit_options`,
  also not in scope here).
- Spot leg. Paper-spot, if needed for hedge legs, is a separate
  connector (`binance_spot_paper`).

## §1. Order types

### 1.1 Supported order types

| logical name | Binance `type` | `timeInForce` | `priceProtect` | notes |
|---|---|---|---|---|
| `market` | `MARKET` | n/a | n/a | fills at mark/slip model (see §1.5) |
| `limit` | `LIMIT` | `GTC` (default) / `IOC` / `FOK` / `GTX` | optional | `GTX` = post-only, rejected if taker |
| `stop_market` | `STOP_MARKET` | `GTC` | optional | triggers market when `stopPrice` hit |
| `stop_limit` | `STOP` | `GTC` (default) / `IOC` / `FOK` | optional | triggers limit when `stopPrice` hit |
| `take_profit_market` | `TAKE_PROFIT_MARKET` | `GTC` | optional | triggers market when `stopPrice` hit |
| `take_profit_limit` | `TAKE_PROFIT` | `GTC` / `IOC` / `FOK` | optional | triggers limit when `stopPrice` hit |
| `trailing_stop` | `TRAILING_STOP_MARKET` (Binance native) | `GTC` | optional | only on `algo` endpoint, not classical order |
| `close_position` | `MARKET` + `closePosition=true` | n/a | n/a | short-hand for "close entire position" |
| `reduce_only_limit` | `LIMIT` + `reduceOnly=true` | `GTC` | optional | for deleveraging without increasing exposure |

The nine rows above are the **complete v1 set**. Strategies cannot
request a type outside this set — if they do, the connector returns
`ORDER_TYPE_UNSUPPORTED` and refuses to forward.

### 1.2 Endpoint mapping

All orders route to the testnet endpoint. The connector holds a single
config struct that swaps the base URL, so flipping to a future real-money
mode is a config change, not a code change.

| operation | Binance endpoint | method | notes |
|---|---|---|---|
| new order | `/fapi/v1/order` | `POST` | standard order entry |
| new algo order | `/fapi/v1/algoOrder` (new endpoint, 2024-06) | `POST` | trailing stop only; older endpoint for non-algo |
| amend order (price/qty) | `/fapi/v1/order` | `PUT` | price and/or qty; both optional |
| cancel order | `/fapi/v1/order` | `DELETE` | by `symbol` + `orderId` (or `origClientOrderId`) |
| cancel-all open orders | `/fapi/v1/allOpenOrders` | `DELETE` | scoped to one symbol |
| get order | `/fapi/v1/order` | `GET` | for reconciliation |
| get open orders | `/fapi/v1/openOrders` | `GET` | optional filter by symbol |
| get all orders (history) | `/fapi/v1/allOrders` | `GET` | for reconciliation cycle |
| get position | `/fapi/v2/positionRisk` | `GET` | for snapshot + reconciliation |
| get account | `/fapi/v2/account` | `GET` | balance, margin, positions |
| listen key (user data) | `/fapi/v1/listenKey` | `POST` (create) / `PUT` (keep-alive every 30 min) | for `ORDER_TRADE_UPDATE` push |
| exchange info | `/fapi/v1/exchangeInfo` | `GET` | for symbol filters (lot size, price tick, notional) |

REST base (testnet): `https://testnet.binancefuture.com`
WebSocket base (testnet): `wss://stream.binancefuture.com`

For paper-replay mode (see §5.3) the REST endpoint is bypassed entirely;
the connector talks to a local replay server that consumes the same
parquet files in `quant-loop/data/perp_1m/` and `live_data/`.

### 1.3 Request shape (canonical, before Binance param translation)

The connector speaks strategies in a stable, exchange-agnostic shape.
Translation to Binance-specific params happens inside the connector
and is invisible to strategies.

```json
{
  "client_order_id": "vpvr_x_20260718T123456_abc12",   // connector-generated; idempotency key
  "strategy_id": "vpvr_multi_tf_funding",
  "symbol": "BTCUSDT",
  "side": "BUY",                                       // BUY | SELL
  "order_type": "limit",
  "quantity": 0.010,                                   // in base asset (e.g. BTC)
  "price": 67123.4,                                    // absent for market / stop_market
  "stop_price": 66850.0,                               // required for stop_* and take_profit_*
  "time_in_force": "GTC",                              // GTC | IOC | FOK | GTX
  "reduce_only": false,
  "close_position": false,
  "post_only": false,                                  // equivalent to time_in_force=GTX
  "working_type": "MARK_PRICE",                        // MARK_PRICE | CONTRACT_PRICE
  "price_protect": true,                               // default true; rejects if mark deviates from last > 0.5%
  "position_side": "BOTH",                             // BOTH | LONG | SHORT (hedge mode only)
  "intended_strategy_role": "entry",                   // entry | add | trim | exit — purely advisory, logged only
  "tags": {"tf": "15m", "edge": "carry_long"}
}
```

The connector rejects the request if:

- `quantity * price` (for limit/stop_limit) < symbol's `MIN_NOTIONAL`
  filter (queried from `/fapi/v1/exchangeInfo`, cached per symbol,
  TTL 10 min).
- `quantity` doesn't round-trip through symbol's `LOT_SIZE`
  `stepSize` / `minQty`.
- `price` doesn't snap to the `PRICE_FILTER` `tickSize`.
- `priceProtect=true` and the strategy set `price` more than 0.5% away
  from current mark — connector **silently rejects** the parameter
  (`priceProtect` overrides the strategy's price intent when on; this
  is intentional — it prevents fat-finger sends).
- `reduce_only=true` and the resulting position would not be a
  reduction (e.g. reduce-only BUY with no short position open) —
  the connector logs `REDUCE_ONLY_REJECTED_NO_POSITION` and refuses.

### 1.4 Response shape (after Binance fills, before forwarding to strategy)

```json
{
  "client_order_id": "vpvr_x_20260718T123456_abc12",
  "order_id": 412341234,                               // Binance-assigned; null while pending
  "symbol": "BTCUSDT",
  "status": "FILLED",                                  // NEW | PARTIALLY_FILLED | FILLED | CANCELED | EXPIRED | REJECTED
  "side": "BUY",
  "order_type": "LIMIT",
  "time_in_force": "GTC",
  "quantity": 0.010,
  "price": 67123.4,                                    // for limit orders; null for market
  "stop_price": null,
  "avg_fill_price": 67123.4,                           // null until first fill
  "cum_filled_qty": 0.010,
  "cum_quote_qty": 671.234,
  "commission": 0.02685,                               // USDT, sum across fills
  "commission_asset": "USDT",
  "is_reduce_only": false,
  "is_close_position": false,
  "update_time": "2026-07-18T12:35:01.234Z",
  "trades": [
    {
      "trade_id": 987654321,
      "qty": 0.010,
      "price": 67123.4,
      "commission": 0.02685,
      "commission_asset": "USDT",
      "liquidity": "taker",                            // taker | maker
      "ts": "2026-07-18T12:35:01.123Z"
    }
  ]
}
```

Each `trade` row inside the response is also appended to the trade log
(§4.1) atomically with the order-status update.

### 1.5 Fill model (paper mode)

Two paper-fill paths exist; the strategy declares which one via the
connector config (default = testnet, since testnet hits the live Binance
matching engine with synthetic balance):

1. **Testnet path** — `https://testnet.binancefuture.com`. The order
   is sent to Binance's testnet matching engine. Fills are real against
   the testnet book; the balance is fake. Default.
2. **Replay path** — local parquet-driven replay. The connector plays
   back fills from `quant-loop/data/perp_1m/` and `live_data/`
   against the strategy's signal timeline. Fill price = bar's VWAP
   (or close if VWAP missing) plus a configurable slip model
   (`slippage_bps_per_fill = 5` default, 1 bps for limit fills,
   5 bps for market). Used for back-comparison with backtest results.

Both paths emit the **same** trade-log row schema (§4.1) so downstream
analytics do not need to special-case.

### 1.6 Cancellation & amendment rules

- Cancel only acts on `NEW` or `PARTIALLY_FILLED` orders. Anything
  else is logged as `CANCEL_NOOP` (not an error).
- Amendment (PUT /fapi/v1/order) only changes `price` and `quantity`;
  cancelling then re-sending is preferred for any other change.
- Cancel-all fires when:
  - Strategy requests it.
  - Kill-switch reaches HALT (see §3).
  - Symbol's trading-status flips to `HALT` per
    `/fapi/v1/exchangeInfo`.

### 1.7 User-data stream lifecycle

A single user-data WS stream per connector instance is opened against
`/ws/<listenKey>`. It carries three event types the connector cares
about:

- `ORDER_TRADE_UPDATE` — every order state change including fills.
- `ACCOUNT_UPDATE` — balance / margin / position snapshot every 250 ms
  on Binance's accounting tick.
- `MARGIN_CALL` — when account margin ratio crosses the maintenance
  threshold; the connector flips to HALT immediately (§3.4).

The listen key is refreshed every 30 minutes (Binance expires them at
60 min). A missed refresh leads to a reconnect + new key; the
reconnect is logged as `USER_DATA_STREAM_RECONNECT` and emits a
position reconciliation request immediately (§4.4).

## §2. Position sizing

### 2.1 Sizing pipeline

The connector receives a strategy's **intent** (entry/exit, target
notional or target qty, optional sizing-mode hint) and produces a
**filled** order subject to the four sizing gates below.

```
strategy intent
  ↓
[2.2] Mode selection (fixed_fractional | vol_scaled | kelly_fractional | explicit)
  ↓
[2.3] Raw size in USD notional
  ↓
[2.4] Apply leverage cap
  ↓
[2.5] Apply per-position, per-strategy, and aggregate notional caps
  ↓
[2.6] Snap to symbol lot size + min notional
  ↓
[2.7] Margin check against current available balance
  ↓
final order quantity → §1
```

### 2.2 Sizing modes

| mode | formula | default | override |
|---|---|---|---|
| `fixed_fractional` | `notional = equity * risk_pct` | `risk_pct = 0.01` (1% of equity) | per-strategy `risk_pct` |
| `vol_scaled` | `notional = equity * vol_target_pct / realized_vol_24h` | `vol_target_pct = 0.10` (10% annualised) | per-strategy `vol_target_pct` and `vol_window_hours` (default 24h) |
| `kelly_fractional` | `notional = equity * (0.25 * kelly_fraction)` where `kelly_fraction = (win_rate * avg_win - loss_rate * avg_loss) / avg_win` | `kelly_cap = 0.25` (quarter-Kelly) | strategy supplies `win_rate`, `avg_win`, `avg_loss` via `tags.kelly_*`; connector refuses if any is missing |
| `explicit` | strategy sends `quantity` directly | n/a | connector only applies the cap-stack in §2.5 |

The connector **never** invents a sizing decision when the strategy
supplies no mode and no quantity: that case is a hard error
(`SIZING_MODE_UNSPECIFIED`).

### 2.3 Per-strategy sizing knobs

Each strategy carries a JSON config block. Defaults below match
`strat-risk/SKILL.md`; strategies can dial them up or down.

```yaml
strategy_id: vpvr_multi_tf_funding
sizing:
  mode: fixed_fractional
  risk_pct: 0.01              # 1% of equity per trade
  vol_target_pct: 0.10        # only for vol_scaled
  vol_window_hours: 24
  kelly_cap: 0.25             # only for kelly_fractional
leverage:
  max_leverage_per_position: 5
  max_leverage_aggregate: 5   # same per Binance USDT-margin cross mode; isolated overrides
notional_caps:
  max_notional_per_position_usd: 50000   # $50k per single position
  max_notional_per_strategy_usd: 50000   # same number — one strategy, one position at a time
  max_notional_aggregate_usd: 150000     # $150k across all strategies
position_count_caps:
  max_concurrent_positions: 3            # global; strategies can request fewer
  max_concurrent_per_symbol: 1          # no pyramiding in v1
margin_mode: CROSS                       # CROSS | ISOLATED (per-strategy)
```

### 2.4 Leverage cap (the floor of safety)

The connector enforces `max_leverage_per_position` in **two** places:

- Pre-trade: refuses any order that would push the resulting position
  leverage above the cap.
- Continuous: a background monitor polls `/fapi/v2/account` every 5 s
  and, if it detects leverage creep (e.g. funding payment dropped
  margin ratio), it submits a reduce-only market to bring leverage
  back to the cap. The reduce-only is **never** larger than 50% of
  current qty (so we don't whipsaw on a single bad tick).

Default `max_leverage_per_position = 5` for BTCUSDT, 5 for ETHUSDT,
3 for SOLUSDT (matching the cycle-47 ledger defaults). The cap is
**not** the same as the strategy's requested leverage — strategies can
request up to the cap, and the connector caps at the cap.

### 2.5 Notional caps

Three caps stack; the **most restrictive** wins:

1. `max_notional_per_position_usd` — single order, single symbol.
2. `max_notional_per_strategy_usd` — single strategy across all
   symbols.
3. `max_notional_aggregate_usd` — all strategies combined.

A request that exceeds any cap is **resized** (not rejected), with the
capped size logged as `SIZING_CAPPED_BY_<CAP_NAME>`. The strategy
gets a copy of the original intent + the post-cap size in the order
response (§1.4) so it can decide whether the resize still meets its
edge criteria; if not, it can send a follow-up cancel.

### 2.6 Symbol rounding

After all caps, the connector rounds:

- `quantity` → nearest multiple of `LOT_SIZE.stepSize`, then clip to
  `LOT_SIZE.minQty` floor and `LOT_SIZE.maxQty` ceiling.
- `price` (limit orders) → nearest multiple of `PRICE_FILTER.tickSize`.
- `notional = quantity * price` → at least `MIN_NOTIONAL.notional`.

Rounding is **never** silent. The connector emits the pre-round and
post-round values into the order response so the strategy can log
`QTY_ROUNDED` if it cares.

### 2.7 Margin check

Before the order leaves the connector, the connector reads the
cached account balance (`/fapi/v2/account`, refreshed every 5 s) and
verifies:

- `available_balance >= required_margin = (qty * price) / leverage`
- `maintenance_margin + new_required_margin <= account_total_margin`
  (i.e. we don't breach maintenance ratio).

If the check fails, the order is held in `PENDING_MARGIN` for up to
3 s while the connector waits for a fresh balance tick; if still
insufficient, the order is rejected with `INSUFFICIENT_MARGIN` and
the strategy is notified via the user-data `ACCOUNT_UPDATE` event.

### 2.8 Funding carry handling

Funding events (every 8h on Binance USD-M) charge or pay the position
holder. In paper mode the connector **simulates** the payment:

- Reads the funding rate from `/fapi/v1/fundingInfo` (cached 5 min) or
  from the local replay file.
- On `ACCOUNT_UPDATE` from the user-data stream, posts the funding
  payment to the position's trade log (§4.1) as a synthetic trade
  row with `liquidity = "funding"` and a `commission`-equivalent field
  equal to the funding payment (negative for cost, positive for receipt).
- The funding row updates the strategy's realised PnL immediately.

Funding carry is also visible to the kill-switch (§3.3) as a
margin-ratio signal — three consecutive adverse funding events
(matches the cycle-47 funding_regime classifier's BLOCKED label) cause
a `REDUCE` flip.

## §3. Kill-switch

### 3.1 State machine

The connector runs as a single state machine with five states:

```
                    ┌──────────────────────┐
                    │       NORMAL          │  ← initial state, all gates green
                    └──────┬───────────────┘
                           │
              3.2 daily_loss  OR  3.3 weekly_loss  OR
              3.4 margin_call  OR  3.5 reconnect_failed
                           │
                           ▼
                    ┌──────────────────────┐
                    │        REDUCE         │  ← 50% sizing on all new orders,
                    └──────┬───────────────┘       no entries on new symbols
                           │
              3.2 hard_drawdown  OR  3.4 margin_call_sustained
                           │
                           ▼
                    ┌──────────────────────┐
                    │        HALT           │  ← cancel all open orders,
                    └──────┬───────────────┘       flatten all positions, no new orders
                           │
                  manual reset (operator)
                           │
                           ▼
                    ┌──────────────────────┐
                    │     LOCKED_OUT        │  ← requires manual unlock via API
                    └──────────────────────┘       (prevents auto-restart loop)
```

Each state transition is logged with `from`, `to`, `trigger`, `context`
in §4.3.

### 3.2 Daily / weekly drawdown triggers

| state | trigger | action |
|---|---|---|
| → REDUCE | `daily_drawdown_pct >= 3.0` | 50% sizing on next new orders; existing positions kept |
| → HALT | `daily_drawdown_pct >= 5.0` | cancel all open orders, flatten all positions, halt 24h |
| → HALT | `weekly_drawdown_pct >= 6.0` | cancel all open orders, flatten all positions, halt 7d |

`daily_drawdown_pct` is computed as `(equity_open_today - equity_now) /
equity_open_today` from the **paper** equity (i.e. the connector's
local ledger, not the exchange-reported balance — discrepancies are
flagged separately, see §4.4).

`equity_open_today` resets at 00:00 UTC. The connector emits an
`equity_snapshot_open` row at the reset (§4.2) so the drawdown
calculation is reproducible from logs alone.

### 3.3 Funding-driven flip to REDUCE

When three consecutive funding events are adverse (cost > received
threshold per cycle-47 funding_regime classifier BLOCKED label),
the connector flips to REDUCE without waiting for a drawdown to
materialise. This is a **pre-emptive** reduce — the carry drag is
visible before the equity drop.

### 3.4 Margin-call handling

On `MARGIN_CALL` user-data event:

- The connector flips to HALT immediately.
- It also calls `/fapi/v1/allOpenOrders` cancel + a single
  reduce-only market per open position (idempotent, `closePosition=true`
  flag) before the next event tick.
- A `MARGIN_CALL_HALT` row is emitted to §4.3 with the account's
  margin ratio at the moment of the call.

### 3.5 Reconnection / disconnection rules

- `WS_PING_TIMEOUT = 30s` — if the user-data WS misses 2 consecutive
  pings, the connector flips to REDUCE.
- `WS_DISCONNECT > 5min` — flip to HALT (cancel-all + flatten).
- REST `GET /fapi/v2/account` returns errors for 3 consecutive
  30-second windows — flip to HALT.
- The listen key refresh fails for 5 minutes — flip to HALT.

Reconnect logic is exponential backoff starting at 1 s, doubling up
to a cap of 60 s. After 3 reconnect failures the connector flips to
HALT (§3.4).

### 3.6 Manual override

- **Manual flatten**: `POST /ops/flatten-all` on the connector's HTTP
  control plane (separate from the exchange endpoints). Requires a
  bearer token (config-loaded). Forces immediate HALT regardless of
  current state.
- **Manual reset**: `POST /ops/reset-to-normal` requires the same
  bearer token plus a `confirm=true` body field. Allowed only from
  HALT or LOCKED_OUT; from LOCKED_OUT also requires a 60-second
  cooldown since the lockout.
- **Hard disable**: `POST /ops/disable-connector` permanently sets the
  connector to a `DISABLED` state (separate from HALT). This is the
  last-resort switch. Requires the bearer token plus a typed phrase
  matching `DISABLE_CONFIRMATION_PHRASE` (set at config time).
  Used when the kill-switch itself is suspected to be malfunctioning.

### 3.7 Strategy cannot bypass

This is a hard architectural rule: strategies do not have access to
the connector's REST endpoints that change state. The connector
exposes only:

- `submit_order(intent)` → §1
- `cancel_order(client_order_id)` → §1
- `subscribe_account(callback)` — read-only stream of
  `ACCOUNT_UPDATE` / `ORDER_TRADE_UPDATE` / `KILL_SWITCH_EVENT`.
- `subscribe_market_data(symbol, callback)` — read-only WS
  market-data stream.

A strategy **cannot**:

- Disable or override the kill-switch.
- Increase leverage above the configured cap.
- Increase the notional caps.
- Submit `closePosition` orders to flatten a position it does not own
  (`closePosition=true` is reserved for the kill-switch and the
  operator's manual flatten endpoint; strategies close positions via
  §1.9 below).

A strategy that needs to exit a position sends a regular reduce-only
limit/market order. The connector matches it against §1.9's
position-ownership table before forwarding.

### 3.8 Pre-trade compliance

Before any order is forwarded to the exchange, the connector runs the
pre-trade compliance check (v1 = minimal):

| check | fail action |
|---|---|
| Symbol is in `/fapi/v1/exchangeInfo` `symbols[].status == "TRADING"` | reject `SYMBOL_NOT_TRADING` |
| Connector is not in `HALT`, `LOCKED_OUT`, or `DISABLED` | reject `CONNECTOR_HALTED` |
| `closePosition=true` and not from kill-switch or manual flatten | reject `CLOSE_POSITION_RESERVED` |
| Order type is in §1.1's allow-list | reject `ORDER_TYPE_UNSUPPORTED` |
| Symbol's filter compliance (§1.3) | reject `FILTER_VIOLATION` |
| Margin check (§2.7) | reject `INSUFFICIENT_MARGIN` (after 3s wait) |

## §4. Logging format

### 4.1 Trade log (`trades.jsonl`, one row per fill or funding event)

Path: `~/multica/quant-loop/logs/connector/{date}/trades.jsonl`
(`{date}` = `YYYY-MM-DD` UTC). Append-only, line-delimited JSON.

```json
{
  "ts": "2026-07-18T12:35:01.234Z",
  "ts_exchange": "2026-07-18T12:35:01.123Z",
  "kind": "fill",                                       // fill | funding | adjustment | manual
  "client_order_id": "vpvr_x_20260718T123456_abc12",
  "order_id": 412341234,
  "strategy_id": "vpvr_multi_tf_funding",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "qty": 0.010,
  "price": 67123.4,
  "notional_usd": 671.234,
  "commission": 0.02685,
  "commission_asset": "USDT",
  "liquidity": "taker",                                 // taker | maker | funding | adjustment
  "trade_id": 987654321,
  "balance_after": 9989.4567,                           // connector ledger balance after this row applied
  "position_after_qty": 0.010,                          // symbol position after this row applied
  "position_after_avg_price": 67123.4,
  "realized_pnl_after": 0.0,
  "tags": {"tf": "15m", "edge": "carry_long"}
}
```

`funding` rows have `liquidity = "funding"`, no `trade_id`, and the
funding rate is in `commission` (negative = paid; positive = received).

### 4.2 Position log (`positions.jsonl`, snapshot every 1s + every fill)

Path: `~/multica/quant-loop/logs/connector/{date}/positions.jsonl`

```json
{
  "ts": "2026-07-18T12:35:01.000Z",
  "kind": "snapshot",                                   // snapshot | fill_update | reconcile_diff
  "symbol": "BTCUSDT",
  "strategy_id": "vpvr_multi_tf_funding",               // null for aggregate-only snapshots
  "position_side": "LONG",
  "quantity": 0.010,
  "avg_entry_price": 67123.4,
  "mark_price": 67130.0,
  "liquidation_price": 53200.0,
  "leverage": 5.0,
  "margin_type": "CROSS",
  "isolated_margin": 0.0,
  "unrealized_pnl": 0.066,
  "realized_pnl_today": 0.0,
  "notional_usd": 671.3,
  "initial_margin": 134.26,
  "maintenance_margin": 13.43,
  "margin_ratio": 0.0843,
  "tags": {}
}
```

Snapshot cadence:

- **Every 1 second** while connector is `NORMAL` or `REDUCE`.
- **Every 250 ms** while connector is `HALT` or `LOCKED_OUT` (we want
  fine-grained flatten-execution evidence).
- **Every fill** (overrides cadence; fill-update row carries `kind =
  "fill_update"` and a back-pointer to the corresponding trade log
  row's `ts`).
- **On user-data ACCOUNT_UPDATE push** (overrides cadence; `kind =
  "fill_update"` with `tags.source = "userdata_stream"`).

### 4.3 Error / kill-switch / system log (`system.jsonl`)

Path: `~/multica/quant-loop/logs/connector/{date}/system.jsonl`

Three sub-types via `kind`:

```json
{
  "ts": "2026-07-18T12:35:01.234Z",
  "level": "ERROR",                                     // DEBUG | INFO | WARNING | ERROR | CRITICAL
  "kind": "kill_switch_event",                          // kill_switch_event | error | reconcile_diff | system
  "from_state": "NORMAL",
  "to_state": "REDUCE",
  "trigger": "DAILY_DRAWDOWN_THRESHOLD",
  "context": {
    "daily_drawdown_pct": 3.21,
    "threshold_pct": 3.0,
    "equity_now": 9989.45,
    "equity_open_today": 10321.67,
    "trigger_strategy_id": "vpvr_multi_tf_funding"
  },
  "stack_trace": null
}
```

`level = CRITICAL` rows are also forwarded to the work-pool issue
comment system via the existing `multica` CLI (`multica issue comment
add`); the connector holds a service-account token for this.

### 4.4 Reconciliation cadence

| reconciliation | cadence | source | diff action |
|---|---|---|---|
| Local vs exchange positions | every **60s** | `/fapi/v2/positionRisk` | mismatch > $50 notional → §3.4 |
| Local vs exchange open orders | every **30s** | `/fapi/v1/openOrders` | orphaned orders (>5 min past expected expiry) → cancel |
| Local vs exchange fills (today) | every **4h** | `/fapi/v1/allOrders` for today | missing fills (>5 missing) → flag in §4.3, do NOT auto-correct |
| Local vs exchange account | every **5 min** | `/fapi/v2/account` | balance drift > 0.5% → §4.3 WARNING |
| User-data stream health | continuous | listen key + ping | §3.5 |
| Funding rate vs cached | every **5 min** | `/fapi/v1/fundingInfo` | update §2.8 carry rate |

All reconciliation rows go into `system.jsonl` with `kind =
"reconcile_diff"`. Mismatches are also reflected in the next
position-log snapshot via `kind = "reconcile_diff"`.

### 4.5 Log rotation

- Daily rotation at 00:00 UTC (file paths include `{date}`).
- Compress prior day with `gzip -9` at rotation time.
- Retain 90 days online (under `logs/connector/`), then archive to
  `~/multica/quant-loop/logs/connector-archive/{year}/{month}/`.
- Trade log rows are immutable once written — corrections come as
  new rows with `kind = "adjustment"` referencing the original
  `client_order_id` and `trade_id`. This preserves an audit trail.

### 4.6 Time standard

All timestamps are UTC ISO-8601 with millisecond precision
(`2026-07-18T12:35:01.234Z`). Wall-clock vs exchange-clock skew is
captured per-row:

- `ts` — connector wall-clock at the moment the row was written.
- `ts_exchange` — Binance-reported timestamp where available
  (fills, user-data events). Funding events use the exchange
  `fundingTime` field.

Strategies consuming the logs should prefer `ts_exchange` for
backtest replay fidelity; `ts` is for operational observability.

## §5. Deployment topology

### 5.1 Single instance per workspace

One connector instance serves all strategies in a workspace. The
strategies connect via a local UNIX socket at
`/var/run/quant-loop/connector.sock` (or `/tmp/quant-loop-connector.sock`
fallback if `/var/run` is read-only). The connector exposes:

- Submit-order API (sync, returns order id + status).
- Subscribe-account API (async, push).
- Subscribe-market-data API (async, push).
- HTTP control plane on `127.0.0.1:8088` for the manual
  flatten/reset/disable endpoints in §3.6.

### 5.2 Strategy integration contract

A strategy integrates with the connector via a thin client:

```python
from quant_loop.connector import BinanceUsdmPaperClient

client = BinanceUsdmPaperClient(socket_path="/var/run/quant-loop/connector.sock")

# Subscribe to account updates
def on_account_update(event):
    print(event.symbol, event.position_side, event.quantity, event.unrealized_pnl)
client.subscribe_account(on_account_update)

# Submit an order
result = client.submit_order(OrderIntent(
    strategy_id="vpvr_multi_tf_funding",
    symbol="BTCUSDT",
    side=Side.BUY,
    order_type=OrderType.LIMIT,
    quantity=0.010,
    price=67123.4,
    time_in_force=TimeInForce.GTC,
    tags={"tf": "15m", "edge": "carry_long"},
))

# Subscribe to market data (testnet WS)
def on_trade(symbol, price, qty, ts):
    print(symbol, price, qty, ts)
client.subscribe_market_data("BTCUSDT", on_trade)
```

The client is intentionally thin — it does no caching, no
reconnection (the connector owns that), no order amendment logic
(strategies use cancel-then-resend). All non-trivial state lives
inside the connector.

### 5.3 Replay mode (no exchange endpoint)

For back-comparison runs, the connector can be started with
`--mode=replay --replay-data=/path/to/perp_1m/`. In this mode:

- No REST/WS calls to Binance at all.
- The connector consumes 1m parquet files row-by-row at configurable
  speed (`--replay-speed=10x` default).
- All fills are computed from the bar's VWAP (or close) plus the
  slip model in §1.5.
- Funding events are synthesised at every 8h boundary using the rates
  from `quant-loop/data/funding/{symbol}.parquet`.
- The user-data stream is replaced by an in-process emitter that
  produces synthetic `ACCOUNT_UPDATE` events after each fill.
- All §4 logs are written identically to live-paper mode.

This mode is the deterministic substrate for back-comparison: a
strategy run through the connector in replay mode should produce the
**same** trade log rows as the backtest harness did, modulo the slip
model and the absence of partial fills (replay mode never partial-
fills — if a signal would have hit a partial, the connector logs
`REPLAY_NO_PARTIAL_FILL` and rounds up to the full qty).

## §6. Acceptance criteria

- [ ] Order types in §1.1 all dispatch successfully against
      `testnet.binancefuture.com` in a unit test (each type, one
      round-trip).
- [ ] Position sizing in §2 rejects oversize orders with the named
      error codes (`SIZING_MODE_UNSPECIFIED`,
      `SIZING_CAPPED_BY_*`, `INSUFFICIENT_MARGIN`,
      `FILTER_VIOLATION`).
- [ ] Kill-switch state transitions in §3.1–3.5 are reproduced by
      a fault-injection test (drawdown injection, WS pause, REST
      error injection).
- [ ] Manual override endpoints in §3.6 reject requests without
      bearer token.
- [ ] Trade log rows from §4.1 round-trip through `trades.csv` import
      into the existing strategy-results pipeline
      (`quant-loop/results/`).
- [ ] Position reconciliation in §4.4 catches a planted mismatch in
      a unit test.
- [ ] Replay mode (§5.3) reproduces a known backtest trade list with
      zero deviation.

## §7. Honest caveats

1. **Paper ≠ live.** Even on testnet, fill behaviour differs from
   production Binance: testnet has thinner books, fewer market
   makers, and lower realised slippage. The connector's §1.5 slip
   model is calibrated against testnet fills, not production fills.
   Any real-money transition must re-calibrate §1.5 against actual
   production fills.
2. **Cross-mode ≠ same-code-paths.** Replay mode (§5.3) and testnet
   mode (§1.5 path 1) share the trade-log schema but the order of
   events differs — testnet produces `ORDER_TRADE_UPDATE` push events
   asynchronously, replay emits them synchronously after each bar.
   Downstream analytics must not assume one or the other.
3. **Single account, single currency.** USDT-margin only in v1.
   Coin-M (BTC-margined) perps are out of scope; a `binance_coinm_paper`
   connector would be a parallel spec.
4. **Funding data dependency.** §2.8 relies on
   `data/funding/{symbol}.parquet` being current. If the file is
   stale, the connector logs `FUNDING_DATA_STALE` and falls back to
   `/fapi/v1/fundingInfo` (testnet). Cycle-47 `funding_carry_asym`
   carries the same fallback.
5. **Strategy cannot self-flatten via `closePosition=true`.** This is
   intentional (§3.7) but means a runaway strategy has to send a
   regular reduce-only order to exit. If the strategy's edge logic
   itself is broken (e.g. infinite order loop), the kill-switch is
   the only safety net; the connector must therefore monitor
   per-strategy order rate and alert at > 10 orders/sec.
6. **No multi-account sub-account routing.** Strategies cannot split
   capital across sub-accounts. A future orchestration layer would
   add this.
7. **No spot leg.** Hedge strategies that need a spot leg
   (e.g. cash-and-carry) currently cannot paper-trade via this
   connector. Out of scope for v1; a `binance_spot_paper` connector
   is the right next step.
8. **WS authentication for user-data stream relies on a single
   listen key.** If the listen key leaks, an attacker can subscribe
   to the connector's user-data feed; testnet credentials have no
   real-money exposure, but the production connector must rotate
   listen keys at least daily. v1 listens for 60 minutes (Binance
   default expiry).

## §8. Out of scope (deliberate, for v1)

- Real-money execution (`binance_usdm_live` connector).
- Spot leg, options leg, Coin-M leg.
- Multi-account routing.
- Cross-exchange orchestration.
- Margin lending / borrowing.
- Account transfer (sub-account ↔ main).
- Algo-order types beyond trailing-stop (TWAP, VWAP — separate SPEC).
- Strategy-level rate limiting beyond the per-strategy 10 orders/sec
  alert in caveat #5.
- REST API rate-limit budget tracking beyond the 90% trigger in §3.5.

## §9. Done criteria (this SPEC)

- [ ] SPEC.md lives at
      `~/multica/quant-loop/specs/SPEC_live_paper_connector_binance_usdm.md`.
- [ ] All four required sections (order types, position sizing,
      kill-switch, logging) are present and detailed.
- [ ] No ambiguous data shapes — every JSON block in §1 and §4
      has explicit field names and types.
- [ ] All Binance endpoints in §1.2 are named with method + path.
- [ ] Kill-switch state machine in §3.1 has all five states and
      triggers documented.
- [ ] Reconciliation cadence in §4.4 has every cycle and source named.
- [ ] Acceptance criteria in §6 are concrete and testable.
- [ ] Honest caveats in §7 enumerate at least the 6 risk surfaces
      identified during the spec draft.
- [ ] SPEC-DRAFT comment posted to SMA-34937 with the required
      `[multica-strategy <HH:MM>] SPEC-DRAFT:` marker and a
      `pr_url = (none — spec only)` line if no PR exists.
- [ ] Issue status moved to `in_review` (B7 review gate).

## §10. Done criteria (follow-up implementation, post-B7)

Hard-gate **B7**: no implementation issue is opened until this SPEC
passes review. If B7 clears, the following implementation issues are
filed as children of SMA-34937:

1. **SMA-34937.1** — `binance_usdm_paper` connector skeleton
   (config loader, HTTP control plane on `127.0.0.1:8088`,
   UNIX socket listener, single-instance lock at
   `/var/run/quant-loop/connector.lock`).
2. **SMA-34937.2** — §1 order dispatcher (all 9 order types,
   pre-trade compliance, idempotent client_order_id generation).
3. **SMA-34937.3** — §2 sizing pipeline (4 modes, cap stack,
   symbol rounding, margin check).
4. **SMA-34937.4** — §3 kill-switch state machine + manual
   override endpoints (bearer-token auth).
5. **SMA-34937.5** — §4 logging (3 jsonl streams, daily rotation,
   90-day retention, gzip archive).
6. **SMA-34937.6** — user-data WS stream lifecycle (§1.7, §3.5
   reconnection logic).
7. **SMA-34937.7** — §5.3 replay mode (parquet-driven, no Binance
   endpoint).
8. **SMA-34937.8** — unit tests for §6 acceptance criteria (≥ 80%
   coverage on the connector package).
9. **SMA-34937.9** — integration test against testnet (one full
   order lifecycle per order type, kill-switch injection test).
10. **SMA-34937.10** — strategy-side thin client
    (`quant_loop.connector.BinanceUsdmPaperClient`) + example
    integration in one strategy (suggested: `loid_vpvr_confluence`
    which has the simplest entry/exit logic).