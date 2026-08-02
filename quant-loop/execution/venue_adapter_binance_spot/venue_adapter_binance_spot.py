"""venue_adapter_binance_spot — Binance spot REST venue adapter (E2, E8).

Spot counterpart of
:mod:`execution.venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp`
(read that module first — the structure, sign convention, and
journaling discipline are mirrored here).  The adapter sits between
the execution runner and the Binance spot REST API
(``POST /api/v3/order``) and owns:

* **signing** of outbound REST requests — HMAC-SHA256 over the
  canonicalized query string (:func:`sign_binance_spot_request`),
  identical wire rules to the perp sibling;
* **pre-trade validation** of spot-targeted intents
  (:func:`validate_spot_intent`) — venue is ``binance_spot``, symbol
  is upper-case ASCII alnum (spot has no quote-suffix rule — BTCUSDT,
  ETHBTC and BTCEUR are all legal), ``qty``/``price`` finite and
  positive, ``order_type`` in the spot set and ``time_in_force``
  consistent with it;
* **order-type / TIF matrix** (E8):

  ============  ===============================================
  LIMIT         requires ``timeInForce`` in {GTC, IOC, FOK}
  LIMIT_MAKER   post-only; carries NO ``timeInForce`` on the wire
  MARKET        carries no ``timeInForce`` (immediate-or-cancel by
                construction); IOC/FOK on MARKET is a validation
                error here even though the venue may tolerate it —
                explicit beats implicit
  ============  ===============================================

  IOC fills what is immediately available and cancels the remainder
  (venue status ``FILLED`` or ``EXPIRED`` with a partial
  ``executedQty``); FOK fills in full or not at all (``FILLED`` or
  ``EXPIRED`` with ``executedQty=0``); LIMIT_MAKER rests or is
  rejected (``-2010`` / ``EXPIRED_IN_MATCH``) when it would cross.
* **journaling** of every accepted intent and every ack into the
  additive tables ``binance_spot_intents`` / ``binance_spot_events``
  / ``binance_spot_acks`` (UPSERT on ``client_order_id``), so a
  cold-start process rebuilds live state without replaying the
  canonical ``fills`` log;
* **classification** of REST acks
  (:func:`classify_binance_spot_rest_ack`) — ``NEW`` /
  ``PARTIALLY_FILLED`` / ``FILLED`` / ``CANCELED`` /
  ``PENDING_CANCEL`` / ``REJECTED`` / ``EXPIRED`` /
  ``EXPIRED_IN_MATCH`` — plus Binance error-code classification for
  the ``{"code": ..., "msg": ...}`` failure shape.

Differences from the perp sibling
---------------------------------
* No ``reduceOnly`` (spot has no position side to reduce);
  ``quoteOrderQty`` for MARKET buys is accepted and journaled but not
  required.
* No user-data WebSocket consumer in this module — the spot
  ``executionReport`` WS parser is deliberately left out of scope
  (the REST path + journal is the auditable core); tracked as
  follow-up.
* **Runner independence.**  The perp module hard-depends on the
  canonical ``execution.runner`` (absent in this repo, which makes
  its tests uncollectable).  This module duck-types the journal (any
  object with a ``.conn`` SQLite connection) and falls back to local
  ``ComponentResult`` / ``BlockReason`` definitions when the runner
  is not importable, so the full test-suite runs offline.

References
----------
- Binance Spot API docs — ``POST /api/v3/order``, filters,
  ``LIMIT_MAKER`` semantics, error codes.
- Same execution-design lineage as the perp sibling (Cartea,
  Jaimungal & Penalva 2015, Ch. 6).

Sign convention: ``intended_qty`` is signed (+ BUY / − SELL);
``filled_qty`` from an ack follows the same convention via the ack's
``side``.  ``commission`` is recorded as reported (fee paid is a
positive cost here, unlike the perp module — spot commission is a
separate asset deduction; the column documents the venue value).
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
    Tuple,
)

# ---------------------------------------------------------------------------
# Runner interop (duck-typed; falls back to local definitions so the module
# and its tests run without the canonical execution.runner on sys.path)
# ---------------------------------------------------------------------------

try:  # canonical runner import path (production wiring)
    from runner import ComponentResult, BlockReason  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from execution.runner import (  # type: ignore
            ComponentResult, BlockReason,
        )
    except ImportError:
        @dataclass(frozen=True)
        class BlockReason:  # type: ignore[no-redef]
            component: str
            reason: str
            severity: str = "WARN"

        @dataclass(frozen=True)
        class ComponentResult:  # type: ignore[no-redef]
            block: Optional[BlockReason] = None
            observation: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_VENUE = "binance_spot"

# Binance spot REST order endpoint (documented for the transport;
# the adapter itself performs no I/O).
SPOT_ORDER_PATH = "/api/v3/order"
SPOT_REST_BASE = "https://api.binance.com"

_SYMBOL_KEY = "symbol"
_SIDE_KEY = "side"
_QTY_KEY = "quantity"
_PRICE_KEY = "price"
_TYPE_KEY = "type"
_TIME_IN_FORCE_KEY = "timeInForce"
_QUOTE_ORDER_QTY_KEY = "quoteOrderQty"
_NEW_ORDER_RESP_TYPE_KEY = "newOrderRespType"
_RECV_WINDOW_KEY = "recvWindow"
_TIMESTAMP_KEY = "timestamp"
_SIGNATURE_KEY = "signature"
_CLIENT_ORDER_ID_KEY = "newClientOrderId"
_ORDER_ID_KEY = "orderId"

# Spot time-in-force set.  Post-only is an order *type* on spot
# (LIMIT_MAKER), not a TIF.
_SPOT_TIME_IN_FORCE = frozenset({"GTC", "IOC", "FOK"})

_SPOT_ORDER_TYPES = frozenset({"LIMIT", "MARKET", "LIMIT_MAKER"})

# TIF values that are legal on LIMIT orders (E8).
_TIF_ON_LIMIT = frozenset({"GTC", "IOC", "FOK"})

_VENUE_STATUS_NEW = "NEW"
_VENUE_STATUS_PARTIALLY_FILLED = "PARTIALLY_FILLED"
_VENUE_STATUS_FILLED = "FILLED"
_VENUE_STATUS_CANCELED = "CANCELED"
_VENUE_STATUS_PENDING_CANCEL = "PENDING_CANCEL"
_VENUE_STATUS_REJECTED = "REJECTED"
_VENUE_STATUS_EXPIRED = "EXPIRED"
_VENUE_STATUS_EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BinanceSpotStatus(str, Enum):
    """Lifecycle status of a spot-targeted intent.

    ``PENDING``           — tagged, no venue ack yet (in-flight).
    ``SUBMITTED``         — REST ack returned ``NEW`` (resting).
    ``PARTIALLY_FILLED``  — venue acknowledges a partial fill.
    ``FILLED``            — full qty reported (terminal).
    ``CANCELED``          — venue / runner cancelled (terminal).
    ``EXPIRED``           — IOC/FOK unfilled remainder died (terminal).
    ``REJECTED``          — venue refused (terminal).
    ``BLOCKED``           — runner-side pre-trade block (terminal).
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
    def from_raw(cls, value: object) -> "BinanceSpotStatus":
        s = str(value or "").upper().strip()
        for member in cls:
            if member.value == s:
                return member
        raise ValueError(f"unknown binance spot status: {value!r}")


