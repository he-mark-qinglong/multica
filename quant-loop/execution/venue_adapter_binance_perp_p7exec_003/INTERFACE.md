# venue_adapter_binance_perp — Interface contract (P7-EXEC-003)

This file is the wire contract between the adapter and any
caller (the runner, downstream consumers, tests).  It defines the
durable additive schemas, the public Python API, and the
registration protocol.

## 1. Schema (additive to `OrderJournal`)

The component ships its own DDL (`SCHEMA_SQL`) and an idempotent
`bootstrap_journal(journal)` helper.  No edit to
`execution/runner.py` is required — the runner auto-discovers
the additive tables on first connect (via the canonical
`OrderJournal` constructor), and the adapter calls
`bootstrap_journal` itself on construction.

### 1.1 `binance_perp_intents` — per-intent projection (UPSERT)

One row per perp-tagged intent.  PK = `client_order_id`.

| Column                     | Type    | Notes |
|----------------------------|---------|-------|
| `client_order_id`          | TEXT    | PK; the perp intent's coid |
| `ts_first_seen_ns`         | INTEGER | first journal ts |
| `ts_last_seen_ns`          | INTEGER | most recent transition ts |
| `symbol`                   | TEXT    | the perp ticker (USDT/USDC suffix) |
| `side`                     | TEXT    | `'BUY'` or `'SELL'` |
| `qty`                      | REAL    | signed target qty (+ BUY / - SELL) |
| `price`                    | REAL    | limit price (NULL for MARKET) |
| `order_type`               | TEXT    | `'LIMIT'` (default) / `'MARKET'` / algo w/ `policy.allow_algos=True` |
| `time_in_force`            | TEXT    | perp-eligible TIF |
| `venue`                    | TEXT    | canonical venue id `binance_usdt_futures` |
| `reduce_only`              | INTEGER | 0 / 1 |
| `new_client_strategy_id`   | TEXT    | nullable |
| `binance_perp_signature`   | TEXT    | SHA-256 of intent fingerprint |
| `venue_order_id`           | TEXT    | Binance `orderId`, set on first ack |
| `status`                   | TEXT    | `BinancePerpStatus` |
| `updated_ts_ns`            | INTEGER | most recent transition ts |
| `policy_fingerprint`       | TEXT    | SHA-256 of policy |
| `payload`                  | TEXT    | JSON per-intent extras |

Indexes: `ts_first_seen_ns`, `status`, `symbol`, `venue_order_id`.

### 1.2 `binance_perp_events` — append-only audit log

One row per perp event.  PK = auto-increment `id`.

| Column                | Type    | Notes |
|-----------------------|---------|-------|
| `id`                  | INTEGER | PK auto-increment |
| `ts_ns`               | INTEGER | event ts |
| `client_order_id`     | TEXT    | ref to intent |
| `source`              | TEXT    | `'rest_submit'` / `'rest_ack'` / `'wss_userdata'` / `'rest_cancel'` / `'rest_query'` |
| `kind`                | TEXT    | transition (see taxonomy) |
| `venue_order_id`      | TEXT    | ref to Binance orderId |
| `raw_payload`         | TEXT    | JSON of the source payload (truncated in tests) |
| `policy_fingerprint`  | TEXT    | SHA-256 of policy |

Transition taxonomy: `INTENT_TAGGED`, `SUBMITTED`, `ACK_OK`,
`ACK_PARTIAL`, `ACK_REJECT`, `WS_UPDATE`, `CANCEL_REQUESTED`,
`CANCELED`, `EXPIRED`, `BLOCKED`, `VALIDATION_FAILED`,
`WSS_CONNECTED`, `WSS_DISCONNECTED`, `WSS_RECONNECTING`,
`WSS_HALTED`, `WSS_FRAME_IGNORED`.

Indexes: `client_order_id`, `ts_ns`, `source`, `kind`.

### 1.3 `binance_perp_acks` — per-coid terminal outcome (UPSERT)

One row per terminal ack (REST or WS).  PK = `client_order_id`.

