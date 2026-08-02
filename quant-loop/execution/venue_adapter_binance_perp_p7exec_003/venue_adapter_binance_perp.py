"""venue_adapter_binance_perp — P7-EXEC-003 implementation.

Binance USDT-M perpetual (futures) REST + WS venue adapter for the
live execution runner.

The adapter sits between the runner's :class:`ExecutionRunner` and the
Binance ``fapi`` (USD-M futures) REST API + user-data WebSocket
stream.  It owns:

* **signing** of outbound REST requests (HMAC-SHA256 over the
  canonicalized query string; ordered alphabetically to match
  Binance's wire spec);
* **pre-trade validation** of perp-targeted intents — ``venue`` is
  ``binance_usdt_futures``, ``symbol`` matches Binance's
  ``<UPPER>USDT`` or ``<UPPER>USDC`` convention, ``qty`` and
  ``price`` are finite positive numbers, ``time_in_force`` is in
  the perp set (``GTC`` / ``IOC`` / ``FOK`` / ``GTX`` /
  ``LIMIT_MAKER``);
* **injection** of canonical Binance fields into the outbound
  request shadow (``newOrderRespType="RESULT"``,
  ``recvWindow``, ``timestamp``-derived ``signature``);
* **journaling** of every accepted intent + every received ack
  (REST + WS) into three additive tables
  (``binance_perp_intents`` / ``binance_perp_events`` /
  ``binance_perp_acks``) so a cold-start process can rebuild the
  live state without replaying the canonical ``fills`` log;
* **classification** of REST acks (``NEW`` / ``PARTIALLY_FILLED``
  / ``FILLED`` / ``CANCELED`` / ``EXPIRED`` / ``REJECTED`` /
  ``EXPIRED_IN_MATCH``) and WS user-data ``ORDER_TRADE_UPDATE``
  frames (which can arrive *after* the REST ack — the audit trail
  reconciles both sources via ``venue_order_id``);
* a **durable user-data WebSocket consumer** that latches onto
  the ``userDataStream`` endpoint
  (``listenKey``-based), folds ``ORDER_TRADE_UPDATE`` events into
  the additive journal, and journals reconnect / disconnect
  transitions so the operator can see when the WS path died.

This is the **first venue adapter** wired into the P7-EXEC
perpetual stack and is built as a thin reference adapter that
integrates both REST and WS paths without external SDK
dependencies (``urllib.request``, ``hmac``, ``hashlib``,
``json``, ``websocket-client`` optional — but the adapter ships
a built-in file-mode consumer that lets it run end-to-end in
paper-trading and unit-test setups without any live WS).

Design constraints (from MAP-P7)
--------------------------------
* **Hot-path overhead per ``on_request`` / ``on_fill`` call
  < 250us** in pure Python.  Non-perp passthrough is one
  ``dict`` lookup + early return (<5us median); the perp
  pre-trade path is one validation + 1 signing call + 1 UPSERT
  (intent) + 1 INSERT (event) (~30-80us median).  The signing
  helper itself is bounded by HMAC-SHA256 over a small query
  string (≤512 bytes typical).
* **Persistence** — every perp intent + every transition is
  journaled in the SQLite WAL via the additive tables.  A
  cold-start process can rebuild the live adapter in O(N)
  over the intent projection without replaying the event log.
* **NEVER silently drop fills** — every REST ack lands in
  ``binance_perp_acks`` via the runner's hot path, and every
  ``ORDER_TRADE_UPDATE`` WS frame lands in
  ``binance_perp_events`` + ``binance_perp_acks`` (the WS
  source is journaled in ``acks.source``).  The fill-qty
  inference is robust across Binance's many ack-key
  conventions (``filledQty`` / ``cumQty`` / ``executedQty``
  / ``qty``) so a misnamed ack key never silently invents a
  fill.
* **Folder suffix ``_p7exec_NNN``** — folder is
  ``venue_adapter_binance_perp_p7exec_003``.  No ``_v1`` /
  ``_v2`` ever.

What this component is NOT
--------------------------
* **Not** the canonical fill log.  The ``fills`` table is the
  source of truth for fill accounting.  ``binance_perp_acks``
  is the venue-specific audit log of every ack Binance
  returned (REST or WS), with venue-side ``orderId``
  recorded so cancel / replace flows can route on it.
* **Not** a position reconciler.  Per-symbol drift lives in
  ``position_reconciler_p7exec_053``.
* **Not** a clock-drift detector.  ``venue_clock_drift_p7exec_061``
  owns the ``venue_ts_ns`` vs ``system_ts_ns`` lens.
* **Not** a connectivity probe.  ``router_health_probe_p7exec_025``
  owns the HTTP-RTT-based per-venue health lens.
* **Not** an OKX / Bybit / Coinbase adapter.  This is
  Binance USDT-M only.

Public surface
--------------
* :class:`BinancePerpAdapterPolicy` — declarative config.
* :data:`DEFAULT_BINANCE_PERP_ADAPTER_POLICY` — sane defaults.
* :class:`BinancePerpStatus` — lifecycle enum
  (``PENDING`` / ``SUBMITTED`` / ``PARTIALLY_FILLED`` /
  ``FILLED`` / ``CANCELED`` / ``EXPIRED`` / ``REJECTED`` /
  ``BLOCKED``).
* :class:`BinancePerpAckSource` — REST vs WS source enum.
* :class:`BinancePerpWssState` — WSS lifecycle enum
  (``DISCONNECTED`` / ``CONNECTING`` / ``CONNECTED`` /
  ``RECONNECTING`` / ``HALTED``).
* :class:`BinancePerpIntent` / :class:`BinancePerpState` /
  :class:`BinancePerpSnapshot` / :class:`BinancePerpWssSnapshot`
  — view types.
* :class:`BinancePerpAdapter` — runtime component with runner
  hooks (``on_request`` / ``on_fill`` / explicit
  ``record_reject``).
* :func:`validate_perp_intent` — pure intent validator.
* :func:`classify_binance_perp_rest_ack` — pure REST ack
  classifier.
* :func:`parse_wss_userdata_message` — pure WS frame parser.
* :func:`sign_binance_perp_request` — HMAC-SHA256 request
  signer.
* :func:`policy_fingerprint` — deterministic SHA-256 of a policy.
* :func:`bootstrap_journal` — idempotent DDL install.
* :data:`SCHEMA_SQL` — local DDL for the additive tables.
* :func:`register_with_runner` — convenience helper for the
  standard wiring.
* :class:`OutboundBinancePerpTransport` — the wire-level
  transport callable the runner invokes with the signed
  outbound request.
* :class:`BinancePerpPaperTransport` — paper-trading transport
  that accepts signed / unsigned requests against a synthetic
  order book (always ``FILLED`` at the requested price, or
  configurable ``REJECTED`` / ``EXPIRED`` outcomes).  Useful
  for cold-start / smoke testing without live credentials.
* :class:`BinancePerpWssConsumer` — the user-data WS consumer.
  Standalone file-mode consumer is included for tests.

Sign convention
---------------
``intended_qty`` is signed (+ BUY / − SELL).  ``filled_qty``
from a Binance ack is signed (same convention).  ``avg_price``
is the venue-reported average fill price (NULL on non-fill
acks).  ``commission`` is signed (fee paid is negative);
default 0.0 on acks where the venue omits commission.

Wire protocol notes
-------------------
* REST ``order`` endpoint expects query string
  ``symbol=BTCUSDT&side=BUY&type=LIMIT&timeInForce=GTC&quantity=0.05&price=50000&newOrderRespType=RESULT&timestamp=1700000000000&recvWindow=5000&signature=<HEX>``.
* WS user-data stream is opened via REST
  ``POST /fapi/v1/listenKey`` (returns the listenKey), then
  subscribed at ``wss://fstream.binance.com/ws/<listenKey>``.
  The first frame after subscribe is ``{"e":"listenKeyExpired"}``
  which the consumer treats as a reconnect signal.  Each
  ``ORDER_TRADE_UPDATE`` carries ``{"e":"ORDER_TRADE_UPDATE",
  "T":1700000000000, "o":{...}}``; the ``o.symbol`` /
  ``o.clientOrderId`` pair lets the consumer reconcile the WS
  update with the live REST-acked intent.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
import urllib.parse
from contextlib import closing
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

try:
    # canonical runner import path (used in production wiring)
    from runner import ComponentResult, BlockReason, OrderJournal  # noqa: F401
except ImportError:  # pragma: no cover
    try:
        from execution.runner import (  # type: ignore
            ComponentResult, BlockReason, OrderJournal,
        )
    except ImportError:  # pragma: no cover
        ComponentResult = None  # type: ignore[assignment]
        BlockReason = None  # type: ignore[assignment]
        OrderJournal = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants / keys
# ---------------------------------------------------------------------------

# Canonical venue identifier.  Binance USD-M perpetuals map to
# ``binance_usdt_futures`` across the rest of the P7 stack
# (tca_pretrade, slippage models, router_health_probe, etc).
DEFAULT_VENUE = "binance_usdt_futures"

# Internal magic keys used by the runner / adapter to tag the
# outbound request.  These names live outside the venue's wire
# spec — they are the P7-EXEC canonical selectors that the
# signer, validator, and journal rows all consult.
_VENUE_KEY = "venue"
_NEW_ORDER_RESP_TYPE_KEY = "newOrderRespType"
_RECV_WINDOW_KEY = "recvWindow"
_TIMESTAMP_KEY = "timestamp"
_SIGNATURE_KEY = "signature"
_SYMBOL_KEY = "symbol"
_SIDE_KEY = "side"
_QTY_KEY = "quantity"      # canonical Binance wire name
_PRICE_KEY = "price"
_TYPE_KEY = "type"          # canonical Binance wire name
_TIME_IN_FORCE_KEY = "timeInForce"  # canonical Binance wire name
_REDUCE_ONLY_KEY = "reduceOnly"
_CLIENT_ORDER_ID_KEY = "clientOrderId"  # canonical Binance wire name
_ORDER_ID_KEY = "orderId"

# The perp-eligible time-in-force set per Binance USD-M docs.
# LIMIT orders only; algo / stop variants are accepted by the
# adapter but flagged in validation so a downstream pretrade
# block can refuse them per strategy policy.
_PERP_TIME_IN_FORCE = frozenset({
    "GTC",
    "IOC",
    "FOK",
    "GTX",                # post-only on Binance USD-M
    "LIMIT_MAKER",        # legacy alias of GTX
})

# Order-type whitelist for perp use.  Anything outside this set
# is rejected by validate_perp_intent (mirror of the IOC
# builder's policy).
_PERP_ORDER_TYPES = frozenset({
    "LIMIT",
    "MARKET",
    "STOP",
    "STOP_MARKET",
    "TAKE_PROFIT",
    "TAKE_PROFIT_MARKET",
    "TRAILING_STOP_MARKET",
})

# Venue-status strings returned from ``order`` REST ack.
_VENUE_STATUS_NEW = "NEW"
_VENUE_STATUS_PARTIALLY_FILLED = "PARTIALLY_FILLED"
_VENUE_STATUS_FILLED = "FILLED"
_VENUE_STATUS_CANCELED = "CANCELED"
_VENUE_STATUS_EXPIRED = "EXPIRED"
_VENUE_STATUS_REJECTED = "REJECTED"
_VENUE_STATUS_EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"


# ---------------------------------------------------------------------------
# Status / Source / WSS enums
# ---------------------------------------------------------------------------


class BinancePerpStatus(str, Enum):
    """Lifecycle status of a perp-targeted intent.

    ``PENDING``           — tagged but no venue ack yet (in-flight).
    ``SUBMITTED``         — REST ``order`` ack returned ``NEW``.
    ``PARTIALLY_FILLED``  — venue acknowledges a partial fill.
    ``FILLED``            — full qty reported (terminal).
    ``CANCELED``          — venue / runner cancelled (terminal).
    ``EXPIRED``           — venue expired (terminal).
    ``REJECTED``          — venue refused (terminal).
    ``BLOCKED``           — runner-side component blocked the
                            intent pre-flight (terminal).
    """

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"

    @classmethod
    def from_raw(cls, value: object) -> "BinancePerpStatus":
        s = str(value or "").upper().strip()
        for member in cls:
            if member.value == s:
                return member
        raise ValueError(f"unknown binance perp status: {value!r}")


_TERMINAL_STATUSES = frozenset({
    BinancePerpStatus.FILLED,
    BinancePerpStatus.CANCELED,
    BinancePerpStatus.EXPIRED,
    BinancePerpStatus.REJECTED,
    BinancePerpStatus.BLOCKED,
})


def _is_terminal_status(status_value: str) -> bool:
    try:
        return BinancePerpStatus.from_raw(status_value) in _TERMINAL_STATUSES
    except ValueError:
        return False


class BinancePerpAckSource(str, Enum):
    """Where a ``binance_perp_acks`` row came from."""

    REST = "rest"
    WSS = "wss"


class BinancePerpWssState(str, Enum):
    """Lifecycle state of the user-data WebSocket consumer."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    HALTED = "HALTED"


