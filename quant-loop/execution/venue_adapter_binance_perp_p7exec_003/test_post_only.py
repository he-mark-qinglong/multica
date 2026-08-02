"""Tests for post_only (E7) — Binance USD-M GTX helpers, offline.

Covers:

* is_post_only_tif / normalize_post_only_tif (alias handling);
* validate_post_only_intent (LIMIT + positive price + explicit
  post-only TIF; rejects GTC / MARKET / missing price);
* build_gtx_order_wire (canonical GTX on the wire, LIMIT_MAKER
  normalised, reduceOnly passthrough);
* classify_gtx_ack (EXPIRED_IN_MATCH -> EXPIRED /
  POST_ONLY_WOULD_CROSS; all other acks delegate to the parent
  classifier).

Run:
    python3 -m pytest execution/venue_adapter_binance_perp_p7exec_003/test_post_only.py -q
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest  # noqa: E402

from execution.venue_adapter_binance_perp_p7exec_003.post_only import (  # noqa: E402
    GTX,
    POST_ONLY_WOULD_CROSS,
    build_gtx_order_wire,
    classify_gtx_ack,
    is_post_only_tif,
    normalize_post_only_tif,
    validate_post_only_intent,
)
from execution.venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp import (  # noqa: E402
    BinancePerpStatus,
)


def _req(**overrides):
    req = {
        "venue": "binance_usdt_futures",
        "client_order_id": "coid-gtx-1",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": 0.05,
        "price": 50000.0,
        "order_type": "LIMIT",
        "time_in_force": "GTX",
    }
    req.update(overrides)
    return req


# -- is_post_only_tif / normalize -------------------------------------------


def test_is_post_only_tif():
    assert is_post_only_tif("GTX")
    assert is_post_only_tif("gtx")
    assert is_post_only_tif("LIMIT_MAKER")
    assert not is_post_only_tif("GTC")
    assert not is_post_only_tif("IOC")
    assert not is_post_only_tif(None)
    assert not is_post_only_tif("")


def test_normalize_post_only_tif():
    assert normalize_post_only_tif("GTX") == "GTX"
    assert normalize_post_only_tif("LIMIT_MAKER") == "GTX"
    assert normalize_post_only_tif("gtx") == "GTX"
    with pytest.raises(ValueError):
        normalize_post_only_tif("GTC")


# -- validate_post_only_intent ----------------------------------------------


def test_validate_happy_path():
    ok, reason = validate_post_only_intent(_req())
    assert ok, reason


def test_validate_limit_maker_alias_accepted():
    ok, reason = validate_post_only_intent(_req(time_in_force="LIMIT_MAKER"))
    assert ok, reason


def test_validate_rejects_non_post_only_tif():
    ok, reason = validate_post_only_intent(_req(time_in_force="GTC"))
    assert not ok
    assert "not_post_only" in reason
    ok, reason = validate_post_only_intent(_req(time_in_force="IOC"))
    assert not ok


def test_validate_rejects_missing_tif():
    ok, reason = validate_post_only_intent(_req(time_in_force=None))
    assert not ok
    assert reason == "post_only_requires_explicit_gtx_tif"


def test_validate_rejects_market_and_bad_price():
    ok, reason = validate_post_only_intent(
        _req(order_type="MARKET", price=None),
    )
    assert not ok
    assert "post_only_requires_limit" in reason or "price" in reason

    ok, reason = validate_post_only_intent(_req(price=0.0))
    assert not ok
    assert "price" in reason


def test_validate_rejects_non_perp_venue():
    ok, reason = validate_post_only_intent(_req(venue="binance_spot"))
    assert not ok
    assert "venue_not_perp" in reason


# -- build_gtx_order_wire ----------------------------------------------------


def test_build_wire_canonical_gtx():
    order = build_gtx_order_wire(_req())
    wire = order.wire
    assert wire["timeInForce"] == "GTX"
    assert wire["type"] == "LIMIT"
    assert wire["symbol"] == "BTCUSDT"
    assert wire["side"] == "BUY"
    assert wire["quantity"] == 0.05
    assert wire["price"] == 50000.0
    assert wire["newClientOrderId"] == "coid-gtx-1"
    assert wire["newOrderRespType"] == "RESULT"
    assert order.time_in_force == GTX


def test_build_wire_normalizes_limit_maker_alias():
    order = build_gtx_order_wire(_req(time_in_force="LIMIT_MAKER"))
    assert order.wire["timeInForce"] == "GTX"


def test_build_wire_reduce_only_passthrough():
    order = build_gtx_order_wire(_req(reduce_only=True, side="SELL"))
    assert order.wire["reduceOnly"] is True
    order2 = build_gtx_order_wire(_req())
    assert "reduceOnly" not in order2.wire


def test_build_wire_invalid_raises():
    with pytest.raises(ValueError):
        build_gtx_order_wire(_req(time_in_force="GTC"))


def test_gtx_wire_order_frozen():
    order = build_gtx_order_wire(_req())
    with pytest.raises(Exception):
        order.qty = 1.0  # type: ignore[misc]


# -- classify_gtx_ack --------------------------------------------------------


def test_classify_expired_in_match_is_post_only_cross():
    ack = {
        "status": "EXPIRED_IN_MATCH",
        "orderId": 12345,
        "side": "BUY",
        "executedQty": "0",
    }
    (status, filled, avg_price, commission, oid, reason,
     err) = classify_gtx_ack(ack)
    assert status == BinancePerpStatus.EXPIRED
    assert filled == 0.0
    assert avg_price is None
    assert commission is None
    assert oid == "12345"
    assert reason == POST_ONLY_WOULD_CROSS
    assert err is None


def test_classify_filled_delegates():
    ack = {
        "status": "FILLED",
        "orderId": 99,
        "side": "SELL",
        "executedQty": "0.05",
        "avgPrice": "50000.0",
    }
    status, filled, *_ = classify_gtx_ack(ack)
    assert status == BinancePerpStatus.FILLED
    assert filled == -0.05  # signed per SELL convention


def test_classify_error_code_delegates():
    ack = {"code": -2010, "msg": "Account has insufficient balance"}
    status, _, _, _, _, reason, err = classify_gtx_ack(ack)
    assert status == BinancePerpStatus.REJECTED
    assert reason == "INSUFFICIENT_MARGIN"
    assert err == "-2010"
