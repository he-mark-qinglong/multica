"""venue_adapter_binance_spot — Binance spot REST venue adapter (E2, E8).

Spot counterpart of the perp adapter
(``execution.venue_adapter_binance_perp_p7exec_003``): HMAC-SHA256
signing, ``POST /api/v3/order`` wire building, ack classification,
additive ``binance_spot_intents`` / ``binance_spot_events`` /
``binance_spot_acks`` journaling, post-only via ``LIMIT_MAKER``, and
IOC / FOK time-in-force support with TIF-aware paper semantics.

The module duck-types the order journal (any object with a ``.conn``
SQLite connection) and falls back to local ``ComponentResult`` /
``BlockReason`` definitions when the canonical ``execution.runner``
is unavailable, so the full test-suite runs offline.
"""
from .venue_adapter_binance_spot import (
    DEFAULT_BINANCE_SPOT_ADAPTER_POLICY,
    DEFAULT_VENUE,
    SCHEMA_SQL,
    SPOT_ORDER_PATH,
    SPOT_REST_BASE,
    T_ACK_OK,
    T_ACK_PARTIAL,
    T_ACK_REJECT,
    T_BLOCKED,
    T_INTENT_TAGGED,
    T_VALIDATION_FAILED,
    BinanceSpotAdapter,
    BinanceSpotAdapterPolicy,
    BinanceSpotAckSource,
    BinanceSpotPaperTransport,
    BinanceSpotPaperTransportFillModel,
    BinanceSpotSnapshot,
    BinanceSpotState,
    BinanceSpotStatus,
    OutboundBinanceSpotTransport,
    bootstrap_journal,
    build_spot_order_wire,
    classify_binance_spot_rest_ack,
    policy_fingerprint,
    sign_binance_spot_request,
    validate_spot_intent,
)

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