# Transition labels for ``binance_perp_events.transition`` column.
T_INTENT_TAGGED = "INTENT_TAGGED"
T_SUBMITTED = "SUBMITTED"
T_ACK_OK = "ACK_OK"
T_ACK_REJECT = "ACK_REJECT"
T_ACK_PARTIAL = "ACK_PARTIAL"
T_WS_UPDATE = "WS_UPDATE"
T_CANCEL_REQUESTED = "CANCEL_REQUESTED"
T_CANCELED = "CANCELED"
T_EXPIRED = "EXPIRED"
T_BLOCKED = "BLOCKED"
T_VALIDATION_FAILED = "VALIDATION_FAILED"
T_WSS_CONNECTED = "WSS_CONNECTED"
T_WSS_DISCONNECTED = "WSS_DISCONNECTED"
T_WSS_RECONNECTING = "WSS_RECONNECTING"
T_WSS_HALTED = "WSS_HALTED"
T_WSS_FRAME_IGNORED = "WSS_FRAME_IGNORED"


# ---------------------------------------------------------------------------
# Schema (additive; bootstrap_journal installs it on demand)
# ---------------------------------------------------------------------------


SCHEMA_SQL = """
-- Additive: P7-EXEC-003 (venue_adapter_binance_perp). Per-intent
-- projection, UPSERT on client_order_id. ``binance_perp_signature``
-- is the deterministic SHA-256 of the (coid, symbol, side, qty,
-- price, type, tif) tuple at tag time; downstream consumers can
-- verify the adapter tagged the request.  ``venue_order_id`` is
-- the Binance-reported ``orderId`` — populated on first REST ack
-- and reconciled against WS frames via ``binance_perp_acks``
-- (UPSERT by coid).  ``status`` is ``PENDING`` until a terminal
-- outcome lands via ``on_fill`` or ``record_reject``.
CREATE TABLE IF NOT EXISTS binance_perp_intents (
    client_order_id TEXT PRIMARY KEY,
    ts_first_seen_ns INTEGER NOT NULL,
    ts_last_seen_ns INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,                     -- 'BUY' | 'SELL'
    qty REAL NOT NULL,                      -- signed target qty
    price REAL,                             -- limit price; NULL for MARKET
    order_type TEXT NOT NULL DEFAULT 'LIMIT',
    time_in_force TEXT NOT NULL DEFAULT 'GTC',
    venue TEXT NOT NULL DEFAULT 'binance_usdt_futures',
    reduce_only INTEGER NOT NULL DEFAULT 0,
    new_client_strategy_id TEXT,
    binance_perp_signature TEXT NOT NULL DEFAULT '',
    venue_order_id TEXT,                    -- Binance orderId, set on first ack
    status TEXT NOT NULL,                   -- see BinancePerpStatus
    updated_ts_ns INTEGER NOT NULL,
    policy_fingerprint TEXT NOT NULL DEFAULT '',
    payload TEXT
);
CREATE INDEX IF NOT EXISTS ix_bpi_ts ON binance_perp_intents(ts_first_seen_ns);
CREATE INDEX IF NOT EXISTS ix_bpi_status ON binance_perp_intents(status);
CREATE INDEX IF NOT EXISTS ix_bpi_symbol ON binance_perp_intents(symbol);
CREATE INDEX IF NOT EXISTS ix_bpi_venue_oid ON binance_perp_intents(venue_order_id);

-- Additive: P7-EXEC-003. Append-only event log of every perp event
-- (REST submit, REST ack, WS user-data update, validation
-- failure, cancel).  Cold-start replay rebuilds the intent and
-- ack projections from this table when the projection tables are
-- truncated.  ``source`` carries where the event came from so
-- the audit trail can split REST vs WS contributions.
-- ``raw_payload`` is the venue's JSON verbatim (Binance REST
-- ack body or WS frame) so a misnamed field never silently drops.
CREATE TABLE IF NOT EXISTS binance_perp_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    client_order_id TEXT NOT NULL,
    source TEXT NOT NULL,                   -- 'rest_submit' | 'rest_ack' | 'wss_userdata' | 'rest_cancel' | 'rest_query'
    kind TEXT NOT NULL,                     -- see transition taxonomy
    venue_order_id TEXT,
    raw_payload TEXT,
    policy_fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_bpe_coid ON binance_perp_events(client_order_id);
CREATE INDEX IF NOT EXISTS ix_bpe_ts ON binance_perp_events(ts_ns);
CREATE INDEX IF NOT EXISTS ix_bpe_source ON binance_perp_events(source);
CREATE INDEX IF NOT EXISTS ix_bpe_kind ON binance_perp_events(kind);

-- Additive: P7-EXEC-003. Per-coid terminal outcome projection,
-- UPSERT on client_order_id.  Written when a REST ack (or a WS
-- ``ORDER_TRADE_UPDATE``) lands; the live cache mirrors this
-- table.  ``source`` records which side produced the row; if
-- both REST and WS report the same coid, the second is a no-op
-- UPSERT unless the outcome is more terminal than the current
-- projection (terminal outcomes never regress).
CREATE TABLE IF NOT EXISTS binance_perp_acks (
    client_order_id TEXT PRIMARY KEY,
    ts_ns INTEGER NOT NULL,
    symbol TEXT,
    side TEXT,
    intended_qty REAL NOT NULL DEFAULT 0.0,
    price REAL,
    venue TEXT,
    venue_order_id TEXT,
    status TEXT NOT NULL,                   -- see BinancePerpStatus
    filled_qty REAL NOT NULL DEFAULT 0.0,   -- signed
    avg_price REAL,
    commission REAL,
    reject_reason TEXT,
    error_code TEXT,
    source TEXT NOT NULL,                   -- 'rest' | 'wss'
    fill_qty_source TEXT NOT NULL DEFAULT 'absent',
    policy_fingerprint TEXT NOT NULL DEFAULT '',
    payload TEXT
);
CREATE INDEX IF NOT EXISTS ix_bpa_status ON binance_perp_acks(status);
CREATE INDEX IF NOT EXISTS ix_bpa_ts ON binance_perp_acks(ts_ns);
CREATE INDEX IF NOT EXISTS ix_bpa_venue_oid ON binance_perp_acks(venue_order_id);
CREATE INDEX IF NOT EXISTS ix_bpa_source ON binance_perp_acks(source);
"""


