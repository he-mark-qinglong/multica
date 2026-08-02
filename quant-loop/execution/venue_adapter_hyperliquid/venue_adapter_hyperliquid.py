"""venue_adapter_hyperliquid — Hyperliquid DEX perp venue adapter (E4).

Rebuild of ``execution/venue_adapter_hyperliquid_p7exec_007`` whose
source was lost (only ``__pycache__`` remains).  The structure,
sign convention, journaling discipline, and duck-typed runner
interop mirror
:mod:`execution.venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp`
and its spot sibling; read those first.

The adapter sits between the execution runner and the Hyperliquid
exchange API (REST ``POST /exchange`` + user-data WebSocket) and
owns:

* **EIP-712 signing abstraction** — the adapter never holds a
  private key.  Signing is an injected callback
  (:data:`SignerFn`) that receives the canonical signable payload
  built by :func:`build_eip712_signable` and returns a
  :class:`SignedEnvelope` (``r`` / ``s`` / ``v``).  The exact
  EIP-712 encoding lives in the signer implementation; this
  module only fixes the payload shape (action + nonce + vault).
* **action.order construction** (:func:`build_order_action`) —
  the ``{"type": "order", "orders": [...], "grouping": "na"}``
  payload with per-order ``a`` (asset index), ``b`` (is_buy),
  ``p`` / ``s`` (price / size strings), ``r`` (reduce_only),
  ``t`` (``{"limit": {"tif": ...}}`` with TIF in
  {``Gtc``, ``Ioc``, ``Alo``}), and ``c`` (cloid).
* **cloid discipline** — a cloid is a 128-bit client order id
  rendered as ``0x`` + 32 lowercase hex chars
  (:func:`is_valid_cloid`, :func:`normalize_cloid`);
  :func:`derive_cloid` deterministically derives one from a
  runner ``client_order_id`` via SHA-256's first 16 bytes so a
  resubmitted intent reuses the same cloid (venue-side
  idempotency).
* **WS parsing** (:func:`parse_ws_message`) — ``orderUpdates``
  and ``userFills`` channels into typed dicts;
  subscription / pong / error frames are classified too.
* **ack classification** (:func:`classify_exchange_response`) —
  the ``/exchange`` response envelope
  (``{"status": "ok", "response": {"type": "order", "data":
  {"statuses": [...]}}}``) into the adapter lifecycle, with
  reason codes :data:`MARGIN_REJECTED`, :data:`RATE_LIMITED`,
  :data:`POST_ONLY_WOULD_CROSS`, :data:`IOC_NO_MATCH`,
  :data:`OTHER`.
* **journaling** — additive ``hyperliquid_intents`` /
  ``hyperliquid_events`` / ``hyperliquid_acks`` tables (UPSERT on
  ``client_order_id``), cold-start recoverable.
* **paper transport** (:class:`HyperliquidPaperTransport`) —
  deterministic in-memory ``/exchange`` responder for tests and
  cold-start smoke runs; never touches the network.

Differences from the Binance perp sibling
------------------------------------------
* Perp symbols are plain coin names (``BTC``, ``ETH``, ``AVAX``)
  — no quote suffix rule.  Orders address the coin by *asset
  index* (``a``); the intent may carry ``asset`` directly or the
  adapter resolves it via the injected ``asset_index`` map.
* Signing is EIP-712 over the action payload, not HMAC over a
  query string; there is no ``recvWindow`` / ``timestamp`` param.
* Post-only is TIF ``Alo`` ("Add Liquidity Only") — the analog of
  Binance's GTX / spot LIMIT_MAKER.

Sign convention: ``intended_qty`` is signed (+ BUY / − SELL);
``filled_qty`` from an ack follows the same convention via the
ack's ``side`` (``"B"`` / ``"A"`` on the wire — HL uses ``A`` for
ask/sell).  ``commission`` is the venue-reported ``fee`` (a
positive cost).

References
----------
- Hyperliquid docs — ``POST /exchange`` (order action), EIP-712
  signing (phantom agent), WS ``orderUpdates`` / ``userFills``
  channels, error strings.
- Binance perp sibling (structure mirrored):
  ``execution/venue_adapter_binance_perp_p7exec_003``.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Callable,
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

DEFAULT_VENUE = "hyperliquid"

# REST endpoints (documented for the transport; the adapter itself
# performs no I/O).
HL_EXCHANGE_PATH = "/exchange"
HL_REST_MAINNET = "https://api.hyperliquid.xyz"
HL_REST_TESTNET = "https://api.hyperliquid-testnet.xyz"
HL_WS_MAINNET = "wss://api.hyperliquid.xyz/ws"
HL_WS_TESTNET = "wss://api.hyperliquid-testnet.xyz/ws"

# Hyperliquid time-in-force set (limit orders only).
_HL_TIME_IN_FORCE = frozenset({"Gtc", "Ioc", "Alo"})

# Ack reject-reason taxonomy.
MARGIN_REJECTED = "MARGIN_REJECTED"
RATE_LIMITED = "RATE_LIMITED"
POST_ONLY_WOULD_CROSS = "POST_ONLY_WOULD_CROSS"
IOC_NO_MATCH = "IOC_NO_MATCH"
PRICE_BAND = "PRICE_BAND"
OTHER = "OTHER"

# Transition labels for hyperliquid_events.kind.
T_INTENT_TAGGED = "INTENT_TAGGED"
T_ACK_OK = "ACK_OK"
T_ACK_PARTIAL = "ACK_PARTIAL"
T_ACK_REJECT = "ACK_REJECT"
T_WS_UPDATE = "WS_UPDATE"
T_BLOCKED = "BLOCKED"
T_VALIDATION_FAILED = "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class HyperliquidStatus(str, Enum):
    """Lifecycle status of a hyperliquid-targeted intent.

    ``PENDING``   — tagged, no venue ack yet (in-flight).
    ``OPEN``      — resting on the book.
    ``FILLED``    — full qty reported (terminal).
    ``CANCELED``  — venue / runner cancelled (terminal).
    ``EXPIRED``   — IOC no-match / post-only would-cross (terminal).
    ``REJECTED``  — venue refused (terminal).
    ``BLOCKED``   — runner-side pre-trade block (terminal).
    """

    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"

    @classmethod
    def from_raw(cls, value: object) -> "HyperliquidStatus":
        s = str(value or "").upper().strip()
        for member in cls:
            if member.value == s:
                return member
        raise ValueError(f"unknown hyperliquid status: {value!r}")


_TERMINAL_STATUSES = frozenset({
    HyperliquidStatus.FILLED,
    HyperliquidStatus.CANCELED,
    HyperliquidStatus.EXPIRED,
    HyperliquidStatus.REJECTED,
    HyperliquidStatus.BLOCKED,
})


class HyperliquidAckSource(str, Enum):
    """Where a ``hyperliquid_acks`` row came from."""

    REST = "rest"
    WSS = "wss"


# ---------------------------------------------------------------------------
# Schema (additive)
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Additive: venue_adapter_hyperliquid. Per-intent projection,
-- UPSERT on client_order_id.  ``cloid`` is the venue-side
-- idempotency key (0x + 32 hex); ``hyperliquid_signature`` is the
-- deterministic SHA-256 of the canonical intent tuple at tag time.
CREATE TABLE IF NOT EXISTS hyperliquid_intents (
    client_order_id TEXT PRIMARY KEY,
    ts_first_seen_ns INTEGER NOT NULL,
    ts_last_seen_ns INTEGER NOT NULL,
    coin TEXT NOT NULL,
    asset INTEGER,
    side TEXT NOT NULL,                     -- 'BUY' | 'SELL'
    qty REAL NOT NULL,                      -- signed target qty
    price REAL,                             -- limit price; NULL for MARKET-like IOC
    order_type TEXT NOT NULL DEFAULT 'LIMIT',
    time_in_force TEXT NOT NULL DEFAULT 'Gtc',
    reduce_only INTEGER NOT NULL DEFAULT 0,
    cloid TEXT,
    venue TEXT NOT NULL DEFAULT 'hyperliquid',
    hyperliquid_signature TEXT NOT NULL DEFAULT '',
    venue_order_id TEXT,                    -- HL oid, set on first ack
    status TEXT NOT NULL,                   -- see HyperliquidStatus
    updated_ts_ns INTEGER NOT NULL,
    policy_fingerprint TEXT NOT NULL DEFAULT '',
    payload TEXT
);
CREATE INDEX IF NOT EXISTS ix_hli_ts ON hyperliquid_intents(ts_first_seen_ns);
CREATE INDEX IF NOT EXISTS ix_hli_status ON hyperliquid_intents(status);
CREATE INDEX IF NOT EXISTS ix_hli_coin ON hyperliquid_intents(coin);
CREATE INDEX IF NOT EXISTS ix_hli_cloid ON hyperliquid_intents(cloid);
CREATE INDEX IF NOT EXISTS ix_hli_venue_oid ON hyperliquid_intents(venue_order_id);

-- Additive: append-only event log of every hyperliquid event
-- (REST submit, REST ack, WS update, validation failure).
CREATE TABLE IF NOT EXISTS hyperliquid_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns INTEGER NOT NULL,
    client_order_id TEXT NOT NULL,
    source TEXT NOT NULL,                   -- 'rest_submit' | 'rest_ack' | 'wss_userdata'
    kind TEXT NOT NULL,
    venue_order_id TEXT,
    raw_payload TEXT,
    policy_fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_hle_coid ON hyperliquid_events(client_order_id);
CREATE INDEX IF NOT EXISTS ix_hle_ts ON hyperliquid_events(ts_ns);
CREATE INDEX IF NOT EXISTS ix_hle_kind ON hyperliquid_events(kind);

-- Additive: per-coid outcome projection, UPSERT on
-- client_order_id.  Terminal outcomes never regress.
CREATE TABLE IF NOT EXISTS hyperliquid_acks (
    client_order_id TEXT PRIMARY KEY,
    ts_ns INTEGER NOT NULL,
    coin TEXT,
    side TEXT,
    intended_qty REAL NOT NULL DEFAULT 0.0,
    price REAL,
    venue TEXT,
    cloid TEXT,
    venue_order_id TEXT,
    status TEXT NOT NULL,                   -- see HyperliquidStatus
    filled_qty REAL NOT NULL DEFAULT 0.0,   -- signed
    avg_price REAL,
    commission REAL,
    reject_reason TEXT,
    error_code TEXT,
    source TEXT NOT NULL,                   -- 'rest' | 'wss'
    policy_fingerprint TEXT NOT NULL DEFAULT '',
    payload TEXT
);
CREATE INDEX IF NOT EXISTS ix_hla_status ON hyperliquid_acks(status);
CREATE INDEX IF NOT EXISTS ix_hla_ts ON hyperliquid_acks(ts_ns);
CREATE INDEX IF NOT EXISTS ix_hla_venue_oid ON hyperliquid_acks(venue_order_id);
"""