| Column                | Type    | Notes |
|-----------------------|---------|-------|
| `client_order_id`     | TEXT    | PK |
| `ts_ns`               | INTEGER | terminal ts |
| `symbol`              | TEXT    | nullable |
| `side`                | TEXT    | nullable |
| `intended_qty`        | REAL    | signed |
| `price`               | REAL    | nullable |
| `venue`               | TEXT    | nullable |
| `venue_order_id`      | TEXT    | nullable |
| `status`              | TEXT    | `BinancePerpStatus` |
| `filled_qty`          | REAL    | signed (0.0 on REJECTED / NO_FILL_CANCELLED) |
| `avg_price`           | REAL    | venue-weighted-average fill price |
| `commission`          | REAL    | signed (negative on fee) |
| `reject_reason`       | TEXT    | canonical reason label |
| `error_code`          | TEXT    | venue's numeric code (as string) |
| `source`              | TEXT    | `'rest'` / `'wss'` (BinancePerpAckSource) |
| `fill_qty_source`     | TEXT    | ack key the classifier inferred `filled_qty` from (`executedQty` / `cumQty` / `filledQty` / `origQty` / `absent` / `wss`) |
| `policy_fingerprint`  | TEXT    | SHA-256 |
| `payload`             | TEXT    | JSON per-outcome extras |

Indexes: `status`, `ts_ns`, `venue_order_id`, `source`.

## 2. Public Python API

```python
from execution.venue_adapter_binance_perp_p7exec_003 import (
    BinancePerpAdapter,
    BinancePerpAdapterPolicy,
    BinancePerpAckSource,
    BinancePerpIntent,
    BinancePerpPaperTransport,
    BinancePerpPaperTransportFillModel,
    BinancePerpSnapshot,
    BinancePerpState,
    BinancePerpStatus,
    BinancePerpWssConsumer,
    BinancePerpWssSnapshot,
    BinancePerpWssState,
    DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
    DEFAULT_VENUE,
    OutboundBinancePerpTransport,
    SCHEMA_SQL,
    T_ACK_OK,
    T_ACK_PARTIAL,
    T_ACK_REJECT,
    T_BLOCKED,
    T_CANCELED,
    T_CANCEL_REQUESTED,
    T_EXPIRED,
    T_INTENT_TAGGED,
    T_SUBMITTED,
    T_VALIDATION_FAILED,
    T_WS_UPDATE,
    T_WSS_CONNECTED,
    T_WSS_DISCONNECTED,
    T_WSS_FRAME_IGNORED,
    T_WSS_HALTED,
    T_WSS_RECONNECTING,
    bootstrap_journal,
    classify_binance_perp_rest_ack,
    parse_wss_userdata_message,
    policy_fingerprint,
    register_with_runner,
    sign_binance_perp_request,
    validate_perp_intent,
)
```

### 2.1 `validate_perp_intent` — pure helper

```python
def validate_perp_intent(
    request: Mapping[str, Any],
    policy: BinancePerpAdapterPolicy = DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
) -> Tuple[bool, str]:
    """Return (is_valid, reason).  Pure; no side effects.

    Validates:
      - venue matches policy.venue OR binance_perp opt-in flag is set;
      - client_order_id / symbol non-empty;
      - symbol is perp-eligible (ASCII upper-case, ends with USDT or USDC);
      - side is 'BUY' / 'SELL';
      - qty coercible + finite + |qty| >= 1e-9;
      - order_type in {_PERP_ORDER_TYPES};
      - LIMIT and MARKET are always accepted; algos need policy.allow_algos=True;
      - price coercible + finite + positive (LIMIT only);
      - time_in_force in _PERP_TIME_IN_FORCE.
    """
```

### 2.2 `sign_binance_perp_request` — pure signer

```python
def sign_binance_perp_request(
    params: Mapping[str, Any],
    *,
    api_secret: str,
    timestamp_ns: Optional[int] = None,
    recv_window_ms: int = 5000,
) -> Dict[str, Any]:
    """Append HMAC-SHA256 signature to a perp REST payload.

    The signer sorts the params alphabetically (Binance's wire spec
    does not dictate insertion order; sorting yields byte-stable
    output across Python processes).  The canonical string is the
    ``urlencode``-d ``"<k>=<v>&..."`` form (with ``quote_via=quote``
    and ``safe=""``); the ``timestamp`` and ``recvWindow`` keys are
    injected if missing.

    Returns a new dict containing the original params + signature.
    Raises ``ValueError`` if ``api_secret`` is empty or any value is
    non-finite.
    """
```

### 2.3 `classify_binance_perp_rest_ack` — pure classifier

```python
def classify_binance_perp_rest_ack(
    ack: Mapping[str, Any],
) -> Tuple[
    BinancePerpStatus,  # status
    float,              # filled_qty (signed)
    Optional[float],    # avg_price
    Optional[float],    # commission
    Optional[str],      # venue_order_id
    Optional[str],      # reject_reason
    Optional[str],      # error_code
]:
    """Map a Binance ``order`` REST ack to a venue-native status.

    Decoding rules (priority):

      * ``status`` field, when present, drives the status enum;
      * ``code`` field, when ``status`` is absent, maps the reject
        reason via the canonical -2010 / -2008 / -1013 / -1021 /
        -1022 / -1003 / -2015..-2019 dictionaries (anything else
        → ``OTHER``);
      * ``filled_qty`` is taken from ``executedQty`` /
        ``cumQty`` / ``filledQty`` / ``origQty`` (priority order).
    """
```

