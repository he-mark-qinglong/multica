# venue_adapter_binance_perp — Spec

> **Component**: `venue_adapter_binance_perp` · **Issue**: [P7-EXEC-003](../) ·
> **Status**: v1, paper-trading smoke + unit + benchmark passing, hot-path budget verified.
> **Folder suffix rule**: `_p7exec_NNN`, never `_v1`/`_v2` (enforced).

## Purpose

Binance USDT-M perpetual (futures) **REST + WS** venue adapter
for the live execution runner.  The adapter is the first end-to-end
venue integration in the P7-EXEC stack: it owns signing,
pre-trade validation, ack classification, and durable journal
reconciliation for both the REST ``/fapi/v1/order`` path and the
``userDataStream`` WebSocket path.

Every perp-targeted intent pushed through
``runner.submit()`` is journaled in three additive tables alongside
the runner's canonical ``fills`` row, and every ack the venue
returns (REST or WS) lands in the same additive tables before
returning.  Terminal outcomes never regress — a duplicate delivery
from the venue is a no-op.  Cold-start replay rebuilds the live
cache from the additive tables via ``BinancePerpAdapter.recover()``.

## What this component owns

* **HMAC-SHA256 signing** of outbound REST requests, sorted
  alphabetically per Binance's wire spec
  (``urllib.parse.urlencode`` with ``doseq=True`` and
  ``quote_via=quote``); the signature is byte-stable across
  insertion orders and across Python processes.
* **pre-trade validation** of perp-targeted intents
  (``venue`` matches ``binance_usdt_futures`` OR
  ``binance_perp=True`` opt-in; ``symbol`` ends with ``USDT`` or
  ``USDC``; ``qty`` finite positive; ``price`` finite positive for
  LIMIT; ``time_in_force`` in the perp set (``GTC`` / ``IOC`` /
  ``FOK`` / ``GTX`` / ``LIMIT_MAKER``)).
* **tag injection** on the outbound request
  (``newOrderRespType="RESULT"``, ``recvWindow``, ``timestamp``,
  ``signature``).
* **durable journal** of every accepted intent + every received
  ack via three additive tables
  (``binance_perp_intents`` / ``binance_perp_events`` /
  ``binance_perp_acks``).
* **REST ack classification** (``NEW`` / ``PARTIALLY_FILLED`` /
  ``FILLED`` / ``CANCELED`` / ``EXPIRED`` / ``REJECTED`` /
  ``EXPIRED_IN_MATCH``); reject codes (-2010 / -2008 / -1013 /
  -1021 / -1022 / -1003 / -2015..-2019) are mapped to canonical
  reason labels (``INSUFFICIENT_MARGIN``, ``SYMBOL_HALTED``,
  ``PRICE_BAND``, ``TIMESTAMP_OUTSIDE_RECVWINDOW``,
  ``INVALID_SIGNATURE``, ``RATE_LIMITED``, ``INVALID_API_KEY``,
  ``OTHER``).
* **WS ack classification** via
  :func:`parse_wss_userdata_message` (``ORDER_TRADE_UPDATE`` /
  ``ACCOUNT_UPDATE`` / ``listenKeyExpired`` / ``error`` /
  unknown ``kind``); the adapter's
  :meth:`BinancePerpAdapter.apply_wss_event` folds each frame
  into the additive tables and reconciles the result with the
  REST path via ``venue_order_id``.
* **WebSocket consumer** (:class:`BinancePerpWssConsumer`) with
  state machine ``DISCONNECTED`` / ``CONNECTING`` /
  ``CONNECTED`` / ``RECONNECTING`` / ``HALTED``; ``listenKeyExpired``
  triggers an auto-reconnect (capped by ``policy.wss_max_reconnects``,
  after which the consumer transitions to ``HALTED``).
