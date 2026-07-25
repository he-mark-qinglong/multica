"""venue_adapter_binance_perp — P7-EXEC-003.

Binance USDT-M perpetual (futures) REST + WS venue adapter for the
live execution runner.  Sits between the canonical
:class:`execution.runner.ExecutionRunner` and the Binance ``fapi``
(USD-M futures) REST API + user-data WebSocket stream.

The component owns:

* HMAC-SHA256 signing of outbound REST requests (canonical query
  string per Binance's wire spec);
* pre-trade validation of perp-targeted intents — venue matches
  the policy (``binance_usdt_futures`` by default), symbol ends
  with ``USDT`` or ``USDC``, ``qty`` / ``price`` finite positive,
  ``time_in_force`` in the perp set;
* journaling every accepted intent + every received ack
  (REST + WS) into three additive tables
  (``binance_perp_intents`` / ``binance_perp_events`` /
  ``binance_perp_acks``);
* classification of REST acks (``NEW`` / ``PARTIALLY_FILLED`` /
  ``FILLED`` / ``CANCELED`` / ``EXPIRED`` / ``REJECTED``) and
  WS user-data ``ORDER_TRADE_UPDATE`` frames (via
  :class:`BinancePerpAdapter.apply_wss_event`);
* a durable user-data WebSocket consumer
  (:class:`BinancePerpWssConsumer`) that latches onto the
  userDataStream, folds ``ORDER_TRADE_UPDATE`` /
  ``ACCOUNT_UPDATE`` / ``listenKeyExpired`` events into the
  additive journal, and journals reconnect / disconnect
  transitions.

See :mod:`execution.venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp`
for the implementation, ``README.md`` for the spec, and
``INTERFACE.md`` for the deployment contract.

Folder convention: ``venue_adapter_binance_perp_p7exec_003/`` per
the MAP-P7 Live Trading Infrastructure project rule (suffix
``_p7exec_NNN``, never ``_v1`` / ``_v2``).
"""
from .venue_adapter_binance_perp import (
    DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
    DEFAULT_VENUE,
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
    BinancePerpAckSource,
    BinancePerpAdapter,
    BinancePerpAdapterPolicy,
    BinancePerpIntent,
    BinancePerpPaperTransport,
    BinancePerpPaperTransportFillModel,
    BinancePerpSnapshot,
    BinancePerpState,
    BinancePerpStatus,
    BinancePerpWssConsumer,
    BinancePerpWssSnapshot,
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

__all__ = [
    "DEFAULT_BINANCE_PERP_ADAPTER_POLICY",
    "DEFAULT_VENUE",
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
    "BinancePerpAckSource",
    "BinancePerpAdapter",
    "BinancePerpAdapterPolicy",
    "BinancePerpIntent",
    "BinancePerpPaperTransport",
    "BinancePerpPaperTransportFillModel",
    "BinancePerpSnapshot",
    "BinancePerpState",
    "BinancePerpStatus",
    "BinancePerpWssConsumer",
    "BinancePerpWssSnapshot",
    "BinancePerpWssState",
    "OutboundBinancePerpTransport",
    "bootstrap_journal",
    "classify_binance_perp_rest_ack",
    "parse_wss_userdata_message",
    "policy_fingerprint",
    "register_with_runner",
    "sign_binance_perp_request",
    "validate_perp_intent",
]

__version__ = "0.1.0"
__issue__ = "SMA-36190"