def bootstrap_journal(journal: Any) -> None:
    """Idempotently install the additive ``hyperliquid_*`` tables.

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
class HyperliquidAdapterPolicy:
    """Declarative configuration for the Hyperliquid adapter.

    ``venue``             canonical venue id (``hyperliquid``).
    ``testnet``           address testnet endpoints (paper transport
                          ignores this; recorded for the audit
                          trail).
    ``block_on_invalid``  return a BlockReason from ``on_request``
                          when validation fails (default False —
                          journal a VALIDATION_FAILED event).
    ``default_tif``       default limit TIF (``Gtc``).
    ``asset_index``       optional coin -> asset-index map used when
                          an intent omits ``asset``.
    ``vault_address``     optional vault the action targets (part of
                          the signable payload).

    Never holds private keys or secrets — signing is an injected
    callback.
    """

    venue: str = DEFAULT_VENUE
    testnet: bool = False
    block_on_invalid: bool = False
    default_tif: str = "Gtc"
    asset_index: Optional[Dict[str, int]] = None
    vault_address: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.venue:
            raise ValueError("venue must be non-empty")
        if self.default_tif not in _HL_TIME_IN_FORCE:
            raise ValueError(
                f"default_tif must be one of "
                f"{sorted(_HL_TIME_IN_FORCE)}, got {self.default_tif!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "venue": self.venue,
            "testnet": bool(self.testnet),
            "block_on_invalid": bool(self.block_on_invalid),
            "default_tif": self.default_tif,
            "asset_index": dict(self.asset_index or {}),
            "vault_address_set": bool(self.vault_address),
        }


DEFAULT_HYPERLIQUID_ADAPTER_POLICY = HyperliquidAdapterPolicy()


def policy_fingerprint(policy: HyperliquidAdapterPolicy) -> str:
    """Deterministic SHA-256 of a policy's serialisable fields."""
    payload = json.dumps(
        policy.to_dict(), sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


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
    f = _coerce_float(value)
    if f is None:
        return None
    return int(f)


def _coerce_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_hl_coin(coin: str) -> bool:
    """HL perp coins are upper-case ASCII (BTC, ETH, AVAX, ...) —
    no quote-suffix rule, unlike the Binance siblings."""
    if not coin or not coin.isascii() or not coin.isupper():
        return False
    return coin.isalnum() or "-" in coin


def _intent_fingerprint(
    *,
    client_order_id: str,
    coin: Optional[str],
    side: str,
    intended_qty: float,
    price: Optional[float],
    order_type: Optional[str],
    time_in_force: Optional[str],
    venue: Optional[str],
    cloid: Optional[str],
) -> str:
    payload = json.dumps(
        {
            "coid": client_order_id,
            "coin": coin,
            "side": side,
            "qty": intended_qty,
            "price": price,
            "type": order_type,
            "tif": time_in_force,
            "venue": venue,
            "cloid": cloid,
        },
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# cloid
# ---------------------------------------------------------------------------


def is_valid_cloid(cloid: Any) -> bool:
    """True when ``cloid`` is a 128-bit client order id rendered as
    ``0x`` + 32 lowercase hex chars.  Pure."""
    if not isinstance(cloid, str):
        return False
    if not cloid.startswith("0x") or len(cloid) != 34:
        return False
    body = cloid[2:]
    return all(c in "0123456789abcdef" for c in body)


def normalize_cloid(cloid: Any) -> str:
    """Validate and canonicalise a cloid (lowercase).  Raises
    :class:`ValueError` on any malformed value — a bad cloid must
    never reach the wire."""
    if not isinstance(cloid, str):
        raise ValueError(f"normalize_cloid: not a string: {cloid!r}")
    lowered = cloid.lower()
    if not is_valid_cloid(lowered):
        raise ValueError(
            f"normalize_cloid: expected 0x + 32 hex chars, "
            f"got {cloid!r}"
        )
    return lowered


def derive_cloid(client_order_id: str) -> str:
    """Deterministically derive a cloid from a runner
    ``client_order_id``: ``0x`` + the first 16 bytes of the
    SHA-256 of the coid.  Resubmitting the same intent reuses the
    same cloid, giving venue-side idempotency.  Pure."""
    if not client_order_id:
        raise ValueError("derive_cloid: client_order_id must be non-empty")
    digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()
    return "0x" + digest[:32]


# ---------------------------------------------------------------------------
# EIP-712 signing abstraction (no private keys in this module)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignedEnvelope:
    """An EIP-712 signature triplet returned by the injected
    signer.  ``r`` / ``s`` are 0x-hex; ``v`` is the recovery id."""

    r: str
    s: str
    v: int


#: Signer callback type: receives the signable payload built by
#: :func:`build_eip712_signable` and returns a SignedEnvelope.
#: The implementation (HSM, KMS, local key) is owned by the caller;
#: this module never sees a private key.
SignerFn = Callable[[Mapping[str, Any]], SignedEnvelope]


def build_eip712_signable(
    action: Mapping[str, Any],
    *,
    nonce_ms: int,
    vault_address: Optional[str] = None,
    is_mainnet: bool = True,
) -> Dict[str, Any]:
    """Build the canonical signable payload for a Hyperliquid
    action.  Pure.

    The returned dict fixes the *shape* the injected signer signs:
    an EIP-712-style envelope with the exchange domain, the action
    verbatim, the nonce (venue replay protection — must be the
    current unix-ms), and the optional vault address.  The exact
    EIP-712 type encoding (msgpack action hash + phantom agent)
    is the signer implementation's responsibility; tests inject a
    stub signer and never touch real cryptography.

    Raises :class:`ValueError` on a non-positive nonce — signing a
    stale nonce produces an order the venue rejects anyway.
    """
    if int(nonce_ms) <= 0:
        raise ValueError(
            f"build_eip712_signable: nonce_ms must be positive, "
            f"got {nonce_ms!r}"
        )
    return {
        "domain": {
            "name": "Exchange",
            "version": "1",
            "chainId": 42161 if is_mainnet else 421614,
            "verifyingContract":
                "0x0000000000000000000000000000000000000000",
        },
        "primaryType": "HyperliquidAction",
        "types": {
            "HyperliquidAction": [
                {"name": "action", "type": "string"},
                {"name": "nonce", "type": "uint64"},
                {"name": "vaultAddress", "type": "address"},
            ],
        },
        "message": {
            "action": json.dumps(
                dict(action), sort_keys=True, separators=(",", ":"),
            ),
            "nonce": int(nonce_ms),
            "vaultAddress": (
                vault_address
                or "0x0000000000000000000000000000000000000000"
            ),
        },
    }


def sign_action(
    action: Mapping[str, Any],
    *,
    nonce_ms: int,
    signer: SignerFn,
    vault_address: Optional[str] = None,
    is_mainnet: bool = True,
) -> Dict[str, Any]:
    """Sign ``action`` via the injected signer and return the
    ``/exchange`` POST body (``action`` + ``nonce`` + ``signature``
    + optional ``vaultAddress``).  Pure apart from the callback.

    Raises :class:`TypeError` when the signer does not return a
    :class:`SignedEnvelope`.
    """
    signable = build_eip712_signable(
        action,
        nonce_ms=nonce_ms,
        vault_address=vault_address,
        is_mainnet=is_mainnet,
    )
    envelope = signer(signable)
    if not isinstance(envelope, SignedEnvelope):
        raise TypeError(
            f"sign_action: signer must return SignedEnvelope, "
            f"got {type(envelope).__name__}"
        )
    body: Dict[str, Any] = {
        "action": dict(action),
        "nonce": int(nonce_ms),
        "signature": {"r": envelope.r, "s": envelope.s,
                      "v": envelope.v},
    }
    if vault_address:
        body["vaultAddress"] = vault_address
    return body


# ---------------------------------------------------------------------------
# action.order construction
# ---------------------------------------------------------------------------


def build_order_wire_order(
    *,
    asset: int,
    is_buy: bool,
    limit_px: float,
    sz: float,
    reduce_only: bool = False,
    tif: str = "Gtc",
    cloid: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one Hyperliquid order wire object.  Pure.

    Keys follow the venue spec: ``a`` asset index, ``b`` is_buy,
    ``p`` limit price (string), ``s`` size (string), ``r``
    reduce_only, ``t`` order type (``{"limit": {"tif": tif}}``),
    ``c`` cloid (omitted when None).  Prices/sizes are rendered
    with ``repr`` to keep the wire byte-stable.
    """
    if tif not in _HL_TIME_IN_FORCE:
        raise ValueError(
            f"build_order_wire_order: tif must be one of "
            f"{sorted(_HL_TIME_IN_FORCE)}, got {tif!r}"
        )
    if int(asset) < 0:
        raise ValueError(f"build_order_wire_order: bad asset {asset!r}")
    if not math.isfinite(limit_px) or limit_px <= 0.0:
        raise ValueError(
            f"build_order_wire_order: bad limit_px {limit_px!r}"
        )
    if not math.isfinite(sz) or sz <= 0.0:
        raise ValueError(f"build_order_wire_order: bad sz {sz!r}")
    wire: Dict[str, Any] = {
        "a": int(asset),
        "b": bool(is_buy),
        "p": repr(float(limit_px)),
        "s": repr(float(sz)),
        "r": bool(reduce_only),
        "t": {"limit": {"tif": tif}},
    }
    if cloid is not None:
        wire["c"] = normalize_cloid(cloid)
    return wire


def build_order_action(
    orders: List[Mapping[str, Any]],
    *,
    grouping: str = "na",
) -> Dict[str, Any]:
    """Build the ``action.order`` payload.  Pure.

    ``orders`` is a list of wire objects from
    :func:`build_order_wire_order`.  ``grouping`` is ``na`` /
    ``normalTpsl`` / ``positionTpsl``.
    """
    if grouping not in ("na", "normalTpsl", "positionTpsl"):
        raise ValueError(
            f"build_order_action: bad grouping {grouping!r}"
        )
    if not orders:
        raise ValueError("build_order_action: orders must be non-empty")
    return {
        "type": "order",
        "orders": [dict(o) for o in orders],
        "grouping": grouping,
    }


# ---------------------------------------------------------------------------
# Intent validation
# ---------------------------------------------------------------------------


def validate_hl_intent(
    request: Mapping[str, Any],
    policy: HyperliquidAdapterPolicy = DEFAULT_HYPERLIQUID_ADAPTER_POLICY,
) -> Tuple[bool, str]:
    """Validate a hyperliquid-tagged intent (pure).

    An intent is HL-tagged when ``request["venue"] == policy.venue``
    or ``request["hyperliquid"]`` is truthy.

    Rules:
      1. venue matches the policy (or the opt-in flag is set);
      2. ``client_order_id`` non-empty;
      3. ``symbol`` (coin) upper-case ASCII;
      4. ``side`` in {BUY, SELL};
      5. ``qty`` finite with ``|qty| >= 1e-9``;
      6. ``price`` finite and positive (all HL order actions are
         limit-typed; an aggressive IOC is a far-touch limit);
      7. ``order_type`` is ``LIMIT`` (the only type this adapter
         builds — trigger orders are out of scope);
      8. ``time_in_force`` in {Gtc, Ioc, Alo} (defaulting to
         ``policy.default_tif``);
      9. ``asset`` resolvable — carried directly or present in
         ``policy.asset_index``;
      10. ``cloid``, when supplied, is a valid 128-bit cloid.
    """
    min_qty = 1e-9
    venue_raw = _coerce_str(request.get("venue"))
    opt_in = _coerce_bool(request.get("hyperliquid"))
    if not (venue_raw == policy.venue or opt_in):
        return (False, f"venue_not_hyperliquid:{venue_raw!r}")

    coid = _coerce_str(request.get("client_order_id"))
    if not coid:
        return (False, "client_order_id_missing")
    coin = _coerce_str(request.get("symbol")) or _coerce_str(
        request.get("coin"),
    )
    if not coin:
        return (False, "symbol_missing")
    if not _is_hl_coin(coin):
        return (False, f"symbol_not_hl_coin:{coin}")
    side = _coerce_str(request.get("side"))
    if side is None:
        return (False, "side_missing")
    side_u = side.upper()
    if side_u not in {"BUY", "SELL"}:
        return (False, f"side_invalid:{side_u}")
    qty = _coerce_float(request.get("qty"))
    if qty is None:
        return (False, "qty_not_coercible")
    if abs(qty) < min_qty:
        return (False, f"qty_below_min:{abs(qty):.9f}")

    order_type = (_coerce_str(request.get("order_type")) or "LIMIT").upper()
    if order_type != "LIMIT":
        return (False, f"order_type_unsupported:{order_type}")

    price = _coerce_float(request.get("price"))
    if price is None:
        return (False, "price_missing")
    if price <= 0.0:
        return (False, f"price_non_positive:{price:.6f}")

    tif_raw = _coerce_str(request.get("time_in_force"))
    if tif_raw is None:
        tif = policy.default_tif
    else:
        # Accept case-insensitive input, canonicalise to HL case.
        tif = next(
            (t for t in _HL_TIME_IN_FORCE if t.lower() == tif_raw.lower()),
            tif_raw,
        )
    if tif not in _HL_TIME_IN_FORCE:
        return (False, f"time_in_force_unsupported:{tif_raw}")

    asset = _coerce_int(request.get("asset"))
    if asset is None:
        index = policy.asset_index or {}
        if coin not in index:
            return (False, f"asset_index_unknown:{coin}")
        asset = int(index[coin])
    if asset < 0:
        return (False, f"asset_invalid:{asset}")

    cloid = _coerce_str(request.get("cloid"))
    if cloid is not None and not is_valid_cloid(cloid.lower()):
        return (False, f"cloid_invalid:{cloid}")
    return (True, "")


# ---------------------------------------------------------------------------
# Ack classification
# ---------------------------------------------------------------------------


def _classify_error_string(msg: str) -> str:
    """Map a Hyperliquid error string to the reason taxonomy."""
    m = msg.lower()
    if "insufficient margin" in m or "margin" in m and "insufficient" in m:
        return MARGIN_REJECTED
    if "429" in m or "rate limit" in m or "too many requests" in m:
        return RATE_LIMITED
    if "post only" in m or "would have immediately matched" in m:
        return POST_ONLY_WOULD_CROSS
    if "could not immediately match" in m:
        return IOC_NO_MATCH
    if "tick" in m or "price band" in m or "min trade" in m:
        return PRICE_BAND
    return OTHER


def classify_exchange_response(
    response: Mapping[str, Any],
    *,
    client_order_id: Optional[str] = None,
) -> Tuple[HyperliquidStatus, float, Optional[float], Optional[float],
           Optional[str], Optional[str], Optional[str]]:
    """Classify a Hyperliquid ``POST /exchange`` order response.

    Success envelope::

        {"status": "ok",
         "response": {"type": "order", "data": {"statuses": [
             {"resting": {"oid": 123}},
             {"filled": {"totalSz": "0.05", "avgPx": "50000.0",
                          "oid": 124}},
             {"error": "Insufficient margin ..."}
         ]}}}

    Failure envelope: ``{"status": "err", "response": "<msg>"}``
    (also the shape of HTTP 429 bodies).

    Returns ``(status, filled_qty, avg_price, commission,
    venue_order_id, reject_reason, error_code)``.  Only the first
    per-order status is classified (this adapter submits one order
    per action).  ``filled_qty`` is unsigned here — the sign is
    applied by the caller from the request side.
    """
    envelope_status = str(response.get("status") or "").strip().lower()
    if envelope_status == "err":
        msg = _coerce_str(response.get("response")) or ""
        return (
            HyperliquidStatus.REJECTED,
            0.0,
            None,
            None,
            None,
            _classify_error_string(msg),
            msg[:128] or None,
        )

    inner = response.get("response")
    if not isinstance(inner, Mapping):
        return (
            HyperliquidStatus.REJECTED, 0.0, None, None, None,
            OTHER, "malformed_envelope",
        )
    data = inner.get("data")
    statuses = data.get("statuses") if isinstance(data, Mapping) else None
    if not isinstance(statuses, (list, tuple)) or not statuses:
        return (
            HyperliquidStatus.REJECTED, 0.0, None, None, None,
            OTHER, "missing_statuses",
        )
    first = statuses[0]
    if not isinstance(first, Mapping):
        return (
            HyperliquidStatus.REJECTED, 0.0, None, None, None,
            OTHER, "malformed_status",
        )

    if "error" in first:
        msg = _coerce_str(first.get("error")) or ""
        return (
            HyperliquidStatus.REJECTED,
            0.0,
            None,
            None,
            None,
            _classify_error_string(msg),
            msg[:128] or None,
        )
    if "resting" in first:
        resting = first.get("resting") or {}
        oid = _coerce_str(resting.get("oid")) if isinstance(
            resting, Mapping) else None
        return (
            HyperliquidStatus.OPEN,
            0.0,
            None,
            None,
            oid,
            None,
            None,
        )
    if "filled" in first:
        filled = first.get("filled") or {}
        if not isinstance(filled, Mapping):
            return (
                HyperliquidStatus.REJECTED, 0.0, None, None, None,
                OTHER, "malformed_filled",
            )
        total_sz = _coerce_float(filled.get("totalSz")) or 0.0
        avg_px = _coerce_float(filled.get("avgPx"))
        oid = _coerce_str(filled.get("oid"))
        return (
            HyperliquidStatus.FILLED,
            float(total_sz),
            avg_px,
            None,
            oid,
            None,
            None,
        )
    return (
        HyperliquidStatus.REJECTED, 0.0, None, None, None,
        OTHER, f"unknown_status_shape:{sorted(first.keys())}",
    )


# ---------------------------------------------------------------------------
# WS parsing (orderUpdates / userFills)
# ---------------------------------------------------------------------------


def parse_ws_message(raw: Any) -> Optional[Dict[str, Any]]:
    """Parse one Hyperliquid user-data WebSocket frame.

    Returns a dict with ``kind`` in
    {``ORDER_UPDATE``, ``USER_FILL``, ``SUBSCRIPTION``,
    ``PONG``, ``WSS_ERROR``, ``WSS_OTHER``}; ``None`` when the
    frame is unparseable.

    ``orderUpdates`` data item shape::

        {"order": {"coin": "BTC", "side": "B", "limitPx": "50000",
                   "sz": "0.05", "oid": 123, "cloid": "0x...",
                   "tif": "Gtc", "timestamp": 1700000000000},
         "status": "open",            -- open | filled | canceled |
                                       -- triggered | rejected |
                                       -- marginCanceled
         "statusTimestamp": 1700000000001}

    ``userFills`` data shape::

        {"user": "0x...", "fills": [
            {"coin": "BTC", "px": "50000", "sz": "0.05",
             "side": "B", "time": 1700000000000, "oid": 123,
             "cloid": "0x...", "fee": "0.01", "tid": 456,
             "crossed": false}
        ]}
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return None
    else:
        payload = raw
    if not isinstance(payload, Mapping):
        return None
    channel = _coerce_str(payload.get("channel"))
    if channel is None:
        return {"kind": "WSS_OTHER", "raw": payload}
    data = payload.get("data")

    if channel == "orderUpdates":
        items = data if isinstance(data, (list, tuple)) else []
        out: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            order = item.get("order")
            if not isinstance(order, Mapping):
                continue
            out.append({
                "coin": _coerce_str(order.get("coin")),
                "side": _coerce_str(order.get("side")),
                "limit_px": _coerce_float(order.get("limitPx")),
                "sz": _coerce_float(order.get("sz")),
                "oid": _coerce_str(order.get("oid")),
                "cloid": _coerce_str(order.get("cloid")),
                "tif": _coerce_str(order.get("tif")),
                "ts_ms": _coerce_int(item.get("statusTimestamp"))
                    or _coerce_int(order.get("timestamp")),
                "status_raw": _coerce_str(item.get("status")),
            })
        return {"kind": "ORDER_UPDATE", "updates": out, "raw": payload}

    if channel == "userFills":
        fills = data.get("fills") if isinstance(data, Mapping) else None
        fills = fills if isinstance(fills, (list, tuple)) else []
        out = []
        for f in fills:
            if not isinstance(f, Mapping):
                continue
            out.append({
                "coin": _coerce_str(f.get("coin")),
                "px": _coerce_float(f.get("px")),
                "sz": _coerce_float(f.get("sz")),
                "side": _coerce_str(f.get("side")),
                "ts_ms": _coerce_int(f.get("time")),
                "oid": _coerce_str(f.get("oid")),
                "cloid": _coerce_str(f.get("cloid")),
                "fee": _coerce_float(f.get("fee")),
                "tid": _coerce_str(f.get("tid")),
                "crossed": _coerce_bool(f.get("crossed")),
            })
        return {"kind": "USER_FILL", "fills": out, "raw": payload}

    if channel == "subscriptionResponse":
        return {"kind": "SUBSCRIPTION", "raw": payload}
    if channel == "pong":
        return {"kind": "PONG", "raw": payload}
    if channel == "error":
        return {"kind": "WSS_ERROR", "raw": payload}
    return {"kind": "WSS_OTHER", "raw": payload}


def map_order_update_status(
    status_raw: Optional[str],
) -> Tuple[HyperliquidStatus, Optional[str]]:
    """Map an ``orderUpdates`` status string to the lifecycle +
    optional reject reason.

    ``marginCanceled`` is a CANCELED outcome with reason
    :data:`MARGIN_REJECTED` (the venue kills the order when margin
    no longer covers it).
    """
    s = str(status_raw or "").strip().lower()
    return {
        "open": (HyperliquidStatus.OPEN, None),
        "filled": (HyperliquidStatus.FILLED, None),
        "canceled": (HyperliquidStatus.CANCELED, None),
        "triggered": (HyperliquidStatus.OPEN, None),
        "rejected": (HyperliquidStatus.REJECTED, OTHER),
        "margincanceled": (HyperliquidStatus.CANCELED, MARGIN_REJECTED),
    }.get(s, (HyperliquidStatus.REJECTED, f"{OTHER}:unknown:{s}"))


# ---------------------------------------------------------------------------
# Immutable views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HyperliquidState:
    """Live view of a hyperliquid intent (``PENDING`` / terminal
    status)."""

    client_order_id: str
    coin: Optional[str]
    side: Optional[str]
    intended_qty: float
    price: Optional[float]
    venue: Optional[str]
    cloid: Optional[str]
    venue_order_id: Optional[str]
    hyperliquid_signature: str
    status: HyperliquidStatus
    ts_ns: int
    updated_ts_ns: int
    policy_fingerprint: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "coin": self.coin,
            "side": self.side,
            "intended_qty": self.intended_qty,
            "price": self.price,
            "venue": self.venue,
            "cloid": self.cloid,
            "venue_order_id": self.venue_order_id,
            "hyperliquid_signature": self.hyperliquid_signature,
            "status": self.status.value,
            "ts_ns": self.ts_ns,
            "updated_ts_ns": self.updated_ts_ns,
            "policy_fingerprint": self.policy_fingerprint,
        }


@dataclass(frozen=True)
class HyperliquidSnapshot:
    """Aggregate snapshot across intents."""

    intents: Tuple[HyperliquidState, ...]
    n_pending: int = 0
    n_open: int = 0
    n_filled: int = 0
    n_canceled: int = 0
    n_expired: int = 0
    n_rejected: int = 0
    n_blocked: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_intents": len(self.intents),
            "n_pending": self.n_pending,
            "n_open": self.n_open,
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


class HyperliquidAdapter:
    """Runtime Hyperliquid adapter.

    Wire into the runner via ``runner.register(adapter)`` +
    ``runner.register_on_fill(adapter)`` (same hook names as the
    Binance siblings), or drive ``on_request`` / ``on_fill`` /
    ``record_reject`` / ``apply_ws_event`` directly.  The
    constructor installs the additive ``hyperliquid_*`` tables on
    the journal (idempotent) and recovers live state from the
    intent projection (cold-start path).
    """

    def __init__(
        self,
        *,
        journal: Any,
        policy: HyperliquidAdapterPolicy = DEFAULT_HYPERLIQUID_ADAPTER_POLICY,
        auto_recover: bool = True,
    ) -> None:
        self._journal = journal
        self.policy = policy
        self._fp: str = policy_fingerprint(policy)
        self._venue = str(policy.venue)
        self._default_tif = str(policy.default_tif)
        self._block_on_invalid = bool(policy.block_on_invalid)

        self._insert_intent_sql = (
            "INSERT INTO hyperliquid_intents ("
            "client_order_id, ts_first_seen_ns, ts_last_seen_ns, "
            "coin, asset, side, qty, price, order_type, "
            "time_in_force, reduce_only, cloid, venue, "
            "hyperliquid_signature, venue_order_id, status, "
            "updated_ts_ns, policy_fingerprint, payload"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?)"
            " ON CONFLICT(client_order_id) DO UPDATE SET "
            "ts_last_seen_ns = excluded.ts_last_seen_ns, "
            "venue_order_id = COALESCE(excluded.venue_order_id, "
            "hyperliquid_intents.venue_order_id), "
            "cloid = COALESCE(excluded.cloid, "
            "hyperliquid_intents.cloid), "
            "status = excluded.status, "
            "updated_ts_ns = excluded.updated_ts_ns, "
            "payload = excluded.payload"
        )
        self._insert_event_sql = (
            "INSERT INTO hyperliquid_events ("
            "ts_ns, client_order_id, source, kind, venue_order_id, "
            "raw_payload, policy_fingerprint"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        self._upsert_ack_sql = (
            "INSERT INTO hyperliquid_acks ("
            "client_order_id, ts_ns, coin, side, intended_qty, "
            "price, venue, cloid, venue_order_id, status, "
            "filled_qty, avg_price, commission, reject_reason, "
            "error_code, source, policy_fingerprint, payload"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?)"
            " ON CONFLICT(client_order_id) DO UPDATE SET "
            "ts_ns = excluded.ts_ns, "
            "venue_order_id = COALESCE(excluded.venue_order_id, "
            "hyperliquid_acks.venue_order_id), "
            "cloid = COALESCE(excluded.cloid, "
            "hyperliquid_acks.cloid), "
            "status = excluded.status, "
            "filled_qty = excluded.filled_qty, "
            "avg_price = COALESCE(excluded.avg_price, "
            "hyperliquid_acks.avg_price), "
            "commission = COALESCE(excluded.commission, "
            "hyperliquid_acks.commission), "
            "reject_reason = excluded.reject_reason, "
            "error_code = excluded.error_code, "
            "source = excluded.source, "
            "payload = excluded.payload"
        )
        bootstrap_journal(journal)
        self._intents: Dict[str, HyperliquidState] = {}
        # cloid -> coid index for WS frames that only carry cloid.
        self._cloid_index: Dict[str, str] = {}
        if auto_recover:
            self._recover_from_intents()

    # -- public reads ---------------------------------------------------

    @property
    def policy_fingerprint(self) -> str:
        return self._fp

    @property
    def journal(self) -> Any:
        return self._journal

    def get(self, client_order_id: str) -> Optional[HyperliquidState]:
        return self._intents.get(client_order_id)

    def snapshot(self) -> HyperliquidSnapshot:
        states = tuple(self._intents.values())

        def _n(s: HyperliquidStatus) -> int:
            return sum(1 for st in states if st.status == s)

        return HyperliquidSnapshot(
            intents=states,
            n_pending=_n(HyperliquidStatus.PENDING),
            n_open=_n(HyperliquidStatus.OPEN),
            n_filled=_n(HyperliquidStatus.FILLED),
            n_canceled=_n(HyperliquidStatus.CANCELED),
            n_expired=_n(HyperliquidStatus.EXPIRED),
            n_rejected=_n(HyperliquidStatus.REJECTED),
            n_blocked=_n(HyperliquidStatus.BLOCKED),
        )

    def recover(self) -> HyperliquidSnapshot:
        """Re-populate the in-memory cache from
        ``hyperliquid_intents`` (cold-start path)."""
        self._recover_from_intents()
        return self.snapshot()

    # -- private cache rebuild ------------------------------------------

    def _recover_from_intents(self) -> None:
        rows = list(self._journal.conn.execute(
            "SELECT client_order_id, ts_first_seen_ns, coin, side, "
            "qty, price, venue, cloid, venue_order_id, "
            "hyperliquid_signature, status, policy_fingerprint "
            "FROM hyperliquid_intents"
        ))
        for r in rows:
            d = dict(r)
            try:
                status = HyperliquidStatus.from_raw(d["status"])
            except ValueError:
                continue
            self._intents[d["client_order_id"]] = HyperliquidState(
                client_order_id=d["client_order_id"],
                coin=d.get("coin"),
                side=d.get("side"),
                intended_qty=float(d.get("qty") or 0.0),
                price=d.get("price"),
                venue=d.get("venue"),
                cloid=d.get("cloid"),
                venue_order_id=d.get("venue_order_id"),
                hyperliquid_signature=d.get(
                    "hyperliquid_signature", "") or "",
                status=status,
                ts_ns=int(d["ts_first_seen_ns"] or 0),
                updated_ts_ns=int(d["ts_first_seen_ns"] or 0),
                policy_fingerprint=d.get("policy_fingerprint", "") or "",
            )
            if d.get("cloid"):
                self._cloid_index[str(d["cloid"])] = d["client_order_id"]

    # -- wire building ----------------------------------------------------

    def build_signed_order(
        self,
        request: Mapping[str, Any],
        *,
        nonce_ms: int,
        signer: SignerFn,
    ) -> Dict[str, Any]:
        """Validate + build + sign one order action for
        ``request``.  Returns the ``/exchange`` POST body.

        Raises :class:`ValueError` on an invalid intent.
        """
        is_valid, reason = validate_hl_intent(request, self.policy)
        if not is_valid:
            raise ValueError(
                f"build_signed_order: invalid intent: {reason}"
            )
        coin = str(request.get("symbol") or request.get("coin"))
        side_u = str(request.get("side")).upper()
        qty = abs(float(request.get("qty")))  # type: ignore[arg-type]
        price = float(request.get("price"))  # type: ignore[arg-type]
        asset = _coerce_int(request.get("asset"))
        if asset is None:
            asset = int((self.policy.asset_index or {})[coin])
        tif_raw = _coerce_str(request.get("time_in_force"))
        if tif_raw is None:
            tif = self._default_tif
        else:
            tif = next(
                (t for t in _HL_TIME_IN_FORCE
                 if t.lower() == tif_raw.lower()),
                tif_raw,
            )
        cloid = _coerce_str(request.get("cloid"))
        if cloid is None:
            cloid = derive_cloid(str(request.get("client_order_id")))
        else:
            cloid = normalize_cloid(cloid)
        wire_order = build_order_wire_order(
            asset=int(asset),
            is_buy=(side_u == "BUY"),
            limit_px=price,
            sz=qty,
            reduce_only=_coerce_bool(request.get("reduce_only")),
            tif=tif,
            cloid=cloid,
        )
        action = build_order_action([wire_order])
        return sign_action(
            action,
            nonce_ms=nonce_ms,
            signer=signer,
            vault_address=self.policy.vault_address,
            is_mainnet=not self.policy.testnet,
        )

    # -- runner hooks ---------------------------------------------------

    def on_request(
        self,
        request: Mapping[str, Any],
        journal: Any,
        ts_ns: int,
    ) -> "ComponentResult":
        """Runner pre-trade hook (same contract as the Binance
        siblings)."""
        venue_raw = _coerce_str(request.get("venue"))
        opt_in = _coerce_bool(request.get("hyperliquid"))
        if not (venue_raw == self._venue or opt_in):
            return _empty_result()

        is_valid, reason = validate_hl_intent(request, self.policy)
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
                    component="hyperliquid_adapter",
                    reason=reason,
                    severity="WARN",
                )
                return ComponentResult(block=block, observation={
                    "hyperliquid_adapter": "validation_failed",
                    "reason": reason,
                })
            return ComponentResult(observation={
                "hyperliquid_adapter": "validation_failed",
                "reason": reason,
            })

        coid = str(request.get("client_order_id"))
        coin = _coerce_str(request.get("symbol")) or _coerce_str(
            request.get("coin"))
        side_u = (_coerce_str(request.get("side")) or "").upper()
        qty = float(request.get("qty"))  # type: ignore[arg-type]
        price = _coerce_float(request.get("price"))
        asset = _coerce_int(request.get("asset"))
        if asset is None:
            asset = int((self.policy.asset_index or {})[coin])  # type: ignore[index]
        tif_raw = _coerce_str(request.get("time_in_force"))
        if tif_raw is None:
            tif = self._default_tif
        else:
            tif = next(
                (t for t in _HL_TIME_IN_FORCE
                 if t.lower() == tif_raw.lower()),
                tif_raw,
            )
        cloid = _coerce_str(request.get("cloid"))
        if cloid is None:
            cloid = derive_cloid(coid)
        else:
            cloid = normalize_cloid(cloid)
        signature = _intent_fingerprint(
            client_order_id=coid,
            coin=coin,
            side=side_u,
            intended_qty=qty,
            price=price,
            order_type="LIMIT",
            time_in_force=tif,
            venue=self._venue,
            cloid=cloid,
        )
        signed_qty = qty if side_u == "BUY" else -abs(qty)
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                self._insert_intent_sql,
                (
                    coid, int(ts_ns), int(ts_ns), coin, int(asset),
                    side_u, signed_qty, price, "LIMIT", tif,
                    int(_coerce_bool(request.get("reduce_only"))),
                    cloid, self._venue, signature, None,
                    HyperliquidStatus.PENDING.value, int(ts_ns),
                    self._fp,
                    json.dumps({"opt_in": opt_in}, default=str),
                ),
            )
            cur.execute(
                self._insert_event_sql,
                (
                    int(ts_ns), coid, "rest_submit", T_INTENT_TAGGED,
                    None,
                    json.dumps(
                        {
                            "intent": {
                                "coin": coin, "side": side_u,
                                "qty": signed_qty, "price": price,
                                "tif": tif, "cloid": cloid,
                            },
                        },
                        default=str,
                    ),
                    self._fp,
                ),
            )
        self._journal.conn.commit()
        self._intents[coid] = HyperliquidState(
            client_order_id=coid,
            coin=coin,
            side=side_u,
            intended_qty=signed_qty,
            price=price,
            venue=self._venue,
            cloid=cloid,
            venue_order_id=None,
            hyperliquid_signature=signature,
            status=HyperliquidStatus.PENDING,
            ts_ns=int(ts_ns),
            updated_ts_ns=int(ts_ns),
            policy_fingerprint=self._fp,
        )
        self._cloid_index[cloid] = coid
        return ComponentResult(observation={
            "hyperliquid_adapter": "tagged",
            "hyperliquid_signature": signature,
            "cloid": cloid,
            "venue": self._venue,
        })

    def on_fill(
        self,
        request: Mapping[str, Any],
        ack: Mapping[str, Any],
        journal: Any,
        ts_ns: int,
    ) -> "ComponentResult":
        """Runner post-fill hook: classify the ``/exchange``
        response, UPSERT intent + ack, journal the event.

        ``ack`` is the raw exchange envelope.  The sign of
        ``filled_qty`` is applied from the request side.  Duplicate
        callbacks against a terminal outcome are no-ops.
        """
        coid = _coerce_str(request.get("client_order_id"))
        if not coid:
            return _empty_result()
        existing = self._intents.get(coid)
        venue_raw = _coerce_str(request.get("venue"))
        opt_in = _coerce_bool(request.get("hyperliquid"))
        if existing is None and not (
            venue_raw == self._venue or opt_in
        ):
            return _empty_result()
        if existing is not None and existing.status in _TERMINAL_STATUSES:
            return ComponentResult(observation={
                "hyperliquid_adapter": "duplicate_callback",
            })

        (status, filled_qty, avg_price, commission, venue_order_id,
         reject_reason, error_code) = classify_exchange_response(
            ack, client_order_id=coid,
        )
        side_u = (
            existing.side if existing else _coerce_str(
                request.get("side"))
        ) or ""
        if side_u.upper() == "SELL":
            filled_qty = -abs(filled_qty)
        else:
            filled_qty = abs(filled_qty)

        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                self._insert_intent_sql,
                (
                    coid, int(ts_ns), int(ts_ns),
                    existing.coin if existing else _coerce_str(
                        request.get("symbol")),
                    _coerce_int(request.get("asset")),
                    side_u.upper() or None,
                    existing.intended_qty if existing else 0.0,
                    existing.price if existing else _coerce_float(
                        request.get("price")),
                    "LIMIT", self._default_tif, 0,
                    existing.cloid if existing else None,
                    self._venue,
                    existing.hyperliquid_signature if existing else "",
                    venue_order_id or (
                        existing.venue_order_id if existing else None),
                    status.value, int(ts_ns), self._fp,
                    json.dumps({"src": "rest"}, default=str),
                ),
            )
            cur.execute(
                self._upsert_ack_sql,
                (
                    coid, int(ts_ns),
                    existing.coin if existing else _coerce_str(
                        request.get("symbol")),
                    side_u.upper() or None,
                    existing.intended_qty if existing else 0.0,
                    existing.price if existing else _coerce_float(
                        request.get("price")),
                    self._venue,
                    existing.cloid if existing else None,
                    venue_order_id,
                    status.value, float(filled_qty), avg_price,
                    commission, reject_reason, error_code,
                    HyperliquidAckSource.REST.value, self._fp,
                    json.dumps(
                        {"raw_keys": sorted(ack.keys())}, default=str,
                    ),
                ),
            )
            cur.execute(
                self._insert_event_sql,
                (
                    int(ts_ns), coid, "rest_ack",
                    T_ACK_REJECT if status == HyperliquidStatus.REJECTED
                    else T_ACK_OK,
                    venue_order_id,
                    json.dumps(
                        {
                            "status": status.value,
                            "filled_qty": filled_qty,
                            "avg_price": avg_price,
                            "reject_reason": reject_reason,
                        },
                        default=str,
                    ),
                    self._fp,
                ),
            )
        self._journal.conn.commit()
        if existing is not None:
            self._intents[coid] = HyperliquidState(
                client_order_id=existing.client_order_id,
                coin=existing.coin,
                side=existing.side,
                intended_qty=existing.intended_qty,
                price=existing.price,
                venue=existing.venue,
                cloid=existing.cloid,
                venue_order_id=venue_order_id or existing.venue_order_id,
                hyperliquid_signature=existing.hyperliquid_signature,
                status=status,
                ts_ns=existing.ts_ns,
                updated_ts_ns=int(ts_ns),
                policy_fingerprint=existing.policy_fingerprint,
            )
        return ComponentResult(observation={
            "hyperliquid_adapter": "ack_classified",
            "hyperliquid_status": status.value,
            "hyperliquid_filled_qty": filled_qty,
        })

    def record_reject(
        self,
        *,
        request: Mapping[str, Any],
        ack: Mapping[str, Any],
        ts_ns: Optional[int] = None,
    ) -> Optional[HyperliquidState]:
        """Explicit reject hook (P7-EXEC-051 pattern).  Journals a
        terminal REJECTED outcome for an HL-tagged intent."""
        coid = _coerce_str(request.get("client_order_id"))
        venue_raw = _coerce_str(request.get("venue"))
        opt_in = _coerce_bool(request.get("hyperliquid"))
        if not coid or not (venue_raw == self._venue or opt_in):
            return None
        now_ns = int(ts_ns) if ts_ns is not None else int(time.time_ns())
        (_, _, _, _, venue_order_id, reject_reason,
         error_code) = classify_exchange_response(ack)
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                self._insert_event_sql,
                (
                    now_ns, coid, "rest_ack", T_ACK_REJECT,
                    venue_order_id,
                    json.dumps(
                        {
                            "reject_reason": reject_reason,
                            "error_code": error_code,
                            "src": "record_reject",
                        },
                        default=str,
                    ),
                    self._fp,
                ),
            )
            cur.execute(
                self._upsert_ack_sql,
                (
                    coid, now_ns,
                    _coerce_str(request.get("symbol")),
                    (_coerce_str(request.get("side")) or "").upper(),
                    float(request.get("qty") or 0.0),
                    _coerce_float(request.get("price")),
                    self._venue,
                    _coerce_str(request.get("cloid")),
                    venue_order_id,
                    HyperliquidStatus.REJECTED.value, 0.0, None, None,
                    reject_reason, error_code,
                    HyperliquidAckSource.REST.value, self._fp,
                    json.dumps({"src": "record_reject"}, default=str),
                ),
            )
        self._journal.conn.commit()
        state = HyperliquidState(
            client_order_id=coid,
            coin=_coerce_str(request.get("symbol")),
            side=(_coerce_str(request.get("side")) or "").upper() or None,
            intended_qty=float(request.get("qty") or 0.0),
            price=_coerce_float(request.get("price")),
            venue=self._venue,
            cloid=_coerce_str(request.get("cloid")),
            venue_order_id=venue_order_id,
            hyperliquid_signature="",
            status=HyperliquidStatus.REJECTED,
            ts_ns=now_ns,
            updated_ts_ns=now_ns,
            policy_fingerprint=self._fp,
        )
        self._intents[coid] = state
        return state

    # -- WS application ---------------------------------------------------

    def apply_ws_event(
        self,
        parsed: Mapping[str, Any],
        *,
        ts_ns: int,
    ) -> Optional["ComponentResult"]:
        """Apply one parsed WS frame (from :func:`parse_ws_message`)
        to the live state + journal.  Frames are reconciled to
        intents via cloid (falling back to venue_order_id).

        ``USER_FILL`` frames journal an event row and update the
        ack projection's filled_qty / avg_price / commission; they
        do not mark the intent FILLED on their own — the paired
        ``orderUpdates`` ``filled`` status owns the terminal
        transition.
        """
        kind = str(parsed.get("kind") or "")
        if kind == "ORDER_UPDATE":
            last: Optional[ComponentResult] = None
            for upd in parsed.get("updates") or []:
                last = self._apply_order_update(upd, ts_ns=int(ts_ns))
            return last
        if kind == "USER_FILL":
            for fill in parsed.get("fills") or []:
                self._apply_user_fill(fill, ts_ns=int(ts_ns))
            return ComponentResult(observation={
                "hyperliquid_wss": "user_fills",
                "n": len(parsed.get("fills") or []),
            })
        return ComponentResult(observation={
            "hyperliquid_wss": "frame_ignored",
            "kind": kind,
        })

    def _resolve_coid(
        self,
        *,
        cloid: Optional[str],
        oid: Optional[str],
    ) -> Tuple[Optional[str], Optional[HyperliquidState]]:
        if cloid:
            coid = self._cloid_index.get(cloid.lower())
            if coid:
                return coid, self._intents.get(coid)
        if oid:
            for coid, st in self._intents.items():
                if st.venue_order_id == oid:
                    return coid, st
        return None, None

    def _apply_order_update(
        self,
        upd: Mapping[str, Any],
        *,
        ts_ns: int,
    ) -> Optional[ComponentResult]:
        cloid = _coerce_str(upd.get("cloid"))
        oid = _coerce_str(upd.get("oid"))
        status, reason = map_order_update_status(
            _coerce_str(upd.get("status_raw")))
        coid, existing = self._resolve_coid(cloid=cloid, oid=oid)
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                self._insert_event_sql,
                (
                    int(ts_ns), coid or "unknown", "wss_userdata",
                    T_WS_UPDATE, oid,
                    json.dumps(
                        {
                            "kind": "ORDER_UPDATE",
                            "status_raw": upd.get("status_raw"),
                            "cloid": cloid,
                        },
                        default=str,
                    ),
                    self._fp,
                ),
            )
            if coid is not None and (
                existing is None
                or existing.status not in _TERMINAL_STATUSES
            ):
                cur.execute(
                    self._upsert_ack_sql,
                    (
                        coid, int(ts_ns),
                        existing.coin if existing else _coerce_str(
                            upd.get("coin")),
                        existing.side if existing else None,
                        existing.intended_qty if existing else 0.0,
                        existing.price if existing else _coerce_float(
                            upd.get("limit_px")),
                        self._venue, cloid, oid, status.value,
                        0.0, None, None, reason, None,
                        HyperliquidAckSource.WSS.value, self._fp,
                        json.dumps({"src": "wss_order_update"},
                                   default=str),
                    ),
                )
                cur.execute(
                    self._insert_intent_sql,
                    (
                        coid, int(ts_ns), int(ts_ns),
                        existing.coin if existing else _coerce_str(
                            upd.get("coin")),
                        None,
                        existing.side if existing else None,
                        existing.intended_qty if existing else 0.0,
                        existing.price if existing else _coerce_float(
                            upd.get("limit_px")),
                        "LIMIT", self._default_tif, 0, cloid,
                        self._venue,
                        existing.hyperliquid_signature if existing
                        else "",
                        oid or (existing.venue_order_id if existing
                                else None),
                        status.value, int(ts_ns), self._fp,
                        json.dumps({"src": "wss_order_update"},
                                   default=str),
                    ),
                )
        self._journal.conn.commit()
        if coid is not None and existing is not None and (
            existing.status not in _TERMINAL_STATUSES
        ):
            self._intents[coid] = HyperliquidState(
                client_order_id=existing.client_order_id,
                coin=existing.coin,
                side=existing.side,
                intended_qty=existing.intended_qty,
                price=existing.price,
                venue=existing.venue,
                cloid=existing.cloid,
                venue_order_id=oid or existing.venue_order_id,
                hyperliquid_signature=existing.hyperliquid_signature,
                status=status,
                ts_ns=existing.ts_ns,
                updated_ts_ns=int(ts_ns),
                policy_fingerprint=existing.policy_fingerprint,
            )
        return ComponentResult(observation={
            "hyperliquid_wss": "order_update",
            "client_order_id": coid,
            "status": status.value,
        })

    def _apply_user_fill(
        self,
        fill: Mapping[str, Any],
        *,
        ts_ns: int,
    ) -> None:
        cloid = _coerce_str(fill.get("cloid"))
        oid = _coerce_str(fill.get("oid"))
        coid, existing = self._resolve_coid(cloid=cloid, oid=oid)
        if coid is None or existing is None:
            # A fill for an unknown intent is still journaled —
            # never silently drop fills.
            with closing(self._journal.conn.cursor()) as cur:
                cur.execute(
                    self._insert_event_sql,
                    (
                        int(ts_ns), coid or "unknown", "wss_userdata",
                        T_WS_UPDATE, oid,
                        json.dumps(
                            {"kind": "USER_FILL_UNMATCHED",
                             "cloid": cloid},
                            default=str,
                        ),
                        self._fp,
                    ),
                )
            self._journal.conn.commit()
            return
        sz = float(fill.get("sz") or 0.0)
        side = str(fill.get("side") or "").upper()
        signed_sz = -abs(sz) if side == "A" else abs(sz)
        px = _coerce_float(fill.get("px"))
        fee = _coerce_float(fill.get("fee"))
        with closing(self._journal.conn.cursor()) as cur:
            cur.execute(
                self._insert_event_sql,
                (
                    int(ts_ns), coid, "wss_userdata", T_WS_UPDATE,
                    oid,
                    json.dumps(
                        {
                            "kind": "USER_FILL",
                            "sz": signed_sz, "px": px, "fee": fee,
                            "crossed": bool(fill.get("crossed")),
                        },
                        default=str,
                    ),
                    self._fp,
                ),
            )
            cur.execute(
                self._upsert_ack_sql,
                (
                    coid, int(ts_ns), existing.coin, existing.side,
                    existing.intended_qty, existing.price,
                    self._venue, existing.cloid,
                    oid or existing.venue_order_id,
                    existing.status.value, signed_sz, px, fee,
                    None, None,
                    HyperliquidAckSource.WSS.value, self._fp,
                    json.dumps({"src": "wss_user_fill"}, default=str),
                ),
            )
        self._journal.conn.commit()