_TERMINAL_STATUSES = frozenset({
    BinanceSpotStatus.FILLED,
    BinanceSpotStatus.CANCELED,
    BinanceSpotStatus.EXPIRED,
    BinanceSpotStatus.REJECTED,
    BinanceSpotStatus.BLOCKED,
})


def _is_terminal_status(status_value: str) -> bool:
    try:
        return BinanceSpotStatus.from_raw(status_value) in _TERMINAL_STATUSES
    except ValueError:
        return False


class BinanceSpotAckSource(str, Enum):
    """Where a ``binance_spot_acks`` row came from."""

    REST = "rest"
    WSS = "wss"


# Transition labels for binance_spot_events.kind.
T_INTENT_TAGGED = "INTENT_TAGGED"
T_ACK_OK = "ACK_OK"
T_ACK_PARTIAL = "ACK_PARTIAL"
T_ACK_REJECT = "ACK_REJECT"
T_BLOCKED = "BLOCKED"
T_VALIDATION_FAILED = "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# Schema (additive)
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Additive: venue_adapter_binance_spot. Per-intent projection,
-- UPSERT on client_order_id.  ``binance_spot_signature`` is the
-- deterministic SHA-256 of the (coid, symbol, side, qty, price,
-- type, tif) tuple at tag time.
CREATE TABLE IF NOT EXISTS binance_spot_intents (
    client_order_id TEXT PRIMARY KEY,
    ts_first_seen_ns INTEGER NOT NULL,
    ts_last_seen_ns INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,                     -- 'BUY' | 'SELL'
    qty REAL NOT NULL,                      -- signed target qty
    price REAL,                             -- limit price; NULL for MARKET
    order_type TEXT NOT NULL DEFAULT 'LIMIT',
    time_in_force TEXT,                     -- NULL for MARKET / LIMIT_MAKER
    quote_order_qty REAL,                   -- MARKET-buy quote sizing
    venue TEXT NOT NULL DEFAULT 'binance_spot',
    binance_spot_signature TEXT NOT NULL DEFAULT '',
    venue_order_id TEXT,                    -- Binance orderId, set on first ack
    status TEXT NOT NULL,                   -- see BinanceSpotStatus
    updated_ts_ns INTEGER NOT NULL,
    policy_fingerprint TEXT NOT NULL DEFAULT '',
    payload TEXT
);
CREATE INDEX IF NOT EXISTS ix_bsi_ts ON binance_spot_intents(ts_first_seen_ns);
CREATE INDEX IF NOT EXISTS ix_bsi_status ON binance_spot_intents(status);
CREATE INDEX IF NOT EXISTS ix_bsi_symbol ON binance_spot_intents(symbol);
CREATE INDEX IF NOT EXISTS ix_bsi_venue_oid ON binance_spot_intents(venue_order_id);

-- Additive: append-only event log of every spot event (REST submit,
-- REST ack, validation failure).
CREATE TABLE IF NOT EXISTS binance_spot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    client_order_id TEXT NOT NULL,
    source TEXT NOT NULL,                   -- 'rest_submit' | 'rest_ack'
    kind TEXT NOT NULL,
    venue_order_id TEXT,
    raw_payload TEXT,
    policy_fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_bse_coid ON binance_spot_events(client_order_id);
CREATE INDEX IF NOT EXISTS ix_bse_ts ON binance_spot_events(ts_ns);
CREATE INDEX IF NOT EXISTS ix_bse_kind ON binance_spot_events(kind);