### 2.4 `parse_wss_userdata_message` — pure parser

```python
def parse_wss_userdata_message(raw: str) -> Optional[Dict[str, Any]]:
    """Parse one user-data WS frame.

    Returns ``None`` on unparseable input.  Otherwise returns a dict
    with at minimum ``{kind, event_type, ts_ms, client_order_id}``.
    ``ORDER_TRADE_UPDATE`` frames additionally carry ``symbol``,
    ``side``, ``status_raw``, ``filled_qty``, ``avg_price``,
    ``commission``, ``venue_order_id``.

    Supported ``event_type``: ``ORDER_TRADE_UPDATE``,
    ``ACCOUNT_UPDATE``, ``listenKeyExpired``, ``error``, anything
    else (kind ``WSS_OTHER``).
    """
```

### 2.5 `BinancePerpAdapter`

```python
class BinancePerpAdapter:
    def __init__(
        self, *, journal: OrderJournal,
        policy: BinancePerpAdapterPolicy = DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
        auto_recover: bool = True,
    ) -> None: ...
```

`auto_recover=True` (default) re-populates the in-memory cache
from `binance_perp_intents` on construction (cold-start path).

#### Runner hooks

```python
def on_request(
    self, request: Mapping[str, Any], journal: OrderJournal,
    ts_ns: int,
) -> ComponentResult: ...

def on_fill(
    self, request: Mapping[str, Any], ack: Mapping[str, Any],
    journal: OrderJournal, ts_ns: int,
) -> ComponentResult: ...

def record_reject(
    self, *, request: Mapping[str, Any],
    ack: Mapping[str, Any], ts_ns: Optional[int] = None,
) -> Optional[BinancePerpState]: ...
```

`on_request` validates the perp intent, UPSERTs the additive
``binance_perp_intents`` row + INSERTs the ``INTENT_TAGGED``
event, and folds the observation into the runner ack envelope.

`on_fill` classifies the Binance REST ack via
`classify_binance_perp_rest_ack`, UPSERTs the additive tables,
and INSERTs an `ACK_OK` / `ACK_PARTIAL` / `ACK_REJECT` event.
Duplicate callbacks against a terminal outcome are a no-op
(`observation["binance_perp_adapter"] == "duplicate_callback"`).

`record_reject` journals a terminal `REJECTED` outcome (the
venue refused the perp intent outright).  Auto-discovered by
the runner's `_maybe_register_projection` (P7-EXEC-051 pattern).

#### Reads / recovery

```python
def get(self, client_order_id: str) -> Optional[BinancePerpState]: ...
def snapshot(self) -> BinancePerpSnapshot: ...
def recover(self) -> BinancePerpSnapshot: ...
def apply_wss_event(
    self, parsed: Mapping[str, Any], *, ts_ns: int,
) -> ComponentResult: ...
def wss_snapshot(self) -> BinancePerpWssSnapshot: ...
```

`snapshot` returns the aggregate view; `recover` rebuilds the
cache from `binance_perp_intents`; `apply_wss_event` folds a
parsed WS frame into the additive tables and reconciles against
the live state.

### 2.6 `BinancePerpAdapterPolicy`

```python
@dataclass(frozen=True)
class BinancePerpAdapterPolicy:
    venue: str = "binance_usdt_futures"
    allow_algos: bool = False
    block_on_invalid: bool = False
    recv_window_ms: int = 5000
    default_tif: str = "GTC"
    wss_max_reconnects: int = 5
    wss_heartbeat_s: float = 30.0
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
```

Validated by `__post_init__`; rejects non-positive `recv_window_ms`
/ `wss_heartbeat_s` and `wss_max_reconnects < 0`.

Secrets (`api_key`, `api_secret`) are NEVER serialised by
`policy_fingerprint` (the to_dict() surface records only the
``api_secret_set`` boolean).

### 2.7 Status / source enums

```python
class BinancePerpStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"

class BinancePerpAckSource(str, Enum):
    REST = "rest"
    WSS = "wss"

class BinancePerpWssState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    HALTED = "HALTED"
```

