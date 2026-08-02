"""post_only — Binance USD-M post-only (GTX) order helpers (E7).

Additive companion module to
:mod:`execution.venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp`.
That adapter accepts ``GTX`` / ``LIMIT_MAKER`` in its TIF whitelist
but ships no dedicated post-only order builder or post-only ack
semantics; this module closes that gap **without touching the
existing module** (project rule: new functionality lives in new
modules).

Public surface
--------------
* :func:`is_post_only_tif` — GTX / LIMIT_MAKER detection
  (``LIMIT_MAKER`` is the legacy alias of ``GTX`` on USD-M).
* :func:`normalize_post_only_tif` — canonicalise any post-only
  alias to ``GTX``.
* :func:`validate_post_only_intent` — pure validator: LIMIT order,
  positive price, TIF GTX (or alias), plus the full perp intent
  rules of the parent module.
* :func:`build_gtx_order_wire` — pure builder producing the
  Binance ``/fapi/v1/order`` wire params for a post-only LIMIT
  order (``timeInForce=GTX``).
* :class:`GtxWireOrder` — frozen view of the built wire order.
* :func:`classify_gtx_ack` — post-only-aware ack classifier.
  Wraps the parent's REST classifier and maps the
  ``EXPIRED_IN_MATCH`` venue status (post-only order that would
  have crossed) to an ``EXPIRED`` outcome with reason
  ``POST_ONLY_WOULD_CROSS`` instead of the parent's generic
  ``OTHER:unknown_status:EXPIRED_IN_MATCH``.

Wire protocol notes
-------------------
On Binance USD-M, post-only is a *time-in-force*: ``GTX`` ("Good
Till Crossing").  A GTX order rests on the book like GTC; if it
would immediately match a resting order the venue kills it with
status ``EXPIRED_IN_MATCH`` and zero fill.  ``LIMIT_MAKER`` is
accepted by the venue as a legacy alias; this module always emits
the canonical ``GTX`` on the wire.

References
----------
- Binance USD-M Futures API docs — ``POST /fapi/v1/order``,
  ``timeInForce`` semantics (GTX), ``EXPIRED_IN_MATCH`` status.
- Parent module:
  :mod:`execution.venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp`
  (validation rules, sign convention, ack taxonomy).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from execution.venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp import (  # noqa: E501
    DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
    BinancePerpAdapterPolicy,
    BinancePerpStatus,
    classify_binance_perp_rest_ack,
    validate_perp_intent,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical post-only TIF on Binance USD-M.
GTX = "GTX"

# Legacy alias accepted by the venue (and by the parent adapter's
# TIF whitelist); normalised to GTX on the wire.
LIMIT_MAKER_ALIAS = "LIMIT_MAKER"

_POST_ONLY_TIFS = frozenset({GTX, LIMIT_MAKER_ALIAS})

# Reject-reason label emitted when the venue kills a GTX order
# that would have crossed (EXPIRED_IN_MATCH).
POST_ONLY_WOULD_CROSS = "POST_ONLY_WOULD_CROSS"

_VENUE_STATUS_EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"


# ---------------------------------------------------------------------------
# Frozen views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GtxWireOrder:
    """Immutable view of a built post-only wire order.

    ``wire`` carries the exact Binance ``/fapi/v1/order`` params
    (unsigned — signing is the transport's job, see the parent
    module's :func:`sign_binance_perp_request`).
    """

    client_order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    time_in_force: str
    venue: str
    wire: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "price": self.price,
            "time_in_force": self.time_in_force,
            "venue": self.venue,
            "wire": dict(self.wire),
        }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def is_post_only_tif(time_in_force: Any) -> bool:
    """True when ``time_in_force`` is a post-only TIF (GTX or the
    LIMIT_MAKER legacy alias).  Pure."""
    if time_in_force is None:
        return False
    return str(time_in_force).strip().upper() in _POST_ONLY_TIFS


def normalize_post_only_tif(time_in_force: Any) -> str:
    """Canonicalise a post-only TIF to ``GTX``.

    Raises :class:`ValueError` for any non-post-only TIF — a GTC /
    IOC / FOK order is not post-only and must not be silently
    re-tagged.
    """
    if not is_post_only_tif(time_in_force):
        raise ValueError(
            f"normalize_post_only_tif: not a post-only TIF: "
            f"{time_in_force!r}"
        )
    return GTX


def validate_post_only_intent(
    request: Mapping[str, Any],
    policy: BinancePerpAdapterPolicy = DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
) -> Tuple[bool, str]:
    """Validate a post-only (GTX) perp intent.  Pure.

    Runs the parent module's :func:`validate_perp_intent` first
    (venue, symbol, side, qty, order-type whitelist), then enforces
    the post-only-specific rules:

      1. ``order_type`` must be ``LIMIT`` (post-only is meaningless
         on MARKET / stop types);
      2. ``price`` finite and positive (a post-only order must
         quote a price);
      3. ``time_in_force`` must be GTX or its LIMIT_MAKER alias —
         an explicit GTC / IOC / FOK is a hard error here even
         though the parent validator would accept it.
    """
    is_valid, reason = validate_perp_intent(request, policy)
    if not is_valid:
        return (False, reason)

    order_type = (
        str(request.get("order_type") or "LIMIT").strip().upper()
    )
    if order_type != "LIMIT":
        return (False, f"post_only_requires_limit:{order_type}")

    try:
        price = float(request.get("price"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return (False, "price_missing")
    if not math.isfinite(price) or price <= 0.0:
        return (False, f"price_non_positive:{price:.6f}")

    tif_raw = request.get("time_in_force")
    if tif_raw is None:
        return (False, "post_only_requires_explicit_gtx_tif")
    if not is_post_only_tif(tif_raw):
        return (
            False,
            f"time_in_force_not_post_only:{str(tif_raw).upper()}",
        )
    return (True, "")


def build_gtx_order_wire(
    request: Mapping[str, Any],
    policy: BinancePerpAdapterPolicy = DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
) -> GtxWireOrder:
    """Build the Binance ``/fapi/v1/order`` wire params for a
    post-only LIMIT order.  Pure; unsigned.

    The emitted wire always carries ``timeInForce=GTX`` (the
    LIMIT_MAKER alias is normalised away) and
    ``newOrderRespType=RESULT`` so the ack includes fill details.
    Raises :class:`ValueError` on an invalid intent.
    """
    is_valid, reason = validate_post_only_intent(request, policy)
    if not is_valid:
        raise ValueError(f"build_gtx_order_wire: invalid intent: {reason}")

    coid = str(request.get("client_order_id"))
    symbol = str(request.get("symbol")).strip().upper()
    side = str(request.get("side")).strip().upper()
    qty = abs(float(request.get("qty")))  # type: ignore[arg-type]
    price = float(request.get("price"))  # type: ignore[arg-type]
    tif = normalize_post_only_tif(request.get("time_in_force"))

    wire: Dict[str, Any] = {
        "newClientOrderId": coid,
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "timeInForce": tif,
        "quantity": qty,
        "price": price,
        "newOrderRespType": request.get("newOrderRespType") or "RESULT",
    }
    if request.get("reduceOnly") is not None or (
        request.get("reduce_only") is not None
    ):
        wire["reduceOnly"] = bool(
            request.get("reduceOnly", request.get("reduce_only"))
        )
    return GtxWireOrder(
        client_order_id=coid,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        time_in_force=tif,
        venue=policy.venue,
        wire=wire,
    )


def classify_gtx_ack(
    ack: Mapping[str, Any],
) -> Tuple[BinancePerpStatus, float, Optional[float], Optional[float],
           Optional[str], Optional[str], Optional[str]]:
    """Classify a GTX order ack with post-only semantics.  Pure.

    Delegates to the parent's
    :func:`classify_binance_perp_rest_ack` for every case except
    one: the venue status ``EXPIRED_IN_MATCH`` — a post-only order
    killed because it would have crossed — which the parent maps
    to ``REJECTED`` / ``OTHER:unknown_status:...``.  Here it maps
    to ``EXPIRED`` with reason :data:`POST_ONLY_WOULD_CROSS` and
    zero fill, mirroring the spot sibling's LIMIT_MAKER handling.

    Returns the same 7-tuple as the parent classifier:
    ``(status, filled_qty, avg_price, commission, venue_order_id,
    reject_reason, error_code)``.
    """
    raw_status = str(ack.get("status") or "").strip().upper()
    if raw_status == _VENUE_STATUS_EXPIRED_IN_MATCH:
        venue_order_id = ack.get("orderId")
        venue_order_id_s: Optional[str] = None
        if venue_order_id is not None:
            try:
                venue_order_id_s = str(int(str(venue_order_id)))
            except (TypeError, ValueError):
                venue_order_id_s = str(venue_order_id)
        return (
            BinancePerpStatus.EXPIRED,
            0.0,
            None,
            None,
            venue_order_id_s,
            POST_ONLY_WOULD_CROSS,
            None,
        )
    return classify_binance_perp_rest_ack(ack)


__all__ = [
    "GTX",
    "LIMIT_MAKER_ALIAS",
    "POST_ONLY_WOULD_CROSS",
    "GtxWireOrder",
    "build_gtx_order_wire",
    "classify_gtx_ack",
    "is_post_only_tif",
    "normalize_post_only_tif",
    "validate_post_only_intent",
]