-- Additive: per-coid outcome projection, UPSERT on client_order_id.
-- Terminal outcomes never regress.
CREATE TABLE IF NOT EXISTS binance_spot_acks (
    client_order_id TEXT PRIMARY KEY,
    ts_ns INTEGER NOT NULL,
    symbol TEXT,
    side TEXT,
    intended_qty REAL NOT NULL DEFAULT 0.0,
    price REAL,
    venue TEXT,
    venue_order_id TEXT,
    status TEXT NOT NULL,                   -- see BinanceSpotStatus
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
CREATE INDEX IF NOT EXISTS ix_bsa_status ON binance_spot_acks(status);
CREATE INDEX IF NOT EXISTS ix_bsa_ts ON binance_spot_acks(ts_ns);
CREATE INDEX IF NOT EXISTS ix_bsa_venue_oid ON binance_spot_acks(venue_order_id);
"""


def bootstrap_journal(journal: Any) -> None:
    """Idempotently install the additive ``binance_spot_*`` tables.

    ``journal`` is duck-typed: any object exposing a ``.conn``
    :class:`sqlite3.Connection`.  Safe to call repeatedly.
    """
    conn = getattr(journal, "conn", None)
    if conn is None:
        raise TypeError(
            "bootstrap_journal: journal must expose a .conn "
            "sqlite3.Connection"
        )
    with closing(conn.cursor()) as cur:
        cur.executescript(SCHEMA_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BinanceSpotAdapterPolicy:
    """Declarative configuration for the Binance spot adapter.

    ``venue``              canonical venue id (``binance_spot``).
    ``block_on_invalid``   return a BlockReason from ``on_request``
                           when validation fails (default False —
                           journal a VALIDATION_FAILED event instead).
    ``recv_window_ms``     Binance ``recvWindow`` injected into every
                           signed request.
    ``default_tif``        default LIMIT time-in-force (``GTC``).
    ``api_key`` / ``api_secret``
                           optional; when ``api_secret`` is set the
                           outbound transport signs every request.
                           Never hardcode; never serialise.
    """

    venue: str = DEFAULT_VENUE
    block_on_invalid: bool = False
    recv_window_ms: int = 5000
    default_tif: str = "GTC"
    api_key: Optional[str] = None
    api_secret: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.venue:
            raise ValueError("venue must be non-empty")
        if self.recv_window_ms <= 0:
            raise ValueError(
                f"recv_window_ms must be positive, "
                f"got {self.recv_window_ms!r}"
            )
        tif_u = self.default_tif.upper()
        if tif_u not in _SPOT_TIME_IN_FORCE:
            raise ValueError(
                f"default_tif must be one of "
                f"{sorted(_SPOT_TIME_IN_FORCE)}, got {self.default_tif!r}"
            )
        object.__setattr__(self, "default_tif", tif_u)

    def to_dict(self) -> Dict[str, Any]:
        # Never serialise secrets.
        return {
            "venue": self.venue,
            "block_on_invalid": self.block_on_invalid,
            "recv_window_ms": self.recv_window_ms,
            "default_tif": self.default_tif,
            "api_key_set": bool(self.api_key),
            "api_secret_set": bool(self.api_secret),
        }


DEFAULT_BINANCE_SPOT_ADAPTER_POLICY = BinanceSpotAdapterPolicy()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def policy_fingerprint(policy: BinanceSpotAdapterPolicy) -> str:
    """Deterministic SHA-256 of a policy's serialisable fields."""
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
) -> str:
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


def _coerce_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _is_spot_symbol(symbol: str) -> bool:
    """Spot symbols are upper-case ASCII alnum (BTCUSDT, ETHBTC,
    BTCEUR are all legal — unlike the perp sibling there is no
    quote-suffix rule)."""
    if not symbol or not symbol.isascii() or not symbol.isupper():
        return False
    return symbol.isalnum()