* **transport contracts**:
  - :class:`OutboundBinancePerpTransport` — the wire-level
    transport the runner invokes with the signed outbound request
    (canonical Binance wire shape; signs when ``api_secret`` is
    supplied).
  - :class:`BinancePerpPaperTransport` — paper-trading
    transport with deterministic per-coid outcomes; suitable
    for cold-start / smoke / unit tests without live
    credentials.

## What this component is NOT

* **Not** the canonical fill log.  The ``fills`` table is the
  single source of truth for fill accounting; the additive
  ``binance_perp_*`` tables are the venue-specific audit log.
* **Not** a position reconciler.  Per-symbol drift lives in
  ``position_reconciler_p7exec_053``.
* **Not** a clock-drift detector.  ``venue_clock_drift_p7exec_061``
  owns the ``venue_ts_ns`` vs ``system_ts_ns`` lens.
* **Not** a connectivity probe.  ``router_health_probe_p7exec_025``
  owns the HTTP-RTT-based per-venue health lens.
* **Not** an OKX / Bybit / Coinbase adapter.  Binance USDT-M
  only — venue reuse is opt-in via the ``binance_perp`` flag.

## Layout

```
venue_adapter_binance_perp_p7exec_003/
├── __init__.py                                         ← public surface
├── README.md                                           ← this file
├── INTERFACE.md                                        ← wire contract
├── venue_adapter_binance_perp.py                       ← implementation
├── test_venue_adapter_binance_perp.py                  ← unit tests (plain asserts)
├── test_smoke.py                                       ← end-to-end paper-trading smoke
├── bench_venue_adapter_binance_perp.py                 ← hot-path benchmark
└── evidence/
    ├── bench.json
    └── smoke.json
```

## Public surface

See [INTERFACE.md](./INTERFACE.md).  Summary:

```python
from execution.venue_adapter_binance_perp_p7exec_003 import (
    BinancePerpAdapter,
    BinancePerpAdapterPolicy,
    DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
    BinancePerpPaperTransport,
    BinancePerpPaperTransportFillModel,
    BinancePerpStatus,
    BinancePerpWssConsumer,
    BinancePerpWssState,
    OutboundBinancePerpTransport,
    bootstrap_journal,
    classify_binance_perp_rest_ack,
    parse_wss_userdata_message,
    policy_fingerprint,
    register_with_runner,
    sign_binance_perp_request,
    validate_perp_intent,
)
from execution.runner import (
    ExecutionRunner, OrderJournal, OutboundTransport,
)

journal = OrderJournal("/var/lib/multica/perp.db")
adapter = BinancePerpAdapter(journal=journal)

# Paper-trading wiring (no live credentials):
runner = ExecutionRunner(
    journal=journal,
    transport=OutboundTransport(
        callable_send=BinancePerpPaperTransport(),
    ),
)
register_with_runner(runner, adapter)

# Live wiring (api_secret set via env; never hardcoded):
import os
live_transport = OutboundBinancePerpTransport(
    callable_send=my_venue_send,
    api_key=os.environ["BINANCE_PERP_KEY"],
    api_secret=os.environ["BINANCE_PERP_SECRET"],
    recv_window_ms=5000,
)
live_runner = ExecutionRunner(
    journal=journal,
    transport=OutboundTransport(callable_send=live_transport),
)
register_with_runner(live_runner, adapter)

# WebSocket consumer (separate from the runner; back-fills the
# additive tables with WS-only fills that may follow the REST
# ack):
wss = BinancePerpWssConsumer(adapter=adapter, listen_key="...")
wss.connect(ts_ns=...)
wss.push_frame(raw_json_frame, ts_ns=...)
```

## Constraints (from MAP-P7)