# ---------------------------------------------------------------------------
# Paper transport (mock /exchange responder — no network)
# ---------------------------------------------------------------------------


class HyperliquidPaperTransport:
    """Paper-trading transport for the hyperliquid adapter.

    Accepts the signed ``/exchange`` POST body (from
    :func:`sign_action` / :meth:`HyperliquidAdapter.build_signed_order`)
    and returns a deterministic Hyperliquid-shaped response
    envelope.  Per-cloid outcomes are configurable via
    ``fill_model``.  Never touches the network.

    The transport does NOT journal — the runner's hot path does.
    """

    @dataclass(frozen=True)
    class FillModel:
        """One scripted outcome.

        ``kind``: ``"resting"`` | ``"filled"`` | ``"error"`` |
        ``"envelope_err"`` (top-level ``status: err``).
        """

        kind: str = "filled"
        error: Optional[str] = None
        total_sz: Optional[float] = None
        avg_px: Optional[float] = None
        oid: Optional[int] = None

    def __init__(
        self,
        *,
        default_fill: "HyperliquidPaperTransport.FillModel" = None,
        fill_model: Optional[Dict[
            str, "HyperliquidPaperTransport.FillModel"]] = None,
    ) -> None:
        self.default_fill = default_fill or self.FillModel()
        self.fill_model = dict(fill_model or {})
        self.n_calls = 0
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, body: Mapping[str, Any]) -> Dict[str, Any]:
        self.n_calls += 1
        self.calls.append(dict(body))
        action = body.get("action") if isinstance(body, Mapping) else None
        orders = action.get("orders") if isinstance(action, Mapping) else []
        order = orders[0] if isinstance(orders, (list, tuple)) and orders \
            else {}
        cloid = _coerce_str(order.get("c"))
        model = self.fill_model.get(cloid or "", self.default_fill)
        oid = model.oid if model.oid is not None else 900000 + self.n_calls

        if model.kind == "envelope_err":
            return {"status": "err", "response": model.error or "OTHER"}
        if model.kind == "error":
            return {
                "status": "ok",
                "response": {
                    "type": "order",
                    "data": {"statuses": [
                        {"error": model.error or "OTHER"},
                    ]},
                },
            }
        if model.kind == "resting":
            return {
                "status": "ok",
                "response": {
                    "type": "order",
                    "data": {"statuses": [{"resting": {"oid": oid}}]},
                },
            }
        px = model.avg_px
        if px is None:
            px = _coerce_float(order.get("p")) or 0.0
        sz = model.total_sz
        if sz is None:
            sz = _coerce_float(order.get("s")) or 0.0
        return {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {"statuses": [{"filled": {
                    "totalSz": repr(float(sz)),
                    "avgPx": repr(float(px)),
                    "oid": oid,
                }}]},
            },
        }