def sign_binance_spot_request(
    params: Mapping[str, Any],
    *,
    api_secret: str,
    timestamp_ns: Optional[int] = None,
    recv_window_ms: int = 5000,
) -> Dict[str, Any]:
    """Attach an HMAC-SHA256 ``signature`` to a spot REST payload.

    Pure: no I/O, no journal writes.  Canonicalises params per
    Binance's wire spec (alphabetical sort, ``quote_via=quote``),
    appends ``timestamp`` (derived from ``timestamp_ns`` or
    ``time.time_ns()``) and ``recvWindow``, and returns a **new**
    dict including ``signature``.  The input mapping is not mutated.
    """
    if not api_secret:
        raise ValueError(
            "sign_binance_spot_request: api_secret must be a "
            "non-empty string; bypass by not calling the signer"
        )

    def _coerce(value: Any) -> str:
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(
                    f"sign_binance_spot_request: non-finite float in "
                    f"params: {value!r}"
                )
            return repr(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

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


def validate_spot_intent(
    request: Mapping[str, Any],
    policy: BinanceSpotAdapterPolicy = DEFAULT_BINANCE_SPOT_ADAPTER_POLICY,
) -> Tuple[bool, str]:
    """Validate a spot-tagged intent (pure).

    An intent is spot-tagged when ``request["venue"] == policy.venue``
    or ``request["binance_spot"]`` is truthy.

    Rules:
      1. venue matches the policy (or the opt-in flag is set);
      2. ``client_order_id`` non-empty;
      3. ``symbol`` upper-case ASCII alnum;
      4. ``side`` in {BUY, SELL};
      5. ``qty`` finite with ``|qty| >= 1e-9`` (unless a MARKET BUY
         carries ``quote_order_qty`` instead);
      6. ``order_type`` in {LIMIT, MARKET, LIMIT_MAKER};
      7. LIMIT requires a positive ``price`` and a TIF in
         {GTC, IOC, FOK} (defaulting to ``policy.default_tif``);
      8. LIMIT_MAKER (post-only) requires a positive ``price`` and
         carries NO ``time_in_force``;
      9. MARKET requires no price and no TIF.
    """
    min_qty = 1e-9
    venue_raw = _coerce_str(request.get("venue"))
    opt_in = _coerce_bool(request.get("binance_spot"))
    if not (venue_raw == policy.venue or opt_in):
        return (False, f"venue_not_spot:{venue_raw!r}")

    coid = _coerce_str(request.get("client_order_id"))
    if not coid:
        return (False, "client_order_id_missing")
    symbol = _coerce_str(request.get("symbol"))
    if not symbol:
        return (False, "symbol_missing")
    if not _is_spot_symbol(symbol):
        return (False, f"symbol_not_spot_eligible:{symbol}")
    side = _coerce_str(request.get("side"))
    if side is None:
        return (False, "side_missing")
    side_u = side.upper()
    if side_u not in {"BUY", "SELL"}:
        return (False, f"side_invalid:{side_u}")

    order_type = (_coerce_str(request.get("order_type")) or "LIMIT").upper()
    if order_type not in _SPOT_ORDER_TYPES:
        return (False, f"order_type_unsupported:{order_type}")

    qty = _coerce_float(request.get("qty"))
    quote_qty = _coerce_float(request.get("quote_order_qty"))
    if qty is None or abs(qty) < min_qty:
        if not (order_type == "MARKET" and side_u == "BUY"
                and quote_qty is not None and quote_qty > 0.0):
            return (False, "qty_not_coercible" if qty is None
                    else f"qty_below_min:{abs(qty):.9f}")

    tif_raw = _coerce_str(request.get("time_in_force"))
    if order_type == "LIMIT":
        price = _coerce_float(request.get("price"))
        if price is None:
            return (False, "price_missing")
        if price <= 0.0:
            return (False, f"price_non_positive:{price:.6f}")
        tif_u = (tif_raw or policy.default_tif).upper()
        if tif_u not in _TIF_ON_LIMIT:
            return (False, f"time_in_force_unsupported:{tif_u}")
    elif order_type == "LIMIT_MAKER":
        price = _coerce_float(request.get("price"))
        if price is None:
            return (False, "price_missing")
        if price <= 0.0:
            return (False, f"price_non_positive:{price:.6f}")
        if tif_raw is not None:
            return (False, "limit_maker_takes_no_time_in_force")
    else:  # MARKET
        if tif_raw is not None:
            return (False, "market_takes_no_time_in_force")
    return (True, "")


def build_spot_order_wire(
    request: Mapping[str, Any],
    policy: BinanceSpotAdapterPolicy = DEFAULT_BINANCE_SPOT_ADAPTER_POLICY,
) -> Dict[str, Any]:
    """Coerce a validated runner request into Binance ``/api/v3/order``
    wire params (pure; unsigned — signing is the transport's job).

    Enforces the E8 TIF matrix: LIMIT keeps its ``timeInForce``
    (defaulting to ``policy.default_tif``); LIMIT_MAKER and MARKET
    carry none.
    """
    is_valid, reason = validate_spot_intent(request, policy)
    if not is_valid:
        raise ValueError(f"build_spot_order_wire: invalid intent: {reason}")
    order_type = (_coerce_str(request.get("order_type")) or "LIMIT").upper()
    wire: Dict[str, Any] = {
        _CLIENT_ORDER_ID_KEY: str(request.get("client_order_id")),
        _SYMBOL_KEY: _coerce_str(request.get("symbol")),
        _SIDE_KEY: (_coerce_str(request.get("side")) or "").upper(),
        _TYPE_KEY: order_type,
        _NEW_ORDER_RESP_TYPE_KEY: (
            request.get("newOrderRespType") or "RESULT"
        ),
    }
    qty = _coerce_float(request.get("qty"))
    if qty is not None and abs(qty) >= 1e-9:
        wire[_QTY_KEY] = abs(qty)
    quote_qty = _coerce_float(request.get("quote_order_qty"))
    if quote_qty is not None and order_type == "MARKET":
        wire[_QUOTE_ORDER_QTY_KEY] = quote_qty
    if order_type in ("LIMIT", "LIMIT_MAKER"):
        wire[_PRICE_KEY] = _coerce_float(request.get("price"))
    if order_type == "LIMIT":
        wire[_TIME_IN_FORCE_KEY] = (
            (_coerce_str(request.get("time_in_force"))
             or policy.default_tif).upper()
        )
    return wire


def _extract_filled_qty_from_ack(ack: Mapping[str, Any]) -> Tuple[float, str]:
    """Pick the most-specific Binance fill-qty key from an ack.

    Priority: ``executedQty`` > ``cumQty`` > ``filledQty`` >
    ``origQty``.  Never invents a fill.
    """
    for key in ("executedQty", "cumQty", "filledQty", "origQty"):
        coerced = _coerce_float(ack.get(key))
        if coerced is not None:
            return float(coerced), key
    return 0.0, "absent"


def _avg_price_from_ack(ack: Mapping[str, Any]) -> Optional[float]:
    """Spot ``RESULT`` acks report fills in a ``fills`` array; derive
    the volume-weighted average price from it when ``avgPrice`` is
    absent."""
    direct = _coerce_float(ack.get("avgPrice"))
    if direct is not None and direct > 0.0:
        return direct
    fills = ack.get("fills")
    if isinstance(fills, (list, tuple)) and fills:
        num = 0.0
        den = 0.0
        for f in fills:
            if not isinstance(f, Mapping):
                continue
            p = _coerce_float(f.get("price"))
            q = _coerce_float(f.get("qty"))
            if p is None or q is None:
                continue
            num += p * q
            den += q
        if den > 0.0:
            return num / den
    return None


def _commission_from_ack(ack: Mapping[str, Any]) -> Optional[float]:
    direct = _coerce_float(ack.get("commission"))
    if direct is not None:
        return direct
    fills = ack.get("fills")
    if isinstance(fills, (list, tuple)) and fills:
        total = 0.0
        seen = False
        for f in fills:
            if not isinstance(f, Mapping):
                continue
            c = _coerce_float(f.get("commission"))
            if c is not None:
                total += c
                seen = True
        if seen:
            return total
    return None


def classify_binance_spot_rest_ack(
    ack: Mapping[str, Any],
) -> Tuple[BinanceSpotStatus, float, Optional[float], Optional[float],
           Optional[str], Optional[str], Optional[str]]:
    """Classify a Binance spot ``POST /api/v3/order`` response.

    Success shape (``newOrderRespType=RESULT``)::

        {"symbol": "BTCUSDT", "orderId": 28, "clientOrderId": "abc",
         "transactTime": 1507725176595, "price": "0.000000",
         "origQty": "10.0", "executedQty": "10.0",
         "status": "FILLED", "timeInForce": "GTC", "type": "MARKET",
         "side": "SELL",
         "fills": [{"price": "4000.0", "qty": "10.0",
                    "commission": "0.01", "commissionAsset": "BTC"}]}

    Failure shape::

        {"code": -2010, "msg": "Account has insufficient balance ..."}

    Returns ``(status, filled_qty, avg_price, commission,
    venue_order_id, reject_reason, error_code)``.  ``filled_qty`` is
    signed per the module convention (ack ``side`` aware).
    """
    raw_status = str(ack.get("status") or "").strip().upper()
    raw_code = ack.get("code")
    error_code: Optional[str] = None
    if raw_code is not None:
        error_code = str(raw_code)
    fill_qty, _fill_source = _extract_filled_qty_from_ack(ack)
    avg_price = _avg_price_from_ack(ack)
    commission = _commission_from_ack(ack)
    venue_order_id = _coerce_str(ack.get("orderId"))
    if venue_order_id is not None:
        try:
            venue_order_id = str(int(venue_order_id))
        except (TypeError, ValueError):
            venue_order_id = str(venue_order_id)

    def _code_reason() -> str:
        try:
            code_int = int(raw_code)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "OTHER"
        return {
            -2010: "INSUFFICIENT_BALANCE",
            -1013: "FILTER_FAILURE",
            -1021: "TIMESTAMP_OUTSIDE_RECVWINDOW",
            -1022: "INVALID_SIGNATURE",
            -1003: "RATE_LIMITED",
            -1100: "ILLEGAL_CHARS",
            -2014: "INVALID_API_KEY",
            -2015: "INVALID_API_KEY",
        }.get(code_int, "OTHER")

    if raw_code is not None and not raw_status:
        return (
            BinanceSpotStatus.REJECTED,
            float(fill_qty),
            avg_price,
            commission,
            venue_order_id,
            _code_reason(),
            error_code,
        )
    if not raw_status:
        msg = _coerce_str(ack.get("msg"))
        reject_reason = "OTHER" if not msg else msg[:64]
        return (
            BinanceSpotStatus.REJECTED,
            float(fill_qty),
            avg_price,
            commission,
            venue_order_id,
            reject_reason,
            error_code,
        )

    # PENDING_CANCEL is a transient, not an outcome: map to the
    # adapter's CANCELED lifecycle only when the venue confirms;
    # otherwise treat unknown-but-nonterminal statuses conservatively.
    if raw_status == _VENUE_STATUS_PENDING_CANCEL:
        status = BinanceSpotStatus.CANCELED
    elif raw_status == _VENUE_STATUS_EXPIRED_IN_MATCH:
        # post-only (LIMIT_MAKER) order that would have crossed: the
        # venue kills it with zero fill — an EXPIRED outcome.
        status = BinanceSpotStatus.EXPIRED
    else:
        try:
            status = BinanceSpotStatus.from_raw(raw_status)
        except ValueError:
            reject_reason = f"OTHER:unknown_status:{raw_status}"
            return (
                BinanceSpotStatus.REJECTED,
                float(fill_qty),
                avg_price,
                commission,
                venue_order_id,
                reject_reason,
                error_code,
            )

    side = _coerce_str(ack.get("side"))
    if side is not None:
        if side.upper() == "SELL":
            fill_qty = -abs(fill_qty)
        else:
            fill_qty = abs(fill_qty)
    # An explicit error code alongside a status (Binance sometimes
    # returns {"code": ..., "status": "REJECTED"} on order reject)
    # still classifies its reason from the code.
    final_reason: Optional[str] = None
    if raw_code is not None:
        final_reason = _code_reason()
    return (
        status,
        float(fill_qty),
        avg_price,
        commission,
        venue_order_id,
        final_reason,
        error_code,
    )


# ---------------------------------------------------------------------------
# Immutable views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BinanceSpotState:
    """Live view of a spot intent (``PENDING`` / terminal status)."""

    client_order_id: str
    symbol: Optional[str]
    side: Optional[str]
    intended_qty: float
    price: Optional[float]
    venue: Optional[str]
    venue_order_id: Optional[str]
    binance_spot_signature: str
    status: BinanceSpotStatus
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
            "binance_spot_signature": self.binance_spot_signature,
            "status": self.status.value,
            "ts_ns": self.ts_ns,
            "updated_ts_ns": self.updated_ts_ns,
            "policy_fingerprint": self.policy_fingerprint,
        }