| Constraint | How this component meets it |
|------------|-----------------------------|
| Hot-path overhead < 250us Python | All hot-path medians verified <=250us on stdlib sqlite3 + WAL; the perp ``on_request`` path is ~43us median, the ``on_fill`` path is ~60us median, the WSS apply path is ~53us median.  Benchmarks in ``evidence/bench.json``.  Live trading with sub-50us latency should keep the pre-trade path within budget; if a deploy needs sub-100us end-to-end, the signing + journal windows can be offloaded to a Rust extension without changing the additive schema (the adapter's own Python surface stays unchanged). |
| Local state journaled (WAL / sqlite) | Three additive tables; cold-start replay rebuilds from ``binance_perp_intents`` via ``recover()``. |
| NEVER silently drop fills | Every REST ack and every ``ORDER_TRADE_UPDATE`` WS frame lands in the additive tables; the canonical ``fills`` row + ``binance_perp_acks`` row agree.  Smoke ``evidence/smoke.json`` enforces this contract. |
| Folder suffix ``_p7exec_NNN`` | Folder is ``venue_adapter_binance_perp_p7exec_003``.  No ``_v1``/``_v2`` ever. |

## Acceptance checks (this PR)

- ✅ Unit tests cover the validator (``validate_perp_intent`` for
  canonical / non-perp / algo / market-without-price / lowercase /
  non-eligible-pair / invalid-tif cases), the signer
  (``sign_binance_perp_request`` deterministic, tamper-detection,
  timestamp injection, recv_window override), the REST ack
  classifier (``FILLED`` / ``PARTIALLY_FILLED`` / ``REJECTED`` with
  -2010/-9999/empty-status, signed qty on SELL, missing-key
  tolerance), the WS frame parser (``ORDER_TRADE_UPDATE`` /
  ``ACCOUNT_UPDATE`` / ``listenKeyExpired`` / unparseable /
  unknown-event), the DDL idempotence, the on_request
  passthrough-vs-tag-vs-validation-fail paths, the on_fill
  classify / idempotence / PARTIAL replay, the record_reject
  terminal promotion, the WSS consumer (connect / frame / listen
  key expired / unparseable / halted-after-max-reconnects /
  account-update counter), the runner registration wiring, and
  paper-transport round-trips (FILLED + REJECTED).
- ✅ Integration smoke drives 4 intents through the live runner +
  paper transport with both REST and WS paths; WS
  ``ORDER_TRADE_UPDATE`` promotes a PARTIAL REST coid to
  terminal FILLED; ``listenKeyExpired`` transitions to
  RECONNECTING; durable reopen rebuilds the cache with every
  intent + ack intact.
- ✅ Latency budget verified below 250us median for every hot
  path (signed signing ~21us, validation ~2.5us, classification
  ~4.1us, WSS parse ~7us, ``on_request`` passthrough ~1.2us,
  ``on_request`` tagged ~43us, ``on_fill`` ~60us, ``apply_wss``
  ~53us).  Full runner ``submit()`` cycle ~162us median,
  comfortably inside the 1500us soft budget.

## Open issues (deferred, NOT blockers)

* SBE / WebSocket transport via Binance's newer lowercase
  ``ws-api.binance.com:443/ws-api/v3`` schema (post-cutover
  from ``fstream.binance.com:443/ws``).  Today's WS consumer
  is bytes-compatible with the JSON event payload Binance
  ships on the userDataStream; the adapter's frame parser is
  the switchable boundary.
* Auto-pause on the runner when the consumer transitions to
  ``HALTED`` (today the consumer surfaces ``HALTED`` and the
  caller decides).  Out of scope until the runner grows a
  per-venue throttle pause surface.
* Multi-listenKey rotation for high-throughput strategies
  (single-listenKey serves >= 24 hours; clusters frequently
  rotate to avoid hitting the per-listenKey request quota).
  Out of scope for v1.
* Per-symbol qty / price precision enforcement lives in
  ``price_precision_normalizer_p7exec_063`` +
  ``qty_unit_normalizer_p7exec_062`` today; the adapter
  trusts the upstream pipeline for precision rounding.  The
  additive journal records ``qty`` exactly as the venue
  reported — no rounding — so the audit trail is faithful.

These are tracked under the parent
[MAP-P7 Live Trading Infrastructure project](#); do not let
them block this component.