`FILLED` / `CANCELED` / `EXPIRED` / `REJECTED` / `BLOCKED` are
terminal — duplicate callbacks against any of these return a
`duplicate_callback` observation (no projection change).

## 3. Registration protocol

```python
from execution.venue_adapter_binance_perp_p7exec_003 import (
    BinancePerpAdapter, register_with_runner,
)

adapter = BinancePerpAdapter(journal=runner.journal)
register_with_runner(runner, adapter)
# → runner.register(adapter)             # on_request hook
# → runner.register_on_fill(adapter)     # on_fill hook
# → runner._projection_components += [adapter]   # record_reject
```

`register_with_runner` is a convenience wrapper; the adapter
also accepts the lower-level `runner.register(adapter)` +
`runner.register_on_fill(adapter)` calls directly.

## 4. Transport wiring (paper-trading vs live)

```python
# Paper-trading (no credentials, deterministic per-coid fills):
from execution.venue_adapter_binance_perp_p7exec_003 import (
    BinancePerpPaperTransport, BinancePerpPaperTransportFillModel,
)
paper = BinancePerpPaperTransport()
paper.fill_model["perp-001"] = BinancePerpPaperTransportFillModel(
    reject_code=-2010,
    reject_message="Account has insufficient balance.",
)
runner = ExecutionRunner(
    journal=journal,
    transport=OutboundTransport(callable_send=paper),
)

# Live (api_secret set via env; never hardcoded):
from execution.venue_adapter_binance_perp_p7exec_003 import (
    OutboundBinancePerpTransport,
)
import os
live = OutboundBinancePerpTransport(
    callable_send=my_urllib_post,   # wraps urllib.request POST
    api_key=os.environ["BINANCE_PERP_KEY"],
    api_secret=os.environ["BINANCE_PERP_SECRET"],
    recv_window_ms=5000,
)
runner = ExecutionRunner(
    journal=journal,
    transport=OutboundTransport(callable_send=live),
)
```

`OutboundBinancePerpTransport` coerces the runner's internal
request into Binance wire keys (``clientOrderId`` /
``quantity`` / ``timeInForce`` / ``type``), adds
``newOrderRespType``, signs when ``api_secret`` is supplied,
and forwards to the caller-supplied HTTP callback.

## 5. WebSocket consumer protocol

```python
from execution.venue_adapter_binance_perp_p7exec_003 import (
    BinancePerpWssConsumer,
)

consumer = BinancePerpWssConsumer(adapter=adapter, listen_key="...")
consumer.connect(ts_ns=...)
# In production, wire to ``websocket-client`` or
# ``websockets.run_until_complete`` on
# ``wss://fstream.binance.com/ws/<listen_key>``; each frame is
# one JSON string:
res = consumer.push_frame(raw_frame, ts_ns=...)
snapshot = consumer.snapshot()
```

The consumer state machine:

```
DISCONNECTED  --(connect)-->  CONNECTING  --(connect ok)-->  CONNECTED
                                                                |
                                                                v
                                                listenKeyExpired / conn lost
                                                                |
                                                                v
                                          RECONNECTING  --(cap exceeded)-->  HALTED
```

`listenKeyExpired` is treated as a reconnect signal; the
counter caps at ``policy.wss_max_reconnects`` (default 5) before
the consumer transitions to ``HALTED``.

## 6. Constraints

* Hot-path overhead per `on_request` / `on_fill` call: < 250us
  pure Python.  Component-owned paths are measured at ~43us /
  ~60us median respectively on stdlib sqlite3 + WAL.  See
  `evidence/bench.json` for the actuals on this hardware.
* Local state journaled via `OrderJournal` (SQLite WAL).
  Cold-start rebuilds the live adapter in O(N) over
  `binance_perp_intents` without replaying the event log.
* NEVER silently drop fills — every REST ack + every WS
  `ORDER_TRADE_UPDATE` lands in `binance_perp_acks` alongside
  the runner's canonical `fills` row.  The runner's row is
  unchanged.
* Idempotence — duplicate `on_fill` / `record_reject`
  callbacks for the same terminal coid are no-ops on the live
  cache (terminal status never overwrites terminal).
* Folder suffix `_p7exec_NNN` — folder is
  `venue_adapter_binance_perp_p7exec_003`.  No `_v1` / `_v2` ever.

## 7. Companion tables (unchanged)

This component does NOT add any new columns to existing tables.
The canonical event log (`fills`) remains the single source of
truth for fill accounting; `binance_perp_events` is the audit
log of perp state transitions as tracked by this component;
`binance_perp_intents` mirrors the perp intents; `binance_perp_acks`
is the venue-side terminal projection.