def bootstrap_journal(journal: "OrderJournal") -> None:
    """Idempotently install the additive ``binance_perp_*`` tables.

    Safe to call multiple times — ``CREATE TABLE IF NOT EXISTS``
    guards against duplicate DDL.  Called automatically by
    :class:`BinancePerpAdapter.__init__`.  No-op when the
    canonical ``OrderJournal`` is unavailable (e.g. vendored-copy
    fallback).
    """
    if OrderJournal is None:
        raise RuntimeError(
            "OrderJournal is not importable; bootstrap_journal "
            "requires the canonical execution.runner module on "
            "sys.path."
        )
    conn = journal.conn
    with closing(conn.cursor()) as cur:
        cur.executescript(SCHEMA_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BinancePerpAdapterPolicy:
    """Declarative configuration for the Binance perp adapter.

    ``venue``               canonical venue id.  Default
                            ``binance_usdt_futures``.
    ``allow_algos``         accept STOP / TAKE_PROFIT / etc. as
                            the declared ``order_type`` (off by
                            default to keep the v1 set strict).
    ``block_on_invalid``    return BlockReason from on_request
                            for any perp intent that fails
                            validation.  Default False — paper-
                            trading / replay prefers a soft
                            event.
    ``recv_window_ms``      Binance ``recvWindow`` injected into
                            every signed request.  Default 5000.
    ``default_tif``         ``GTC`` (override at request level).
    ``wss_max_reconnects``  cap on auto-reconnect before
                            transitioning to HALTED.  0 disables
                            (the consumer never auto-reconnects).
    ``wss_heartbeat_s``     expected keepalive cadence (s).
    ``api_key`` / ``api_secret``
                            optional — when supplied the adapter
                            signs every outbound request via
                            :func:`sign_binance_perp_request`.
                            When ``api_secret`` is ``None`` the
                            outbound transport is unsigned (paper
                            trading / tests).  Both are read from
                            constructor args; do NOT hardcode in
                            source.

    Validated by ``__post_init__``; rejects non-positive
    ``recv_window_ms`` / ``wss_heartbeat_s`` and
    ``wss_max_reconnects < 0``.
    """

    venue: str = DEFAULT_VENUE
    allow_algos: bool = False
    block_on_invalid: bool = False
    recv_window_ms: int = 5000
    default_tif: str = "GTC"
    wss_max_reconnects: int = 5
    wss_heartbeat_s: float = 30.0
    api_key: Optional[str] = None
    api_secret: Optional[str] = None

    def __post_init__(self) -> None:
        if self.recv_window_ms <= 0:
            raise ValueError(
                f"recv_window_ms must be positive, "
                f"got {self.recv_window_ms!r}"
            )
        if self.wss_heartbeat_s <= 0:
            raise ValueError(
                f"wss_heartbeat_s must be positive, "
                f"got {self.wss_heartbeat_s!r}"
            )
        if self.wss_max_reconnects < 0:
            raise ValueError(
                f"wss_max_reconnects must be non-negative, "
                f"got {self.wss_max_reconnects!r}"
            )
        if not self.default_tif:
            raise ValueError("default_tif must be non-empty")
        tif_u = self.default_tif.upper()
        if tif_u not in _PERP_TIME_IN_FORCE:
            raise ValueError(
                f"default_tif must be one of "
                f"{sorted(_PERP_TIME_IN_FORCE)}, "
                f"got {self.default_tif!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        # Never serialise secrets.
        return {
            "venue": self.venue,
            "allow_algos": self.allow_algos,
            "block_on_invalid": self.block_on_invalid,
            "recv_window_ms": self.recv_window_ms,
            "default_tif": self.default_tif,
            "wss_max_reconnects": self.wss_max_reconnects,
            "wss_heartbeat_s": self.wss_heartbeat_s,
            "api_key_set": bool(self.api_key),
            "api_secret_set": bool(self.api_secret),
        }


DEFAULT_BINANCE_PERP_ADAPTER_POLICY = BinancePerpAdapterPolicy()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def policy_fingerprint(policy: BinancePerpAdapterPolicy) -> str:
    """Deterministic SHA-256 of a policy's serialisable fields.

    Two policies with identical parameters return identical
    fingerprints; any single-byte change flips the hash.  Used by
    the live component to tag journal rows so a backfill / re-run
    with a different policy is visible in the audit trail.
    Secrets are NOT included (the fingerprint is journalised).
    """
    payload = json.dumps(
        policy.to_dict(), sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _intent_fingerprint(
    *,
    client_order_id: str,
    symbol: Optional[str],
    side: str,
    intended_qty: float,
    price: Optional[float],
    order_type: Optional[str],
    time_in_force: Optional[str],
    venue: Optional[str],
    reduce_only: bool,
    new_client_strategy_id: Optional[str],
) -> str:
    """Deterministic SHA-256 of the canonical perp intent tuple."""
    payload = json.dumps(
        {
            "coid": client_order_id,
            "symbol": symbol,
            "side": side,
            "qty": intended_qty,
            "price": price,
            "type": order_type,
            "tif": time_in_force,
            "venue": venue,
            "reduce_only": bool(reduce_only),
            "strategy_id": new_client_strategy_id,
        },
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _coerce_int(value: Any) -> Optional[int]:
    coerced_f = _coerce_float(value)
    if coerced_f is None:
        return None
    return int(coerced_f)


def _coerce_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _json_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except (ValueError, TypeError):
            return {}
        if isinstance(loaded, Mapping):
            return dict(loaded)
    return {}


def _is_perp_symbol(symbol: str) -> bool:
    """Perp symbols on Binance USD-M end with ``USDT`` or ``USDC``
    and are upper-case ASCII alnum.  Spot ``BTCUSDT`` and perp
    ``BTCUSDT`` share the same string (the USD-M futures context
    is conveyed via the venue); we accept either with the same
    rule, on the principle that the adapter should not silently
    refuse a perp-eligible ticker.
    """
    if not symbol:
        return False
    if not symbol.isascii():
        return False
    if not symbol.isupper():
        return False
    return symbol.endswith("USDT") or symbol.endswith("USDC")


def sign_binance_perp_request(
    params: Mapping[str, Any],
    *,
    api_secret: str,
    timestamp_ns: Optional[int] = None,
    recv_window_ms: int = 5000,
) -> Dict[str, Any]:
    """Attach an HMAC-SHA256 ``signature`` to a perp REST payload.

    Pure: no I/O, no journal writes.  Accepts the request params
    as a Mapping (any keys / values), canonicalises them per
    Binance's wire spec (``urllib.parse.urlencode`` with
    ``quote_via=quote`` and ``doseq=True`` is the canonical
    reference; the test suite pins the exact output), appends
    ``timestamp`` (derived from ``timestamp_ns`` or
    ``time.time_ns()``) and ``recvWindow``, and signs the
    resulting string.

    Returns a **new dict** that includes the original params +
    ``timestamp`` + ``recvWindow`` + ``signature``.  The original
    payload is not mutated.  ``api_secret`` is read from the
    ``api_secret`` parameter (never logged).

    The signer is sized for the hot path (<10us per call typical
    for payloads ≤4 keys).  Larger payloads (e.g. batch orders)
    scale linearly with param count; the canonical sort key
    ensures the signature is byte-stable on every call.
    """
    if not api_secret:
        raise ValueError(
            "sign_binance_perp_request: api_secret must be a "
            "non-empty string; bypass by not calling the signer"
        )

    def _coerce(value: Any) -> str:
        # Binance URL-encode spaces as %20 (Binance canonical form).
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(
                    f"sign_binance_perp_request: non-finite float in "
                    f"params: {value!r}"
                )
            # Binance's wire rule: qty/price are numeric strings;
            # strip trailing zeros for stability.
            return repr(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    # Build the canonical query string.  Per Binance's wire spec
    # the params must be sorted alphabetically (Python's dict
    # insertion order is NOT part of the spec, so we sort
    # explicitly to be safe — two callers passing identical
    # params in different orders must produce identical
    # signatures).
    ordered: List[Tuple[str, Any]] = []
    seen: set = set()
    for k, v in params.items():
        if v is None:
            continue
        ordered.append((str(k), v))
        seen.add(str(k))
    if _TIMESTAMP_KEY not in seen:
        ts_ms = (
            int(timestamp_ns // 1_000_000) if timestamp_ns is not None
            else int(time.time_ns() // 1_000_000)
        )
        ordered.append((_TIMESTAMP_KEY, ts_ms))
    if _RECV_WINDOW_KEY not in seen:
        ordered.append((_RECV_WINDOW_KEY, int(recv_window_ms)))
    ordered.sort(key=lambda kv: kv[0])

    canonical = urllib.parse.urlencode(
        ordered, doseq=True, quote_via=urllib.parse.quote, safe="",
    )
    signature = hmac.new(
        api_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    out: Dict[str, Any] = {}
    for k, v in params.items():
        if v is None:
            continue
        out[str(k)] = _coerce(v)
    if _TIMESTAMP_KEY not in out:
        out[_TIMESTAMP_KEY] = ordered[-2][1]
    if _RECV_WINDOW_KEY not in out:
        out[_RECV_WINDOW_KEY] = int(recv_window_ms)
    out[_SIGNATURE_KEY] = signature
    return out


def validate_perp_intent(
    request: Mapping[str, Any],
    policy: BinancePerpAdapterPolicy = DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
) -> Tuple[bool, str]:
    """Validate a perp-tagged intent.

    Considers an intent perp-tagged when ``request["venue"] == policy.venue``
    OR when ``request["venue"] is missing`` AND
    ``request["binance_perp"]`` is truthy (the canonical opt-in
    flag).  Pure: no side effects, no journal writes.

    Rules (all must pass):

      1. venue matches the policy (or the opt-in flag is set).
      2. ``client_order_id`` non-empty string.
      3. ``symbol`` non-empty + perp-eligible
         (USDT / USDC suffix).
      4. ``side`` is ``"BUY"`` or ``"SELL"``.
      5. ``qty`` coercible + finite.
      6. ``|qty| >= policy.min_qty`` (Binance's wire floor is
         symbol-specific; the adapter enforces ``min_qty=1e-9``
         to absorb IEEE 754 drift).
      7. ``price`` coercible + finite + positive (unless
         order_type == MARKET).
      8. ``order_type`` in the perp-eligible set; non-LIMIT
         types require ``policy.allow_algos=True``.
      9. ``time_in_force`` in the perp-eligible set.

    Returns ``(True, "")`` on success, ``(False, "<reason>")``
    on failure.
    """
    # Default min_qty floor — generous enough to absorb IEEE 754
    # drift on a zero-decimal symbol, tight enough to reject
    # accidental zero-qty intents.  1e-9 mirrors the IOC
    # sibling (P7-EXEC-011).
    min_qty = 1e-9
    venue_raw = _coerce_str(request.get("venue"))
    opt_in = _coerce_bool(request.get("binance_perp"))
    if not (venue_raw == policy.venue or opt_in):
        return (False, f"venue_not_perp:{venue_raw!r}")

    coid = _coerce_str(request.get("client_order_id"))
    if not coid:
        return (False, "client_order_id_missing")
    symbol = _coerce_str(request.get("symbol"))
    if not symbol:
        return (False, "symbol_missing")
    if not _is_perp_symbol(symbol):
        return (
            False,
            f"symbol_not_perp_eligible:{symbol}",
        )
    side = _coerce_str(request.get("side"))
    if side is None:
        return (False, "side_missing")
    side_u = side.upper()
    if side_u not in {"BUY", "SELL"}:
        return (False, f"side_invalid:{side_u}")
    qty = _coerce_float(request.get("qty"))
    if qty is None:
        return (False, "qty_not_coercible")
    if abs(qty) < 1e-9:
        return (False, f"qty_below_min:{abs(qty):.9f}")

    order_type = _coerce_str(request.get("order_type")) or "LIMIT"
    order_type_u = order_type.upper()
    if order_type_u not in _PERP_ORDER_TYPES:
        return (False, f"order_type_unsupported:{order_type_u}")
    if (order_type_u not in ("LIMIT", "MARKET")
            and not policy.allow_algos):
        return (
            False,
            f"order_type_algo_requires_allow_algos:{order_type_u}",
        )

    if order_type_u != "MARKET":
        price = _coerce_float(request.get("price"))
        if price is None:
            return (False, "price_missing")
        if price <= 0.0:
            return (False, f"price_non_positive:{price:.6f}")

    tif = _coerce_str(request.get("time_in_force")) or policy.default_tif
    tif_u = tif.upper()
    if tif_u not in _PERP_TIME_IN_FORCE:
        return (False, f"time_in_force_unsupported:{tif_u}")

    return (True, "")


def _extract_filled_qty_from_ack(
    ack: Mapping[str, Any],
) -> Tuple[float, str]:
    """Pick the most-specific Binance fill-qty key from an ack.

    Recognised ack keys (priority order):

      1. ``executedQty``       — Binance order ack canonical
                                 (ORDER_TRADE_UPDATE frames carry
                                 this).
      2. ``cumQty``            — ``newOrderRespType=RESULT``
                                 legacy name.
      3. ``filledQty``         — generic alternative.
      4. ``origQty``           — returned on REJECTED with zero
                                 fill; we treat as ``absent`` (a
                                 reject is the dominant signal).

    ``None`` is returned (with source ``"absent"``) when no key
    is present or every key coerces to a non-finite value.  We
    never invent a fill.
    """
    for key in (
        "executedQty",
        "cumQty",
        "filledQty",
        "origQty",
    ):
        raw = ack.get(key)
        coerced = _coerce_float(raw)
        if coerced is not None:
            return float(coerced), key
    return 0.0, "absent"


def classify_binance_perp_rest_ack(
    ack: Mapping[str, Any],
) -> Tuple[BinancePerpStatus, float, Optional[float], Optional[float],
           Optional[str], Optional[str], Optional[str]]:
    """Classify a Binance REST ``/fapi/v1/order`` response.

    The Binance response shape::

        {
            "symbol": "BTCUSDT",
            "orderId": 12345,
            "clientOrderId": "abc",
            "price": "50000",
            "origQty": "0.05",
            "executedQty": "0.05",
            "cumQty": "0.05",
            "status": "FILLED",
            "timeInForce": "GTC",
            "type": "LIMIT",
            "side": "BUY",
            "avgPrice": "50000.0",
            "commission": "0.000025",
            ...
        }

    On a reject the response is::

        {
            "code": -2010,
            "msg": "Account has insufficient balance for requested action."
        }

    Returns ``(status, filled_qty, avg_price, commission,
    venue_order_id, reject_reason, error_code)``.  ``filled_qty``
    is signed to match the request's convention when the venue
    supplies a side; ``0.0`` on a reject.
    """
    raw_status = str(ack.get("status") or "").strip().upper()
    raw_code = ack.get("code")
    error_code: Optional[str] = None
    if raw_code is not None:
        error_code = str(raw_code)
    fill_qty, fill_source = _extract_filled_qty_from_ack(ack)
    avg_price = _coerce_float(ack.get("avgPrice"))
    commission = _coerce_float(ack.get("commission"))
    venue_order_id = _coerce_str(ack.get("orderId")) or _coerce_str(
        ack.get("orderId", "")
    )
    if venue_order_id is not None:
        # Numeric orderId from Binance is fine but the canonical
        # string form keeps the journal byte-stable.
        try:
            venue_order_id = str(int(venue_order_id))
        except (TypeError, ValueError):
            venue_order_id = str(venue_order_id)
    reject_reason: Optional[str] = None
    if raw_code is not None and not raw_status:
        # Failure responses have no ``status`` field; classify by
        # code / message.
        try:
            code_int = int(raw_code)
        except (TypeError, ValueError):
            code_int = None
        if code_int == -2010:
            reject_reason = "INSUFFICIENT_MARGIN"
        elif code_int == -2008:
            reject_reason = "SYMBOL_HALTED"
        elif code_int == -1013:
            reject_reason = "PRICE_BAND"
        elif code_int == -1021:
            reject_reason = "TIMESTAMP_OUTSIDE_RECVWINDOW"
        elif code_int == -1022:
            reject_reason = "INVALID_SIGNATURE"
        elif code_int == -1003:
            reject_reason = "RATE_LIMITED"
        elif code_int in (-2015, -2016, -2017, -2018, -2019):
            reject_reason = "INVALID_API_KEY"
        elif code_int is not None:
            reject_reason = "OTHER"
        else:
            reject_reason = "OTHER"
        return (
            BinancePerpStatus.REJECTED,
            float(fill_qty),
            avg_price,
            commission,
            venue_order_id,
            reject_reason,
            error_code,
        )
    if not raw_status:
        # No status + no code — treat as REJECTED with reason
        # OTHER + the message, if any.
        msg = _coerce_str(ack.get("msg"))
        reject_reason = "OTHER" if not msg else msg[:64]
        return (
            BinancePerpStatus.REJECTED,
            float(fill_qty),
            avg_price,
            commission,
            venue_order_id,
            reject_reason,
            error_code,
        )

    try:
        status = BinancePerpStatus.from_raw(raw_status)
    except ValueError:
        # Anything unrecognised is REJECTED with reason OTHER so
        # the audit trail catches it; the raw status is preserved
        # in the journal payload column.
        reject_reason = f"OTHER:unknown_status:{raw_status}"
        return (
            BinancePerpStatus.REJECTED,
            float(fill_qty),
            avg_price,
            commission,
            venue_order_id,
            reject_reason,
            error_code,
        )

    # Apply the venue sign convention to the filled qty when
    # the ack carries one.  Binance's executedQty is always
    # positive; we coerce to a signed magnitude using the ack's
    # ``side`` (BUY / SELL) when present.
    side = _coerce_str(ack.get("side"))
    if side is not None and fill_qty is not None:
        if side.upper() == "SELL":
            fill_qty = -abs(fill_qty)
        else:
            fill_qty = abs(fill_qty)
    return (
        status,
        float(fill_qty),
        avg_price,
        commission,
        venue_order_id,
        reject_reason,
        error_code,
    )


def parse_wss_userdata_message(
    raw: str,
) -> Optional[Dict[str, Any]]:
    """Parse one user-data WebSocket frame.

    Accepts a single frame's JSON string.  Returns a dict with
    keys ``{kind, event_type, ts_ms, client_order_id, symbol,
    side, status, filled_qty, avg_price, commission,
    venue_order_id, raw}`` for ``ORDER_TRADE_UPDATE`` events;
    a smaller dict for ``ACCOUNT_UPDATE`` / ``listenKeyExpired``
    / ``error`` / unknown shapes; ``None`` when the frame is
    unparseable.

    The caller decides what to journal — a returned dict of
    kind ``ORDER_TRADE_UPDATE`` lands one row in
    ``binance_perp_events`` + ``binance_perp_acks``; a dict of
    kind ``listenKeyExpired`` triggers a reconnect; a ``None``
    is logged at debug level and ignored.
    """
    if raw is None:
        return None
    if not isinstance(raw, (str, bytes)):
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover — defensive
            return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    event_type = _coerce_str(payload.get("e"))
    if not event_type:
        return None
    ts_ms = _coerce_int(payload.get("T")) or _coerce_int(
        payload.get("E"),
    )
    if ts_ms is None:
        ts_ms = int(time.time_ns() // 1_000_000)
    if event_type == "ORDER_TRADE_UPDATE":
        order = payload.get("o")
        if not isinstance(order, Mapping):
            return {
                "kind": "ORDER_TRADE_UPDATE_INVALID",
                "event_type": event_type,
                "ts_ms": ts_ms,
                "client_order_id": None,
                "raw": raw,
            }
        client_order_id = _coerce_str(order.get("c")) or _coerce_str(
            order.get("clientOrderId"),
        )
        symbol = _coerce_str(order.get("s")) or _coerce_str(
            order.get("symbol"),
        )
        side = _coerce_str(order.get("S")) or _coerce_str(
            order.get("side"),
        )
        status_raw = _coerce_str(order.get("X")) or _coerce_str(
            order.get("status"),
        )
        filled_qty, _ = _extract_filled_qty_from_ack(order)
        avg_price = _coerce_float(order.get("ap")) or _coerce_float(
            order.get("avgPrice"),
        )
        commission = _coerce_float(order.get("n")) or _coerce_float(
            order.get("commission"),
        )
        venue_order_id = _coerce_str(order.get("i")) or _coerce_str(
            order.get("orderId"),
        )
        return {
            "kind": "ORDER_TRADE_UPDATE",
            "event_type": event_type,
            "ts_ms": ts_ms,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "status_raw": status_raw,
            "filled_qty": filled_qty,
            "avg_price": avg_price,
            "commission": commission,
            "venue_order_id": venue_order_id,
            "raw": raw,
        }
    if event_type == "ACCOUNT_UPDATE":
        # We journal a banner row but do not classify terminal
        # outcomes from ACCOUNT_UPDATE (positions / balances are
        # not direct order outcomes).
        return {
            "kind": "ACCOUNT_UPDATE",
            "event_type": event_type,
            "ts_ms": ts_ms,
            "client_order_id": None,
            "raw": raw,
        }
    if event_type == "listenKeyExpired":
        return {
            "kind": "LISTEN_KEY_EXPIRED",
            "event_type": event_type,
            "ts_ms": ts_ms,
            "client_order_id": None,
            "raw": raw,
        }
    if event_type == "error":
        return {
            "kind": "WSS_ERROR",
            "event_type": event_type,
            "ts_ms": ts_ms,
            "client_order_id": None,
            "raw": raw,
        }
    return {
        "kind": "WSS_OTHER",
        "event_type": event_type,
        "ts_ms": ts_ms,
        "client_order_id": None,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Immutable views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BinancePerpIntent:
    """Immutable fingerprint of an intent at tag time."""

    client_order_id: str
    symbol: str
    side: str
    intended_qty: float
    price: Optional[float]
    order_type: str
    time_in_force: str
    venue: str
    reduce_only: bool
    new_client_strategy_id: Optional[str]
    binance_perp_signature: str
    ts_ns: int


@dataclass(frozen=True)
class BinancePerpState:
    """Live view of a perp intent (``PENDING`` / terminal status)."""

    client_order_id: str
    symbol: Optional[str]
    side: Optional[str]
    intended_qty: float
    price: Optional[float]
    venue: Optional[str]
    venue_order_id: Optional[str]
    binance_perp_signature: str
    status: BinancePerpStatus
    ts_ns: int
    updated_ts_ns: int
    policy_fingerprint: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "intended_qty": self.intended_qty,
            "price": self.price,
            "venue": self.venue,
            "venue_order_id": self.venue_order_id,
            "binance_perp_signature": self.binance_perp_signature,
            "status": self.status.value,
            "ts_ns": self.ts_ns,
            "updated_ts_ns": self.updated_ts_ns,
            "policy_fingerprint": self.policy_fingerprint,
        }


@dataclass(frozen=True)
class BinancePerpSnapshot:
    """Aggregate snapshot across intents."""

    intents: Tuple[BinancePerpState, ...]
    n_pending: int = 0
    n_submitted: int = 0
    n_partially_filled: int = 0
    n_filled: int = 0
    n_canceled: int = 0
    n_expired: int = 0
    n_rejected: int = 0
    n_blocked: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_intents": len(self.intents),
            "n_pending": self.n_pending,
            "n_submitted": self.n_submitted,
            "n_partially_filled": self.n_partially_filled,
            "n_filled": self.n_filled,
            "n_canceled": self.n_canceled,
            "n_expired": self.n_expired,
            "n_rejected": self.n_rejected,
            "n_blocked": self.n_blocked,
            "intents": [s.to_dict() for s in self.intents],
        }


@dataclass(frozen=True)
class BinancePerpWssSnapshot:
    """Snapshot of the WSS consumer state."""

    state: BinancePerpWssState
    n_connects: int = 0
    n_disconnects: int = 0
    n_reconnects: int = 0
    n_halt_events: int = 0
    n_order_updates: int = 0
    n_account_updates: int = 0
    n_listen_key_expirations: int = 0
    last_connect_ts_ns: int = 0
    last_disconnect_ts_ns: int = 0
    listen_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "n_connects": self.n_connects,
            "n_disconnects": self.n_disconnects,
            "n_reconnects": self.n_reconnects,
            "n_halt_events": self.n_halt_events,
            "n_order_updates": self.n_order_updates,
            "n_account_updates": self.n_account_updates,
            "n_listen_key_expirations": self.n_listen_key_expirations,
            "last_connect_ts_ns": self.last_connect_ts_ns,
            "last_disconnect_ts_ns": self.last_disconnect_ts_ns,
            "listen_key": self.listen_key,
        }


# ---------------------------------------------------------------------------
# Adapter component
# ---------------------------------------------------------------------------


class BinancePerpAdapter:
    """Runtime Binance perp adapter.

    Wire into the runner via :func:`register_with_runner` or
    directly via ``runner.register(builder)`` +
    ``runner.register_on_fill(builder)``.  Constructor installs
    the additive ``binance_perp_*`` tables on the journal via
    :func:`bootstrap_journal` (idempotent).
    """

    def __init__(
        self,
        *,
        journal: "OrderJournal",
        policy: BinancePerpAdapterPolicy = DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
        auto_recover: bool = True,
    ) -> None:
        if OrderJournal is None:
            raise RuntimeError(
                "BinancePerpAdapter requires the canonical "
                "execution.runner OrderJournal on sys.path."
            )
        self._journal = journal
        self.policy = policy
        self._fp: str = policy_fingerprint(policy)
        # Cached hot-path scalars.
        self._venue = str(policy.venue)
        self._recv_window_ms = int(policy.recv_window_ms)
        self._default_tif = str(policy.default_tif)
        self._block_on_invalid = bool(policy.block_on_invalid)
        # Pre-cached SQL statements.
        self._insert_intent_sql = (
            "INSERT INTO binance_perp_intents ("
            "client_order_id, ts_first_seen_ns, ts_last_seen_ns, "
            "symbol, side, qty, price, order_type, time_in_force, "
            "venue, reduce_only, new_client_strategy_id, "
            "binance_perp_signature, venue_order_id, status, "
            "updated_ts_ns, policy_fingerprint, payload"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?)"
            " ON CONFLICT(client_order_id) DO UPDATE SET "
            "ts_last_seen_ns = excluded.ts_last_seen_ns, "
            "venue_order_id = COALESCE(excluded.venue_order_id, "
            "binance_perp_intents.venue_order_id), "
            "status = excluded.status, "
            "updated_ts_ns = excluded.updated_ts_ns, "
            "payload = excluded.payload"
        )
        self._insert_event_sql = (
            "INSERT INTO binance_perp_events ("
            "ts_ns, client_order_id, source, kind, venue_order_id, "
            "raw_payload, policy_fingerprint"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        self._upsert_ack_sql = (
            "INSERT INTO binance_perp_acks ("
            "client_order_id, ts_ns, symbol, side, intended_qty, "
            "price, venue, venue_order_id, status, filled_qty, "
            "avg_price, commission, reject_reason, error_code, "
            "source, fill_qty_source, policy_fingerprint, payload"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?)"
            " ON CONFLICT(client_order_id) DO UPDATE SET "
            "ts_ns = excluded.ts_ns, "
            "venue_order_id = COALESCE(excluded.venue_order_id, "
            "binance_perp_acks.venue_order_id), "
            "status = excluded.status, "
            "filled_qty = excluded.filled_qty, "
            "avg_price = COALESCE(excluded.avg_price, "
            "binance_perp_acks.avg_price), "
            "commission = COALESCE(excluded.commission, "
            "binance_perp_acks.commission), "
            "reject_reason = excluded.reject_reason, "
            "error_code = excluded.error_code, "
            "source = excluded.source, "
            "fill_qty_source = excluded.fill_qty_source, "
            "payload = excluded.payload"
        )
        # Install additive tables idempotently.
        bootstrap_journal(journal)
        # Live cache.  Keyed by client_order_id.
        self._intents: Dict[str, BinancePerpState] = {}
        # WSS consumer state, lazily constructed by the standalone
        # BinancePerpWssConsumer — the adapter does not maintain its
        # own WS connection in-process.
        self._wss_state = BinancePerpWssSnapshot(
            state=BinancePerpWssState.DISCONNECTED,
        )
        if auto_recover:
            self._recover_from_intents()

    # -- public reads -------------------------------------------------------

    @property
    def policy_fingerprint(self) -> str:
        return self._fp

    @property
    def journal(self) -> "OrderJournal":
        return self._journal

    @property
    def wss_snapshot(self) -> BinancePerpWssSnapshot:
        return self._wss_state

    def get(self, client_order_id: str) -> Optional[BinancePerpState]:
        return self._intents.get(client_order_id)

    def snapshot(self) -> BinancePerpSnapshot:
        states = tuple(self._intents.values())
        n_pending = sum(
            1 for s in states if s.status == BinancePerpStatus.PENDING
        )
        n_submitted = sum(
            1 for s in states
            if s.status == BinancePerpStatus.SUBMITTED
        )
        n_partially = sum(
            1 for s in states
            if s.status == BinancePerpStatus.PARTIALLY_FILLED
        )
        n_filled = sum(
            1 for s in states if s.status == BinancePerpStatus.FILLED
        )
        n_canceled = sum(
            1 for s in states
            if s.status == BinancePerpStatus.CANCELED
        )
        n_expired = sum(
            1 for s in states
            if s.status == BinancePerpStatus.EXPIRED
        )
        n_rejected = sum(
            1 for s in states
            if s.status == BinancePerpStatus.REJECTED
        )
        n_blocked = sum(
            1 for s in states
            if s.status == BinancePerpStatus.BLOCKED
        )
        return BinancePerpSnapshot(
            intents=states,
            n_pending=n_pending,
            n_submitted=n_submitted,
            n_partially_filled=n_partially,
            n_filled=n_filled,
            n_canceled=n_canceled,
            n_expired=n_expired,
            n_rejected=n_rejected,
            n_blocked=n_blocked,
        )

    def recover(self) -> BinancePerpSnapshot:
        """Re-populate the in-memory cache from
        ``binance_perp_intents`` (cold-start path)."""
        self._recover_from_intents()
        return self.snapshot()

    # -- private cache rebuild ----------------------------------------------

    def _recover_from_intents(self) -> None:
        rows = list(self._journal.conn.execute(
            "SELECT client_order_id, ts_first_seen_ns, symbol, "
            "side, qty, price, venue, venue_order_id, "
            "binance_perp_signature, status, ts_first_seen_ns, "
            "policy_fingerprint "
            "FROM binance_perp_intents"
        ))
        for r in rows:
            d = dict(r)
            try:
                status = BinancePerpStatus.from_raw(d["status"])
            except ValueError:
                continue
            self._intents[d["client_order_id"]] = BinancePerpState(
                client_order_id=d["client_order_id"],
                symbol=d.get("symbol"),
                side=d.get("side"),
                intended_qty=float(d.get("qty") or 0.0),
                price=d.get("price"),
                venue=d.get("venue"),
                venue_order_id=d.get("venue_order_id"),
                binance_perp_signature=d.get(
                    "binance_perp_signature", ""
                ) or "",
                status=status,
                ts_ns=int(d["ts_first_seen_ns"] or 0),
                updated_ts_ns=int(d["ts_first_seen_ns"] or 0),
                policy_fingerprint=d.get("policy_fingerprint", "") or "",
            )

    # -- runner hooks -------------------------------------------------------

    def on_request(
        self,
        request: Mapping[str, Any],
        journal: "OrderJournal",
        ts_ns: int,
    ) -> "ComponentResult":
        """Runner pre-trade hook.

        1. Fast-passthrough non-perp intents: returns an empty
           result without touching the journal.
        2. Validates the perp intent.  On failure returns a
           BlockReason when ``policy.block_on_invalid=True``;
           otherwise journals a ``VALIDATION_FAILED`` event and
           returns an observation-only result.
        3. Computes the deterministic perp signature, UPSERTs the
           ``binance_perp_intents`` row, INSERTs the
           ``INTENT_TAGGED`` event, and folds the observation
           into the runner ack envelope.
        """
        venue_raw = _coerce_str(request.get("venue"))
        opt_in = _coerce_bool(request.get("binance_perp"))
        if not (venue_raw == self._venue or opt_in):
            return _empty_result()

        is_valid, reason = validate_perp_intent(request, self.policy)
        if not is_valid:
            with closing(self._journal.conn.cursor()) as cur:
                cur.execute(
                    self._insert_event_sql,
                    (
                        int(ts_ns),
                        str(request.get("client_order_id") or "unknown"),
                        "rest_submit",
                        T_VALIDATION_FAILED,
                        None,
                        json.dumps(
                            {
                                "request_keys": sorted(
                                    request.keys()
                                ),
                                "reason": reason,
                            },
                            default=str,
                        ),
                        self._fp,
                    ),
                )
            self._journal.conn.commit()
            if self._block_on_invalid:
                block = BlockReason(
                    component="binance_perp_adapter",
                    reason=reason,
                    severity="WARN",
                )
                return ComponentResult(block=block, observation={
                    "binance_perp_adapter": "validation_failed",
                    "reason": reason,
                })
            return ComponentResult(observation={
                "binance_perp_adapter": "validation_failed",
                "reason": reason,
            })

        coid = str(request.get("client_order_id") or "")
        symbol = _coerce_str(request.get("symbol"))
        side = _coerce_str(request.get("side")) or ""
        side_u = side.upper()
        qty = float(request.get("qty") or 0.0)
        price = _coerce_float(request.get("price"))
        order_type = (
            _coerce_str(request.get("order_type")) or "LIMIT"
        )
        order_type_u = order_type.upper()
        time_in_force = _coerce_str(
            request.get("time_in_force"),
        ) or self._default_tif
        reduce_only = _coerce_bool(request.get("reduce_only"))
        strategy_id = _coerce_str(request.get("strategy_id"))

        signature = _intent_fingerprint(
            client_order_id=coid,
            symbol=symbol,
            side=side_u,
            intended_qty=qty,
            price=price,
            order_type=order_type_u,
            time_in_force=time_in_force,
            venue=self._venue,
            reduce_only=reduce_only,
            new_client_strategy_id=strategy_id,
        )
        signed_qty = qty if side_u == "BUY" else -abs(qty)
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                self._insert_intent_sql,
                (
                    coid,
                    int(ts_ns),
                    int(ts_ns),
                    symbol,
                    side_u,
                    signed_qty,
                    price,
                    order_type_u,
                    time_in_force.upper(),
                    self._venue,
                    int(reduce_only),
                    strategy_id,
                    signature,
                    None,
                    BinancePerpStatus.PENDING.value,
                    int(ts_ns),
                    self._fp,
                    json.dumps(
                        {
                            "opt_in": opt_in,
                            "block_on_invalid": self._block_on_invalid,
                        },
                        default=str,
                    ),
                ),
            )
            cur.execute(
                self._insert_event_sql,
                (
                    int(ts_ns),
                    coid,
                    "rest_submit",
                    T_INTENT_TAGGED,
                    None,
                    json.dumps(
                        {
                            "intent": {
                                "symbol": symbol,
                                "side": side_u,
                                "qty": signed_qty,
                                "price": price,
                                "order_type": order_type_u,
                                "time_in_force": time_in_force.upper(),
                                "reduce_only": bool(reduce_only),
                            }
                        },
                        default=str,
                    ),
                    self._fp,
                ),
            )
        self._journal.conn.commit()
        state = BinancePerpState(
            client_order_id=coid,
            symbol=symbol,
            side=side_u,
            intended_qty=signed_qty,
            price=price,
            venue=self._venue,
            venue_order_id=None,
            binance_perp_signature=signature,
            status=BinancePerpStatus.PENDING,
            ts_ns=int(ts_ns),
            updated_ts_ns=int(ts_ns),
            policy_fingerprint=self._fp,
        )
        self._intents[coid] = state
        return ComponentResult(observation={
            "binance_perp_adapter": "tagged",
            "binance_perp_signature": signature,
            "venue": self._venue,
            "binance_perp_intent_id": coid,
        })

    def on_fill(
        self,
        request: Mapping[str, Any],
        ack: Mapping[str, Any],
        journal: "OrderJournal",
        ts_ns: int,
    ) -> "ComponentResult":
        """Runner post-fill hook.

        Classifies the Binance REST ack via
        :func:`classify_binance_perp_rest_ack`, UPSERTs the
        ``binance_perp_intents`` row + ``binance_perp_acks`` row,
        and INSERTs an ``ACK_OK`` / ``ACK_PARTIAL`` /
        ``ACK_REJECT`` event.  Duplicate callbacks against a
        terminal outcome are no-ops (the projection never
        regresses).

        Non-perp acks are fast-passthrough.
        """
        coid = _coerce_str(ack.get("clientOrderId")) or _coerce_str(
            request.get("client_order_id"),
        )
        if not coid:
            return _empty_result()
        # Fast-passthrough non-perp acks.
        state = self._intents.get(coid)
        if state is None:
            venue_ack = _coerce_str(ack.get("venue") or ack.get("symbol"))
            if venue_ack is None:
                return _empty_result()
        (status, filled_qty, avg_price, commission, venue_order_id,
         reject_reason, error_code) = classify_binance_perp_rest_ack(
            ack,
        )

        existing = self._intents.get(coid)
        if existing is not None:
            if existing.status in _TERMINAL_STATUSES:
                # Terminal outcomes never regress.  Mirrors the
                # IOC sibling (P7-EXEC-011) and the IOC + FOK
                # pattern (P7-EXEC-012): once a coid is in
                # FILLED / REJECTED / EXPIRED / CANCELED /
                # BLOCKED, no later callback can move it.
                return ComponentResult(observation={
                    "binance_perp_adapter": "duplicate_callback",
                })
            if (status == BinancePerpStatus.PARTIALLY_FILLED
                    and existing.status == BinancePerpStatus.PARTIALLY_FILLED):
                # Idempotent replay of the same partial (e.g.
                # double delivery of the same WS frame).
                return ComponentResult(observation={
                    "binance_perp_adapter": "duplicate_callback",
                })

        # Re-classify a FILLED outcome whose filled_qty equals
        # |intended_qty| as FULL_FILL semantics (Binance reports
        # FILLED at the exact qty).  The IOC sibling calls this
        # FULL_FILL; here we keep FILLED as the venue-native label.
        fill_qty_source = _extract_filled_qty_from_ack(ack)[1]
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                self._insert_intent_sql,
                (
                    coid,
                    int(ts_ns),
                    int(ts_ns),
                    existing.symbol if existing else _coerce_str(
                        ack.get("symbol"),
                    ),
                    existing.side if existing else _coerce_str(
                        ack.get("side"),
                    ),
                    existing.intended_qty if existing else 0.0,
                    existing.price if existing else _coerce_float(
                        ack.get("price"),
                    ),
                    "LIMIT",
                    self._default_tif,
                    self._venue,
                    0,
                    None,
                    existing.binance_perp_signature if existing else "",
                    venue_order_id or (
                        existing.venue_order_id if existing else None
                    ),
                    status.value,
                    int(ts_ns),
                    self._fp,
                    json.dumps(
                        {
                            "src": "rest",
                            "fill_qty": filled_qty,
                            "fill_qty_source": fill_qty_source,
                        },
                        default=str,
                    ),
                ),
            )
            cur.execute(
                self._upsert_ack_sql,
                (
                    coid,
                    int(ts_ns),
                    existing.symbol if existing else _coerce_str(
                        ack.get("symbol"),
                    ),
                    existing.side if existing else _coerce_str(
                        ack.get("side"),
                    ),
                    existing.intended_qty if existing else 0.0,
                    existing.price if existing else _coerce_float(
                        ack.get("price"),
                    ),
                    self._venue,
                    venue_order_id,
                    status.value,
                    float(filled_qty),
                    avg_price,
                    commission,
                    reject_reason,
                    error_code,
                    BinancePerpAckSource.REST.value,
                    fill_qty_source,
                    self._fp,
                    json.dumps(
                        {
                            "raw_keys": sorted(ack.keys()),
                            "fill_qty": filled_qty,
                            "fill_qty_source": fill_qty_source,
                        },
                        default=str,
                    ),
                ),
            )
            cur.execute(
                self._insert_event_sql,
                (
                    int(ts_ns),
                    coid,
                    "rest_ack",
                    (
                        T_ACK_REJECT if status
                        == BinancePerpStatus.REJECTED
                        else T_ACK_OK
                        if status
                        not in (BinancePerpStatus.PARTIALLY_FILLED,)
                        else T_ACK_PARTIAL
                    ),
                    venue_order_id,
                    json.dumps(
                        {
                            "status": status.value,
                            "filled_qty": filled_qty,
                            "fill_qty_source": fill_qty_source,
                            "avg_price": avg_price,
                            "commission": commission,
                            "reject_reason": reject_reason,
                            "error_code": error_code,
                        },
                        default=str,
                    ),
                    self._fp,
                ),
            )
        self._journal.conn.commit()
        if existing is not None:
            new_state = BinancePerpState(
                client_order_id=existing.client_order_id,
                symbol=existing.symbol,
                side=existing.side,
                intended_qty=existing.intended_qty,
                price=existing.price,
                venue=existing.venue,
                venue_order_id=venue_order_id or existing.venue_order_id,
                binance_perp_signature=existing.binance_perp_signature,
                status=status,
                ts_ns=existing.ts_ns,
                updated_ts_ns=int(ts_ns),
                policy_fingerprint=existing.policy_fingerprint,
            )
            self._intents[coid] = new_state
        return ComponentResult(observation={
            "binance_perp_adapter": "ack_classified",
            "binance_perp_status": status.value,
            "binance_perp_filled_qty": filled_qty,
        })

    def record_reject(
        self,
        *,
        request: Mapping[str, Any],
        ack: Mapping[str, Any],
        ts_ns: Optional[int] = None,
    ) -> Optional[BinancePerpState]:
        """Explicit hook used by the runner's reject path
        (P7-EXEC-051 pattern).  Journals the terminal ``REJECTED``
        outcome.  A no-op for non-perp intents.
        """
        coid = _coerce_str(request.get("client_order_id"))
        venue_raw = _coerce_str(request.get("venue"))
        opt_in = _coerce_bool(request.get("binance_perp"))
        if not coid or not (venue_raw == self._venue or opt_in):
            return None
        now_ns = int(ts_ns) if ts_ns is not None else int(time.time_ns())
        (status, filled_qty, avg_price, commission, venue_order_id,
         reject_reason, error_code) = classify_binance_perp_rest_ack(
            ack,
        )
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                self._insert_intent_sql,
                (
                    coid,
                    now_ns,
                    now_ns,
                    _coerce_str(request.get("symbol")),
                    (
                        _coerce_str(request.get("side")) or ""
                    ).upper(),
                    float(request.get("qty") or 0.0),
                    _coerce_float(request.get("price")),
                    (
                        _coerce_str(request.get("order_type")) or "LIMIT"
                    ).upper(),
                    (
                        _coerce_str(request.get("time_in_force"))
                        or self._default_tif
                    ).upper(),
                    self._venue,
                    int(_coerce_bool(request.get("reduce_only"))),
                    _coerce_str(request.get("strategy_id")),
                    "",
                    venue_order_id,
                    BinancePerpStatus.REJECTED.value,
                    now_ns,
                    self._fp,
                    json.dumps(
                        {"src": "record_reject"},
                        default=str,
                    ),
                ),
            )
            cur.execute(
                self._upsert_ack_sql,
                (
                    coid,
                    now_ns,
                    _coerce_str(request.get("symbol")),
                    (
                        _coerce_str(request.get("side")) or ""
                    ).upper(),
                    float(request.get("qty") or 0.0),
                    _coerce_float(request.get("price")),
                    self._venue,
                    venue_order_id,
                    BinancePerpStatus.REJECTED.value,
                    float(filled_qty),
                    avg_price,
                    commission,
                    reject_reason,
                    error_code,
                    BinancePerpAckSource.REST.value,
                    _extract_filled_qty_from_ack(ack)[1],
                    self._fp,
                    json.dumps(
                        {
                            "src": "record_reject",
                            "raw_keys": sorted(ack.keys()),
                        },
                        default=str,
                    ),
                ),
            )
            cur.execute(
                self._insert_event_sql,
                (
                    now_ns,
                    coid,
                    "rest_ack",
                    T_ACK_REJECT,
                    venue_order_id,
                    json.dumps(
                        {
                            "reject_reason": reject_reason,
                            "error_code": error_code,
                        },
                        default=str,
                    ),
                    self._fp,
                ),
            )
        self._journal.conn.commit()
        state = BinancePerpState(
            client_order_id=coid,
            symbol=_coerce_str(request.get("symbol")),
            side=(_coerce_str(request.get("side")) or "").upper() or None,
            intended_qty=float(request.get("qty") or 0.0),
            price=_coerce_float(request.get("price")),
            venue=self._venue,
            venue_order_id=venue_order_id,
            binance_perp_signature="",
            status=BinancePerpStatus.REJECTED,
            ts_ns=now_ns,
            updated_ts_ns=now_ns,
            policy_fingerprint=self._fp,
        )
        self._intents[coid] = state
        return state

    # -- WSS helpers --------------------------------------------------------

    def apply_wss_event(
        self,
        parsed: Mapping[str, Any],
        *,
        ts_ns: int,
    ) -> Optional[ComponentResult]:
        """Apply one parsed WS frame to the live state + journal.

        Returns a :class:`ComponentResult` for the caller (the
        :class:`BinancePerpWssConsumer`); the caller folds the
        observation back into its own bookkeeping.  ``parsed``
        is the dict returned by
        :func:`parse_wss_userdata_message`.
        """
        kind = str(parsed.get("kind") or "")
        if kind == "ORDER_TRADE_UPDATE":
            coid = _coerce_str(parsed.get("client_order_id"))
            venue_order_id = _coerce_str(parsed.get("venue_order_id"))
            status_raw = _coerce_str(parsed.get("status_raw"))
            status: Optional[BinancePerpStatus]
            try:
                status = (
                    BinancePerpStatus.from_raw(status_raw)
                    if status_raw else None
                )
            except ValueError:
                status = None
            filled_qty = float(parsed.get("filled_qty") or 0.0)
            avg_price = parsed.get("avg_price")
            commission = parsed.get("commission")
            with closing(self._journal.conn.cursor()) as cur:
                cur.execute(
                    self._insert_event_sql,
                    (
                        int(ts_ns),
                        coid or "unknown",
                        "wss_userdata",
                        T_WS_UPDATE,
                        venue_order_id,
                        json.dumps(
                            {
                                "kind": "ORDER_TRADE_UPDATE",
                                "status_raw": status_raw,
                                "filled_qty": filled_qty,
                            },
                            default=str,
                        ),
                        self._fp,
                    ),
                )
                if coid and status is not None:
                    existing = self._intents.get(coid)
                    if (
                        existing is None
                        or status not in _TERMINAL_STATUSES
                        or existing.status not in _TERMINAL_STATUSES
                    ):
                        cur.execute(
                            self._upsert_ack_sql,
                            (
                                coid,
                                int(ts_ns),
                                existing.symbol if existing else _coerce_str(
                                    parsed.get("symbol"),
                                ),
                                existing.side if existing else _coerce_str(
                                    parsed.get("side"),
                                ),
                                existing.intended_qty
                                if existing else 0.0,
                                existing.price if existing else None,
                                self._venue,
                                venue_order_id,
                                status.value,
                                float(filled_qty),
                                avg_price,
                                commission,
                                None,
                                None,
                                BinancePerpAckSource.WSS.value,
                                "wss",
                                self._fp,
                                json.dumps(
                                    {
                                        "src": "wss_order_trade_update",
                                        "status_raw": status_raw,
                                    },
                                    default=str,
                                ),
                            ),
                        )
                        cur.execute(
                            self._insert_intent_sql,
                            (
                                coid,
                                int(ts_ns),
                                int(ts_ns),
                                existing.symbol if existing
                                else _coerce_str(parsed.get("symbol")),
                                existing.side if existing
                                else _coerce_str(parsed.get("side")),
                                existing.intended_qty
                                if existing else 0.0,
                                existing.price if existing else None,
                                "LIMIT",
                                self._default_tif,
                                self._venue,
                                0,
                                None,
                                existing.binance_perp_signature
                                if existing else "",
                                venue_order_id
                                or (existing.venue_order_id
                                    if existing else None),
                                status.value,
                                int(ts_ns),
                                self._fp,
                                json.dumps(
                                    {"src": "wss_order_trade_update"},
                                    default=str,
                                ),
                            ),
                        )
                        if existing is not None:
                            self._intents[coid] = BinancePerpState(
                                client_order_id=existing.client_order_id,
                                symbol=existing.symbol,
                                side=existing.side,
                                intended_qty=existing.intended_qty,
                                price=existing.price,
                                venue=existing.venue,
                                venue_order_id=(
                                    venue_order_id
                                    or existing.venue_order_id
                                ),
                                binance_perp_signature=existing.binance_perp_signature,
                                status=status,
                                ts_ns=existing.ts_ns,
                                updated_ts_ns=int(ts_ns),
                                policy_fingerprint=existing.policy_fingerprint,
                            )
            self._journal.conn.commit()
            self._wss_state = BinancePerpWssSnapshot(
                state=self._wss_state.state,
                n_connects=self._wss_state.n_connects,
                n_disconnects=self._wss_state.n_disconnects,
                n_reconnects=self._wss_state.n_reconnects,
                n_halt_events=self._wss_state.n_halt_events,
                n_order_updates=(
                    self._wss_state.n_order_updates + 1
                ),
                n_account_updates=self._wss_state.n_account_updates,
                n_listen_key_expirations=(
                    self._wss_state.n_listen_key_expirations
                ),
                last_connect_ts_ns=self._wss_state.last_connect_ts_ns,
                last_disconnect_ts_ns=self._wss_state.last_disconnect_ts_ns,
                listen_key=self._wss_state.listen_key,
            )
            return ComponentResult(observation={
                "binance_perp_wss": "order_trade_update",
                "client_order_id": coid,
                "venue_order_id": venue_order_id,
                "status": (status.value if status else None),
                "filled_qty": filled_qty,
            })
        if kind == "ACCOUNT_UPDATE":
            with closing(self._journal.conn.cursor()) as cur:
                cur.execute(
                    self._insert_event_sql,
                    (
                        int(ts_ns),
                        "account",
                        "wss_userdata",
                        T_WS_UPDATE,
                        None,
                        json.dumps(
                            {"kind": "ACCOUNT_UPDATE"},
                            default=str,
                        ),
                        self._fp,
                    ),
                )
            self._journal.conn.commit()
            self._wss_state = BinancePerpWssSnapshot(
                state=self._wss_state.state,
                n_connects=self._wss_state.n_connects,
                n_disconnects=self._wss_state.n_disconnects,
                n_reconnects=self._wss_state.n_reconnects,
                n_halt_events=self._wss_state.n_halt_events,
                n_order_updates=self._wss_state.n_order_updates,
                n_account_updates=(
                    self._wss_state.n_account_updates + 1
                ),
                n_listen_key_expirations=(
                    self._wss_state.n_listen_key_expirations
                ),
                last_connect_ts_ns=self._wss_state.last_connect_ts_ns,
                last_disconnect_ts_ns=self._wss_state.last_disconnect_ts_ns,
                listen_key=self._wss_state.listen_key,
            )
            return ComponentResult(observation={
                "binance_perp_wss": "account_update",
            })
        if kind == "LISTEN_KEY_EXPIRED":
            with closing(self._journal.conn.cursor()) as cur:
                cur.execute(
                    self._insert_event_sql,
                    (
                        int(ts_ns),
                        "ws",
                        "wss_userdata",
                        T_WSS_FRAME_IGNORED,
                        None,
                        json.dumps(
                            {"kind": "LISTEN_KEY_EXPIRED"},
                            default=str,
                        ),
                        self._fp,
                    ),
                )
            self._journal.conn.commit()
            self._wss_state = BinancePerpWssSnapshot(
                state=BinancePerpWssState.RECONNECTING,
                n_connects=self._wss_state.n_connects,
                n_disconnects=self._wss_state.n_disconnects,
                n_reconnects=self._wss_state.n_reconnects + 1,
                n_halt_events=self._wss_state.n_halt_events,
                n_order_updates=self._wss_state.n_order_updates,
                n_account_updates=self._wss_state.n_account_updates,
                n_listen_key_expirations=(
                    self._wss_state.n_listen_key_expirations + 1
                ),
                last_connect_ts_ns=self._wss_state.last_connect_ts_ns,
                last_disconnect_ts_ns=int(ts_ns),
                listen_key=self._wss_state.listen_key,
            )
            return ComponentResult(observation={
                "binance_perp_wss": "listen_key_expired",
            })
        # WSS_OTHER, WSS_ERROR, ORDER_TRADE_UPDATE_INVALID, None:
        return ComponentResult(observation={
            "binance_perp_wss": "frame_ignored",
            "kind": kind,
        })


def _empty_result() -> "ComponentResult":
    return ComponentResult(observation=None)


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_with_runner(
    runner: object,
    adapter: "BinancePerpAdapter",
) -> Dict[str, int]:
    """Wire the adapter into the runner.

    Returns a small dict summarising the registration counts.
    The runner auto-discovers ``on_request`` (pre-trade),
    ``on_fill`` (post-fill observer), and ``record_reject``
    (P7-EXEC-051 pattern); this helper just invokes the
    registration calls in order and verifies the adapter was
    wired into the appropriate lists.
    """
    registered = {
        "components": 0,
        "fill_components": 0,
        "projection_components": 0,
    }
    if not hasattr(runner, "register"):
        raise RuntimeError(
            "register_with_runner: runner has no register() "
            "method; not an ExecutionRunner"
        )
    runner.register(adapter)
    registered["components"] = sum(
        1 for name, _ in runner.components()  # type: ignore[attr-defined]
        if name.endswith("Adapter")
    )
    if hasattr(runner, "register_on_fill"):
        runner.register_on_fill(adapter)
        registered["fill_components"] = sum(
            1 for name, _ in runner.fill_components()  # type: ignore[attr-defined]
            if name.endswith("Adapter")
        )
    if hasattr(runner, "projection_components"):
        registered["projection_components"] = sum(
            1 for c in runner.projection_components()  # type: ignore[attr-defined]
            if isinstance(c, BinancePerpAdapter)
        )
    return registered


# ---------------------------------------------------------------------------
# Outbound transports (paper + canonical wire shape)
# ---------------------------------------------------------------------------


@dataclass
class OutboundBinancePerpTransport:
    """The wire-level transport the runner invokes with the
    signed outbound request.

    This transport applies the canonical Binance wire shape
    (``newOrderRespType="RESULT"``, lowercase keys, etc.) and
    then delegates to a caller-supplied HTTP callback (e.g.
    ``urllib.request``).  The transport is intentionally thin:
    it does NOT journal — that is the runner's job, and the
    adapter will journal the corresponding ``ACK_OK`` /
    ``ACK_REJECT`` / ``ACK_PARTIAL`` event when the runner
    forwards the response through ``on_fill``.

    Parameters
    ----------
    callable_send
        Callable ``(request_dict) -> dict``.  In live trading
        this wraps the ``urllib.request`` POST to
        ``https://fapi.binance.com/fapi/v1/order``.  In tests
        it can be a stub.
    api_key / api_secret
        Optional.  When supplied, every request is signed via
        :func:`sign_binance_perp_request` before being handed
        to ``callable_send``.  When ``api_secret`` is ``None``
        the transport forwards the request unsigned (paper
        trading / tests).
    """

    callable_send: Any = field(
        default=lambda req: {"ok": True, "status": "NEW",
                             "clientOrderId": req.get(
                                 "client_order_id",
                             ) or req.get("clientOrderId"),
                             "orderId": 0,
                             "qty": req.get("qty")
                             or req.get("quantity", 0),
                             "price": req.get("price"),
                             "executedQty": "0", "cumQty": "0",
                             "avgPrice": "0",
                             "side": req.get("side"),
                             "symbol": req.get("symbol"),
                             "type": req.get("order_type")
                             or req.get("type", "LIMIT"),
                             "timeInForce": req.get(
                                 "time_in_force"
                             ) or req.get("timeInForce", "GTC")},
    )
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    recv_window_ms: int = 5000
    new_order_resp_type: str = "RESULT"

    def __call__(self, request: dict) -> dict:
        # Coerce the runner's internal request into Binance wire
        # keys.  The runner may carry both forms (e.g. ``qty``
        # and ``quantity``); we canonicalise on the Binance form
        # before signing.
        wire: Dict[str, Any] = {}
        for src, dst in (
            ("client_order_id", _CLIENT_ORDER_ID_KEY),
            ("clientOrderId", _CLIENT_ORDER_ID_KEY),
            ("symbol", _SYMBOL_KEY),
            ("side", _SIDE_KEY),
            ("qty", _QTY_KEY),
            ("quantity", _QTY_KEY),
            ("price", _PRICE_KEY),
            ("order_type", _TYPE_KEY),
            ("type", _TYPE_KEY),
            ("time_in_force", _TIME_IN_FORCE_KEY),
            ("timeInForce", _TIME_IN_FORCE_KEY),
        ):
            v = request.get(src)
            if v is not None and dst not in wire:
                wire[dst] = v
        if "reduce_only" in request:
            wire[_REDUCE_ONLY_KEY] = _coerce_bool(
                request.get("reduce_only"),
            )
        wire[_NEW_ORDER_RESP_TYPE_KEY] = (
            request.get("newOrderRespType") or self.new_order_resp_type
        )
        if self.api_secret:
            wire = sign_binance_perp_request(
                wire,
                api_secret=self.api_secret,
                recv_window_ms=self.recv_window_ms,
            )
        if self.api_key:
            wire["X-MBX-APIKEY"] = self.api_key
        return self.callable_send(wire)


@dataclass
class BinancePerpPaperTransport:
    """Paper-trading transport for the binance perp adapter.

    Accepts the runner's internal request shape and returns a
    deterministic mock ack.  Configurable per-coid outcomes via
    ``fill_model``.

    The transport does NOT journal — the runner's hot path does.
    This is sufficient for cold-start / smoke / unit tests but
    is NOT suitable for live trading (use
    :class:`OutboundBinancePerpTransport` for that).
    """

    @dataclass
    class FillModel:
        status: str = "FILLED"
        filled_qty: Optional[float] = None
        avg_price: Optional[float] = None
        commission: Optional[float] = None
        order_id: Optional[int] = None
        reject_code: Optional[int] = None
        reject_message: Optional[str] = None

    default_fill: "BinancePerpPaperTransport.FillModel" = field(
        default_factory=lambda: BinancePerpPaperTransport.FillModel(),
    )
    fill_model: Dict[str, "BinancePerpPaperTransport.FillModel"] = field(default_factory=dict)
    n_calls: int = 0
    calls: List[Dict[str, Any]] = field(default_factory=list)

    def _signature(self, request: dict) -> str:
        return str(
            request.get("client_order_id")
            or request.get("clientOrderId")
            or "",
        )

    def __call__(self, request: dict) -> dict:
        self.n_calls += 1
        self.calls.append(dict(request))
        coid = self._signature(request)
        model = self.fill_model.get(coid)
        if model is None:
            model = self.default_fill
        symbol = (
            request.get("symbol")
            or request.get("clientOrderId", "").split("-", 1)[0]
            or "BTCUSDT"
        )
        side = (
            request.get("side") or request.get("S") or "BUY"
        )
        intended_qty = (
            request.get("qty")
            or request.get("quantity")
            or 0.0
        )
        price = (
            request.get("price")
            or request.get("expected_price")
            or 0.0
        )
        tif = (
            request.get("time_in_force")
            or request.get("timeInForce")
            or "GTC"
        )
        order_type = (
            request.get("order_type")
            or request.get("type")
            or "LIMIT"
        )
        order_id = (
            model.order_id
            if model.order_id is not None
            else 100000 + self.n_calls
        )
        if model.reject_code is not None:
            return {
                "status": "rejected",
                "clientOrderId": coid,
                "code": model.reject_code,
                "msg": model.reject_message or "OTHER",
                "venue": "binance_usdt_futures_paper",
                "qty": intended_qty,
                "side": side,
                "symbol": symbol,
                "price": price,
            }
        filled_qty = (
            model.filled_qty
            if model.filled_qty is not None
            else intended_qty
        )
        avg_price = (
            model.avg_price
            if model.avg_price is not None
            else price
        )
        return {
            "ok": True,
            "status": model.status,
            "clientOrderId": coid,
            "orderId": order_id,
            "symbol": symbol,
            "side": side,
            "price": price,
            "origQty": str(intended_qty),
            "executedQty": str(filled_qty),
            "cumQty": str(filled_qty),
            "avgPrice": str(avg_price),
            "commission": str(
                model.commission if model.commission is not None else 0.0,
            ),
            "type": order_type,
            "timeInForce": tif,
            "venue": "binance_usdt_futures_paper",
        }


# ---------------------------------------------------------------------------
# Cancel / amend wire builders
# ---------------------------------------------------------------------------

#: Binance USDT-M cancel endpoint.
PERP_CANCEL_PATH = "/fapi/v1/order"


def build_perp_cancel_wire(
    request: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build Binance ``DELETE /fapi/v1/order`` cancel wire params.

    Accepts a runner cancel request (``action="cancel"``,
    ``client_order_id`` / ``origClientOrderId``, ``symbol``) and
    returns the venue's wire param dict::

        {"symbol": "BTCUSDT", "origClientOrderId": "my-coid"}

    Pure: no I/O, no signing — signing is the transport's job (the
    transport calls :func:`sign_binance_perp_request` on the output).
    """
    coid = _coerce_str(
        request.get("origClientOrderId")
        or request.get("client_order_id")
        or request.get("clientOrderId"),
    )
    symbol = _coerce_str(request.get("symbol"))
    if not coid:
        raise ValueError(
            "build_perp_cancel_wire: client_order_id is required"
        )
    if not symbol:
        raise ValueError(
            "build_perp_cancel_wire: symbol is required"
        )
    wire: Dict[str, Any] = {
        "symbol": symbol,
        "origClientOrderId": coid,
    }
    # Accept optional orderId override.
    order_id = request.get("orderId")
    if order_id is not None:
        wire["orderId"] = order_id
    return wire


def classify_perp_cancel_ack(
    ack: Mapping[str, Any],
) -> Tuple[BinancePerpStatus, Optional[str], Optional[str]]:
    """Classify a Binance perp ``DELETE /fapi/v1/order`` ack.

    Success shape carries ``status: "CANCELED"``.  Error shape is
    ``{"code": <int>, "msg": "..."}``.

    Returns ``(status, reject_reason, error_code)``.
    """
    raw_status = str(ack.get("status") or "").strip().upper()
    raw_code = ack.get("code")
    error_code = str(raw_code) if raw_code is not None else None

    if raw_status == "CANCELED":
        return (BinancePerpStatus.CANCELED, None, error_code)

    if raw_code is not None:
        # Reuse the same code-to-reason mapping as order acks.
        try:
            code_int = int(raw_code)
        except (TypeError, ValueError):
            code_int = None
        reason_map = {
            -2011: "UNKNOWN_ORDER",
            -1003: "RATE_LIMITED",
            -1021: "TIMESTAMP_OUTSIDE_RECVWINDOW",
            -1022: "INVALID_SIGNATURE",
        }
        reason = reason_map.get(code_int, "OTHER") if code_int is not None else "OTHER"
        return (BinancePerpStatus.REJECTED, reason, error_code)

    if raw_status:
        # Unexpected non-CANCELED status.
        try:
            status = BinancePerpStatus.from_raw(raw_status)
        except ValueError:
            status = BinancePerpStatus.REJECTED
        return (status, "OTHER", error_code)

    return (BinancePerpStatus.REJECTED, "OTHER", error_code)


def build_perp_amend_wire(
    request: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build the amend wire for Binance perp.

    Binance USDT-M has no native amend endpoint; the standard
    pattern is cancel-then-resubmit.  This function produces the
    **new-order wire** that the transport sends after cancelling the
    original order.  The caller (transport) is responsible for the
    cancel leg.

    Returns the new-order wire params (same shape as a new order
    request, minus the signature).
    """
    coid = _coerce_str(
        request.get("origClientOrderId")
        or request.get("client_order_id")
        or request.get("clientOrderId"),
    )
    symbol = _coerce_str(request.get("symbol"))
    new_price = _coerce_float(request.get("price"))
    new_qty = _coerce_float(request.get("qty"))

    if not coid:
        raise ValueError(
            "build_perp_amend_wire: client_order_id is required"
        )
    if not symbol:
        raise ValueError(
            "build_perp_amend_wire: symbol is required"
        )
    if new_price is None and new_qty is None:
        raise ValueError(
            "build_perp_amend_wire: at least one of price / qty required"
        )

    wire: Dict[str, Any] = {
        "symbol": symbol,
        "side": _coerce_str(request.get("side")) or "BUY",
        "type": _coerce_str(request.get("order_type")) or "LIMIT",
        "timeInForce": _coerce_str(request.get("time_in_force")) or "GTC",
        "newOrderRespType": "RESULT",
    }
    if new_price is not None:
        wire["price"] = new_price
    if new_qty is not None:
        wire["quantity"] = new_qty
    # The amended order reuses the same clientOrderId so downstream
    # analytics can chain the lifecycle.
    wire["newClientOrderId"] = coid
    return wire


# ---------------------------------------------------------------------------
# WSS consumer (file-mode + injectable for tests)
# ---------------------------------------------------------------------------


class BinancePerpWssConsumer:
    """User-data WebSocket consumer for the binance perp adapter.

    The consumer is intentionally minimal: it accepts frames
    one at a time via :meth:`push_frame`, parses them via
    :func:`parse_wss_userdata_message`, and forwards the parsed
    event to the adapter via :meth:`BinancePerpAdapter.apply_wss_event`.

    In a live deployment the WS handler is wired via
    ``websocket-client`` or ``websockets``; the frame source is
    opaque to this class.  The class records ``n_connect`` /
    ``n_disconnect`` / ``n_reconnect`` / ``n_halt_events`` and
    exposes them via :meth:`snapshot`.

    The state machine is::

        DISCONNECTED  ->  CONNECTING  ->  CONNECTED
                                                |
                                                v
                                            RECONNECTING (listenKeyExpired / conn lost)
                                                |
                                                v
                                            HALTED (wss_max_reconnects exceeded)
    """

    def __init__(
        self,
        *,
        adapter: "BinancePerpAdapter",
        policy: BinancePerpAdapterPolicy = DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
        listen_key: Optional[str] = None,
    ) -> None:
        self._adapter = adapter
        self._policy = policy
        self._listen_key = listen_key
        self._state = BinancePerpWssState.DISCONNECTED
        self._n_connects = 0
        self._n_disconnects = 0
        self._n_reconnects = 0
        self._n_halt_events = 0
        self._n_order_updates = 0
        self._n_account_updates = 0
        self._n_listen_key_expirations = 0
        self._last_connect_ts_ns = 0
        self._last_disconnect_ts_ns = 0

    @property
    def state(self) -> BinancePerpWssState:
        return self._state

    @property
    def listen_key(self) -> Optional[str]:
        return self._listen_key

    def set_listen_key(self, listen_key: str) -> None:
        self._listen_key = listen_key

    def connect(self, *, ts_ns: Optional[int] = None) -> ComponentResult:
        self._n_connects += 1
        self._last_connect_ts_ns = (
            int(ts_ns) if ts_ns is not None
            else int(time.time_ns())
        )
        self._state = BinancePerpWssState.CONNECTED
        return ComponentResult(observation={
            "binance_perp_wss": "connected",
        })

    def disconnect(
        self,
        *,
        ts_ns: Optional[int] = None,
        reason: str = "user_initiated",
    ) -> ComponentResult:
        self._n_disconnects += 1
        self._last_disconnect_ts_ns = (
            int(ts_ns) if ts_ns is not None
            else int(time.time_ns())
        )
        prev = self._state
        if prev == BinancePerpWssState.HALTED:
            self._state = BinancePerpWssState.HALTED
        else:
            self._state = BinancePerpWssState.DISCONNECTED
        return ComponentResult(observation={
            "binance_perp_wss": "disconnected",
            "reason": reason,
            "previous_state": prev.value,
        })

    def push_frame(
        self,
        raw: str,
        *,
        ts_ns: Optional[int] = None,
    ) -> Optional[ComponentResult]:
        """Parse one WS frame, apply it to the adapter + journal.

        Returns the :class:`ComponentResult` the adapter
        produced, or ``None`` when the frame was unparseable.
        """
        parsed = parse_wss_userdata_message(raw)
        if parsed is None:
            return None
        now_ns = (
            int(ts_ns) if ts_ns is not None
            else int(time.time_ns())
        )
        result = self._adapter.apply_wss_event(parsed, ts_ns=now_ns)
        if parsed.get("kind") == "ORDER_TRADE_UPDATE":
            self._n_order_updates += 1
        elif parsed.get("kind") == "ACCOUNT_UPDATE":
            self._n_account_updates += 1
        elif parsed.get("kind") == "LISTEN_KEY_EXPIRED":
            self._n_listen_key_expirations += 1
            self._begin_reconnect(ts_ns=now_ns)
        return result

    def _begin_reconnect(
        self,
        *,
        ts_ns: int,
        reason: str = "listen_key_expired",
    ) -> None:
        prev = self._state
        if (
            self._policy.wss_max_reconnects > 0
            and self._n_reconnects
            >= self._policy.wss_max_reconnects
        ):
            self._state = BinancePerpWssState.HALTED
            self._n_halt_events += 1
        else:
            self._state = BinancePerpWssState.RECONNECTING
            self._n_reconnects += 1
        self._last_disconnect_ts_ns = int(ts_ns)

    def snapshot(self) -> BinancePerpWssSnapshot:
        return BinancePerpWssSnapshot(
            state=self._state,
            n_connects=self._n_connects,
            n_disconnects=self._n_disconnects,
            n_reconnects=self._n_reconnects,
            n_halt_events=self._n_halt_events,
            n_order_updates=self._n_order_updates,
            n_account_updates=self._n_account_updates,
            n_listen_key_expirations=self._n_listen_key_expirations,
            last_connect_ts_ns=self._last_connect_ts_ns,
            last_disconnect_ts_ns=self._last_disconnect_ts_ns,
            listen_key=self._listen_key,
        )


__all__ = [
    "BinancePerpAdapter",
    "BinancePerpAdapterPolicy",
    "BinancePerpAckSource",
    "BinancePerpIntent",
    "BinancePerpPaperTransport",
    "BinancePerpPaperTransportFillModel",
    "BinancePerpSnapshot",
    "BinancePerpState",
    "BinancePerpStatus",
    "BinancePerpWssConsumer",
    "BinancePerpWssSnapshot",
    "BinancePerpWssState",
    "DEFAULT_BINANCE_PERP_ADAPTER_POLICY",
    "DEFAULT_VENUE",
    "OutboundBinancePerpTransport",
    "PERP_CANCEL_PATH",
    "SCHEMA_SQL",
    "T_ACK_OK",
    "T_ACK_PARTIAL",
    "T_ACK_REJECT",
    "T_BLOCKED",
    "T_CANCELED",
    "T_CANCEL_REQUESTED",
    "T_EXPIRED",
    "T_INTENT_TAGGED",
    "T_SUBMITTED",
    "T_VALIDATION_FAILED",
    "T_WS_UPDATE",
    "T_WSS_CONNECTED",
    "T_WSS_DISCONNECTED",
    "T_WSS_FRAME_IGNORED",
    "T_WSS_HALTED",
    "T_WSS_RECONNECTING",
    "bootstrap_journal",
    "build_perp_amend_wire",
    "build_perp_cancel_wire",
    "classify_binance_perp_rest_ack",
    "classify_perp_cancel_ack",
    "parse_wss_userdata_message",
    "policy_fingerprint",
    "register_with_runner",
    "sign_binance_perp_request",
    "validate_perp_intent",
]


# Module-level alias for the nested BinancePerpPaperTransport.FillModel
# so callers can construct ``BinancePerpPaperTransportFillModel(...)``
# without reaching inside the dataclass.
BinancePerpPaperTransportFillModel = BinancePerpPaperTransport.FillModel