# Module-level alias mirroring the perp sibling's pattern.
HyperliquidPaperTransportFillModel = HyperliquidPaperTransport.FillModel


__all__ = [
    "DEFAULT_HYPERLIQUID_ADAPTER_POLICY",
    "DEFAULT_VENUE",
    "HL_EXCHANGE_PATH",
    "HL_REST_MAINNET",
    "HL_REST_TESTNET",
    "HL_WS_MAINNET",
    "HL_WS_TESTNET",
    "IOC_NO_MATCH",
    "MARGIN_REJECTED",
    "OTHER",
    "POST_ONLY_WOULD_CROSS",
    "PRICE_BAND",
    "RATE_LIMITED",
    "SCHEMA_SQL",
    "HyperliquidAckSource",
    "HyperliquidAdapter",
    "HyperliquidAdapterPolicy",
    "HyperliquidPaperTransport",
    "HyperliquidPaperTransportFillModel",
    "HyperliquidSnapshot",
    "HyperliquidState",
    "HyperliquidStatus",
    "SignedEnvelope",
    "SignerFn",
    "T_ACK_OK",
    "T_ACK_REJECT",
    "T_BLOCKED",
    "T_INTENT_TAGGED",
    "T_VALIDATION_FAILED",
    "T_WS_UPDATE",
    "bootstrap_journal",
    "build_eip712_signable",
    "build_order_action",
    "build_order_wire_order",
    "classify_exchange_response",
    "derive_cloid",
    "is_valid_cloid",
    "map_order_update_status",
    "normalize_cloid",
    "parse_ws_message",
    "policy_fingerprint",
    "sign_action",
    "validate_hl_intent",
]