@dataclass(frozen=True)
class BinanceSpotSnapshot:
    """Aggregate snapshot across intents."""

    intents: Tuple[BinanceSpotState, ...]
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


def _empty_result() -> "ComponentResult":
    return ComponentResult(observation=None)


# ---------------------------------------------------------------------------
# Adapter component
# ---------------------------------------------------------------------------


class BinanceSpotAdapter:
    """Runtime Binance spot adapter.

    Wire into the runner via ``runner.register(adapter)`` +
    ``runner.register_on_fill(adapter)`` (same hook names as the
    perp sibling), or drive ``on_request`` / ``on_fill`` /
    ``record_reject`` directly.  The constructor installs the
    additive ``binance_spot_*`` tables on the journal (idempotent).
    """

    def __init__(
        self,
        *,
        journal: Any,
        policy: BinanceSpotAdapterPolicy = DEFAULT_BINANCE_SPOT_ADAPTER_POLICY,
        auto_recover: bool = True,
    ) -> None:
        self._journal = journal
        self.policy = policy
        self._fp: str = policy_fingerprint(policy)
        self._venue = str(policy.venue)
        self._default_tif = str(policy.default_tif)
        self._block_on_invalid = bool(policy.block_on_invalid)

        self._insert_intent_sql = (
            "INSERT INTO binance_spot_intents ("
            "client_order_id, ts_first_seen_ns, ts_last_seen_ns, "
            "symbol, side, qty, price, order_type, time_in_force, "
            "quote_order_qty, venue, binance_spot_signature, "
            "venue_order_id, status, updated_ts_ns, "
            "policy_fingerprint, payload"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?)"
            " ON CONFLICT(client_order_id) DO UPDATE SET "
            "ts_last_seen_ns = excluded.ts_last_seen_ns, "
            "venue_order_id = COALESCE(excluded.venue_order_id, "
            "binance_spot_intents.venue_order_id), "
            "status = excluded.status, "
            "updated_ts_ns = excluded.updated_ts_ns, "
            "payload = excluded.payload"
        )
        self._insert_event_sql = (
            "INSERT INTO binance_spot_events ("
            "ts_ns, client_order_id, source, kind, venue_order_id, "
            "raw_payload, policy_fingerprint"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        self._upsert_ack_sql = (
            "INSERT INTO binance_spot_acks ("
            "client_order_id, ts_ns, symbol, side, intended_qty, "
            "price, venue, venue_order_id, status, filled_qty, "
            "avg_price, commission, reject_reason, error_code, "
            "source, fill_qty_source, policy_fingerprint, payload"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?)"
            " ON CONFLICT(client_order_id) DO UPDATE SET "
            "ts_ns = excluded.ts_ns, "
            "venue_order_id = COALESCE(excluded.venue_order_id, "
            "binance_spot_acks.venue_order_id), "
            "status = excluded.status, "
            "filled_qty = excluded.filled_qty, "
            "avg_price = COALESCE(excluded.avg_price, "
            "binance_spot_acks.avg_price), "
            "commission = COALESCE(excluded.commission, "
            "binance_spot_acks.commission), "
            "reject_reason = excluded.reject_reason, "
            "error_code = excluded.error_code, "
            "source = excluded.source, "
            "fill_qty_source = excluded.fill_qty_source, "
            "payload = excluded.payload"
        )
        bootstrap_journal(journal)
        self._intents: Dict[str, BinanceSpotState] = {}
        if auto_recover:
            self._recover_from_intents()

    # -- public reads ---------------------------------------------------

    @property
    def policy_fingerprint(self) -> str:
        return self._fp

    @property
    def journal(self) -> Any:
        return self._journal

    def get(self, client_order_id: str) -> Optional[BinanceSpotState]:
        return self._intents.get(client_order_id)

    def snapshot(self) -> BinanceSpotSnapshot:
        states = tuple(self._intents.values())

        def _count(s: BinanceSpotStatus) -> int:
            return sum(1 for st in states if st.status == s)

        return BinanceSpotSnapshot(
            intents=states,
            n_pending=_count(BinanceSpotStatus.PENDING),
            n_submitted=_count(BinanceSpotStatus.SUBMITTED),
            n_partially_filled=_count(BinanceSpotStatus.PARTIALLY_FILLED),
            n_filled=_count(BinanceSpotStatus.FILLED),
            n_canceled=_count(BinanceSpotStatus.CANCELED),
            n_expired=_count(BinanceSpotStatus.EXPIRED),
            n_rejected=_count(BinanceSpotStatus.REJECTED),
            n_blocked=_count(BinanceSpotStatus.BLOCKED),
        )

    def recover(self) -> BinanceSpotSnapshot:
        """Re-populate the in-memory cache from
        ``binance_spot_intents`` (cold-start path)."""
        self._recover_from_intents()
        return self.snapshot()

    def _recover_from_intents(self) -> None:
        rows = list(self._journal.conn.execute(
            "SELECT client_order_id, ts_first_seen_ns, symbol, "
            "side, qty, price, venue, venue_order_id, "
            "binance_spot_signature, status, policy_fingerprint "
            "FROM binance_spot_intents"
        ))
        for r in rows:
            d = dict(r)
            try:
                status = BinanceSpotStatus.from_raw(d["status"])
            except ValueError:
                continue
            self._intents[d["client_order_id"]] = BinanceSpotState(
                client_order_id=d["client_order_id"],
                symbol=d.get("symbol"),
                side=d.get("side"),
                intended_qty=float(d.get("qty") or 0.0),
                price=d.get("price"),
                venue=d.get("venue"),
                venue_order_id=d.get("venue_order_id"),
                binance_spot_signature=d.get(
                    "binance_spot_signature", "",
                ) or "",
                status=status,
                ts_ns=int(d["ts_first_seen_ns"] or 0),
                updated_ts_ns=int(d["ts_first_seen_ns"] or 0),
                policy_fingerprint=d.get("policy_fingerprint", "") or "",
            )

    # -- runner hooks -----------------------------------------------------

    def on_request(
        self,
        request: Mapping[str, Any],
        journal: Any,
        ts_ns: int,
    ) -> "ComponentResult":
        """Runner pre-trade hook (passthrough non-spot intents)."""
        venue_raw = _coerce_str(request.get("venue"))
        opt_in = _coerce_bool(request.get("binance_spot"))
        if not (venue_raw == self._venue or opt_in):
            return _empty_result()

        is_valid, reason = validate_spot_intent(request, self.policy)
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
                                "request_keys": sorted(request.keys()),
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
                    component="binance_spot_adapter",
                    reason=reason,
                    severity="WARN",
                )
                return ComponentResult(block=block, observation={
                    "binance_spot_adapter": "validation_failed",
                    "reason": reason,
                })
            return ComponentResult(observation={
                "binance_spot_adapter": "validation_failed",
                "reason": reason,
            })

        coid = str(request.get("client_order_id") or "")
        symbol = _coerce_str(request.get("symbol"))
        side_u = (_coerce_str(request.get("side")) or "").upper()
        qty = _coerce_float(request.get("qty")) or 0.0
        price = _coerce_float(request.get("price"))
        order_type_u = (
            _coerce_str(request.get("order_type")) or "LIMIT"
        ).upper()
        time_in_force: Optional[str] = None
        if order_type_u == "LIMIT":
            time_in_force = (
                _coerce_str(request.get("time_in_force"))
                or self._default_tif
            ).upper()
        quote_qty = _coerce_float(request.get("quote_order_qty"))

        signature = _intent_fingerprint(
            client_order_id=coid,
            symbol=symbol,
            side=side_u,
            intended_qty=qty,
            price=price,
            order_type=order_type_u,
            time_in_force=time_in_force,
            venue=self._venue,
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
                    time_in_force,
                    quote_qty,
                    self._venue,
                    signature,
                    None,
                    BinanceSpotStatus.PENDING.value,
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
                                "time_in_force": time_in_force,
                                "quote_order_qty": quote_qty,
                            }
                        },
                        default=str,
                    ),
                    self._fp,
                ),
            )
        self._journal.conn.commit()
        self._intents[coid] = BinanceSpotState(
            client_order_id=coid,
            symbol=symbol,
            side=side_u,
            intended_qty=signed_qty,
            price=price,
            venue=self._venue,
            venue_order_id=None,
            binance_spot_signature=signature,
            status=BinanceSpotStatus.PENDING,
            ts_ns=int(ts_ns),
            updated_ts_ns=int(ts_ns),
            policy_fingerprint=self._fp,
        )
        return ComponentResult(observation={
            "binance_spot_adapter": "tagged",
            "binance_spot_signature": signature,
            "venue": self._venue,
            "binance_spot_intent_id": coid,
        })

    def on_fill(
        self,
        request: Mapping[str, Any],
        ack: Mapping[str, Any],
        journal: Any,
        ts_ns: int,
    ) -> "ComponentResult":
        """Runner post-fill hook: classify the REST ack and journal it.

        Duplicate callbacks against a terminal outcome are no-ops
        (the projection never regresses).
        """
        coid = _coerce_str(ack.get("clientOrderId")) or _coerce_str(
            request.get("client_order_id"),
        )
        if not coid:
            return _empty_result()
        existing = self._intents.get(coid)
        if existing is None:
            # Unknown coid with no spot marker: not ours.
            venue_ack = _coerce_str(ack.get("venue") or ack.get("symbol"))
            if venue_ack is None:
                return _empty_result()
        (status, filled_qty, avg_price, commission, venue_order_id,
         reject_reason, error_code) = classify_binance_spot_rest_ack(ack)

        if existing is not None:
            if existing.status in _TERMINAL_STATUSES:
                return ComponentResult(observation={
                    "binance_spot_adapter": "duplicate_callback",
                })
            if (status == BinanceSpotStatus.PARTIALLY_FILLED
                    and existing.status
                    == BinanceSpotStatus.PARTIALLY_FILLED):
                return ComponentResult(observation={
                    "binance_spot_adapter": "duplicate_callback",
                })

        fill_qty_source = _extract_filled_qty_from_ack(ack)[1]
        order_type_u = (
            _coerce_str(request.get("order_type")) or "LIMIT"
        ).upper()
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
                    order_type_u,
                    None,
                    None,
                    self._venue,
                    existing.binance_spot_signature if existing else "",
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
                    BinanceSpotAckSource.REST.value,
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
                        == BinanceSpotStatus.REJECTED
                        else T_ACK_PARTIAL if status
                        == BinanceSpotStatus.PARTIALLY_FILLED
                        else T_ACK_OK
                    ),
                    venue_order_id,
                    json.dumps(
                        {
                            "status": status.value,
                            "filled_qty": filled_qty,
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
            self._intents[coid] = BinanceSpotState(
                client_order_id=existing.client_order_id,
                symbol=existing.symbol,
                side=existing.side,
                intended_qty=existing.intended_qty,
                price=existing.price,
                venue=existing.venue,
                venue_order_id=venue_order_id or existing.venue_order_id,
                binance_spot_signature=existing.binance_spot_signature,
                status=status,
                ts_ns=existing.ts_ns,
                updated_ts_ns=int(ts_ns),
                policy_fingerprint=existing.policy_fingerprint,
            )
        return ComponentResult(observation={
            "binance_spot_adapter": "ack_classified",
            "binance_spot_status": status.value,
            "binance_spot_filled_qty": filled_qty,
        })

    def record_reject(
        self,
        *,
        request: Mapping[str, Any],
        ack: Mapping[str, Any],
        ts_ns: Optional[int] = None,
    ) -> Optional[BinanceSpotState]:
        """Explicit reject hook (runner reject path).  Journals a
        terminal ``REJECTED`` outcome; no-op for non-spot intents."""
        coid = _coerce_str(request.get("client_order_id"))
        venue_raw = _coerce_str(request.get("venue"))
        opt_in = _coerce_bool(request.get("binance_spot"))
        if not coid or not (venue_raw == self._venue or opt_in):
            return None
        now_ns = int(ts_ns) if ts_ns is not None else int(time.time_ns())
        (_status, filled_qty, avg_price, commission, venue_order_id,
         reject_reason, error_code) = classify_binance_spot_rest_ack(ack)
        order_type_u = (
            _coerce_str(request.get("order_type")) or "LIMIT"
        ).upper()
        tif: Optional[str] = None
        if order_type_u == "LIMIT":
            tif = (
                _coerce_str(request.get("time_in_force"))
                or self._default_tif
            ).upper()
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                self._insert_intent_sql,
                (
                    coid,
                    now_ns,
                    now_ns,
                    _coerce_str(request.get("symbol")),
                    (_coerce_str(request.get("side")) or "").upper(),
                    float(request.get("qty") or 0.0),
                    _coerce_float(request.get("price")),
                    order_type_u,
                    tif,
                    _coerce_float(request.get("quote_order_qty")),
                    self._venue,
                    "",
                    venue_order_id,
                    BinanceSpotStatus.REJECTED.value,
                    now_ns,
                    self._fp,
                    json.dumps({"src": "record_reject"}, default=str),
                ),
            )
            cur.execute(
                self._upsert_ack_sql,
                (
                    coid,
                    now_ns,
                    _coerce_str(request.get("symbol")),
                    (_coerce_str(request.get("side")) or "").upper(),
                    float(request.get("qty") or 0.0),
                    _coerce_float(request.get("price")),
                    self._venue,
                    venue_order_id,
                    BinanceSpotStatus.REJECTED.value,
                    float(filled_qty),
                    avg_price,
                    commission,
                    reject_reason,
                    error_code,
                    BinanceSpotAckSource.REST.value,
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
        state = BinanceSpotState(
            client_order_id=coid,
            symbol=_coerce_str(request.get("symbol")),
            side=(_coerce_str(request.get("side")) or "").upper() or None,
            intended_qty=float(request.get("qty") or 0.0),
            price=_coerce_float(request.get("price")),
            venue=self._venue,
            venue_order_id=venue_order_id,
            binance_spot_signature="",
            status=BinanceSpotStatus.REJECTED,
            ts_ns=now_ns,
            updated_ts_ns=now_ns,
            policy_fingerprint=self._fp,
        )
        self._intents[coid] = state
        return state


# ---------------------------------------------------------------------------
# Outbound transports (wire + paper)
# ---------------------------------------------------------------------------


@dataclass
class OutboundBinanceSpotTransport:
    """Wire-level transport for ``POST /api/v3/order``.

    Coerces the runner's internal request into Binance wire keys via
    :func:`build_spot_order_wire`, signs when ``api_secret`` is set,
    and delegates to ``callable_send``.  Does NOT journal — the
    runner's hot path owns journaling (the adapter's ``on_fill``
    classifies the response).
    """

    callable_send: Any = field(
        default=lambda req: {
            "ok": True,
            "status": "NEW",
            "clientOrderId": req.get("newClientOrderId"),
            "orderId": 0,
            "executedQty": "0",
            "side": req.get("side"),
            "symbol": req.get("symbol"),
            "type": req.get("type", "LIMIT"),
            "timeInForce": req.get("timeInForce"),
        },
    )
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    recv_window_ms: int = 5000
    policy: BinanceSpotAdapterPolicy = DEFAULT_BINANCE_SPOT_ADAPTER_POLICY

    def __call__(self, request: dict) -> dict:
        wire = build_spot_order_wire(request, self.policy)
        if self.api_secret:
            wire = sign_binance_spot_request(
                wire,
                api_secret=self.api_secret,
                recv_window_ms=self.recv_window_ms,
            )
        if self.api_key:
            wire["X-MBX-APIKEY"] = self.api_key
        return self.callable_send(wire)


@dataclass
class BinanceSpotPaperTransport:
    """Paper-trading transport modelling spot TIF semantics (E8).

    Deterministic mock acks; no network, no journaling.  The
    :class:`FillModel` controls the outcome per coid:

    * GTC LIMIT / LIMIT_MAKER -> ``NEW`` (resting) by default;
    * IOC -> ``FILLED`` when the model can fill everything, else
      ``EXPIRED`` with the partial ``executedQty`` (the remainder is
      cancelled by the venue);
    * FOK -> ``FILLED`` in full or ``EXPIRED`` with
      ``executedQty=0`` (all-or-nothing);
    * LIMIT_MAKER that would cross -> reject ``-2010``
      (``EXPIRED_IN_MATCH`` when the venue reports it as a status);
    * MARKET -> ``FILLED`` at the model price.
    """

    @dataclass
    class FillModel:
        status: Optional[str] = None       # explicit override
        filled_qty: Optional[float] = None
        avg_price: Optional[float] = None
        commission: Optional[float] = None
        order_id: Optional[int] = None
        reject_code: Optional[int] = None
        reject_message: Optional[str] = None

    default_fill: "BinanceSpotPaperTransport.FillModel" = field(
        default_factory=lambda: BinanceSpotPaperTransport.FillModel(),
    )
    fill_model: Dict[str, "BinanceSpotPaperTransport.FillModel"] = field(
        default_factory=dict,
    )
    n_calls: int = 0
    calls: List[Dict[str, Any]] = field(default_factory=list)

    def _coid(self, request: dict) -> str:
        return str(
            request.get("client_order_id")
            or request.get("newClientOrderId")
            or request.get("clientOrderId")
            or "",
        )

    def __call__(self, request: dict) -> dict:
        self.n_calls += 1
        self.calls.append(dict(request))
        coid = self._coid(request)
        model = self.fill_model.get(coid, self.default_fill)
        symbol = request.get("symbol") or "BTCUSDT"
        side = (request.get("side") or "BUY").upper()
        intended_qty = float(
            request.get("qty") or request.get("quantity") or 0.0,
        )
        price = float(
            request.get("price") or request.get("expected_price") or 0.0,
        )
        order_type = (
            request.get("order_type") or request.get("type") or "LIMIT"
        ).upper()
        tif = (
            request.get("time_in_force") or request.get("timeInForce")
        )
        tif_u = tif.upper() if isinstance(tif, str) and tif else None
        order_id = (
            model.order_id
            if model.order_id is not None
            else 100000 + self.n_calls
        )
        avg_price = (
            model.avg_price if model.avg_price is not None else price
        )

        def _ack(status: str, filled: float) -> dict:
            commission = (
                model.commission if model.commission is not None else 0.0
            )
            body: Dict[str, Any] = {
                "ok": True,
                "status": status,
                "clientOrderId": coid,
                "orderId": order_id,
                "symbol": symbol,
                "side": side,
                "price": str(price),
                "origQty": str(intended_qty),
                "executedQty": str(filled),
                "cumQty": str(filled),
                "type": order_type,
                "timeInForce": tif_u,
                "venue": "binance_spot_paper",
                "transactTime": 0,
            }
            if filled > 0.0 and avg_price > 0.0:
                body["fills"] = [{
                    "price": str(avg_price),
                    "qty": str(filled),
                    "commission": str(commission),
                    "commissionAsset": "USDT",
                }]
            return body

        if model.reject_code is not None:
            return {
                "status": "rejected",
                "clientOrderId": coid,
                "code": model.reject_code,
                "msg": model.reject_message or "OTHER",
                "venue": "binance_spot_paper",
                "symbol": symbol,
                "side": side,
            }

        if model.status is not None:
            filled = (
                model.filled_qty
                if model.filled_qty is not None
                else intended_qty
            )
            return _ack(model.status, filled)

        # TIF-aware default semantics (E8).
        if order_type == "MARKET":
            return _ack("FILLED", intended_qty)
        if tif_u == "IOC":
            available = (
                model.filled_qty
                if model.filled_qty is not None
                else intended_qty
            )
            if available >= intended_qty:
                return _ack("FILLED", intended_qty)
            return _ack("EXPIRED", max(0.0, available))
        if tif_u == "FOK":
            available = (
                model.filled_qty
                if model.filled_qty is not None
                else intended_qty
            )
            if available >= intended_qty:
                return _ack("FILLED", intended_qty)
            return _ack("EXPIRED", 0.0)
        # GTC LIMIT and LIMIT_MAKER rest on the book.
        return _ack("NEW", 0.0)


# Module-level alias for the nested FillModel (mirrors the perp
# sibling's BinancePerpPaperTransportFillModel alias).
BinanceSpotPaperTransportFillModel = BinanceSpotPaperTransport.FillModel


__all__ = [
    "BinanceSpotAdapter",
    "BinanceSpotAdapterPolicy",
    "BinanceSpotAckSource",
    "BinanceSpotPaperTransport",
    "BinanceSpotPaperTransportFillModel",
    "BinanceSpotSnapshot",
    "BinanceSpotState",
    "BinanceSpotStatus",
    "DEFAULT_BINANCE_SPOT_ADAPTER_POLICY",
    "DEFAULT_VENUE",
    "OutboundBinanceSpotTransport",
    "SCHEMA_SQL",
    "SPOT_ORDER_PATH",
    "SPOT_REST_BASE",
    "T_ACK_OK",
    "T_ACK_PARTIAL",
    "T_ACK_REJECT",
    "T_BLOCKED",
    "T_INTENT_TAGGED",
    "T_VALIDATION_FAILED",
    "bootstrap_journal",
    "build_spot_order_wire",
    "classify_binance_spot_rest_ack",
    "policy_fingerprint",
    "sign_binance_spot_request",
    "validate_spot_intent",
]
