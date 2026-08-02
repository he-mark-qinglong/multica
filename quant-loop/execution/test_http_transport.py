"""Tests for execution.http_transport — mock HTTP layer.

Verifies:
- Paper mode returns deterministic acks (no network)
- Live mode requires explicit api_key + api_secret
- Live mode requires env vars when env_key/env_secret are used
- Success POST returns parsed JSON ack
- 429 triggers retry with Retry-After honour
- 5xx triggers exponential backoff retry
- 4xx non-retryable returns error body
- Transient OSError triggers retry
- Exhausted retries raise HttpTransportError
- Binance perp/spot/HL cancel wire builders produce correct shapes
- Cancel/amend ack classifiers work
"""
from __future__ import annotations

import json
import os
import unittest
from typing import Any, Dict, Mapping, Optional
from unittest.mock import patch

from execution.http_transport import (
    HttpTransport,
    HttpTransportError,
    HttpTransportPolicy,
    HttpResponse,
    _default_http_post,
    _HttpError,
    _RateLimited,
    _TransientError,
)

# Venue wire builders
from execution.venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp import (
    build_perp_cancel_wire,
    build_perp_amend_wire,
    classify_perp_cancel_ack,
    BinancePerpStatus,
    sign_binance_perp_request,
)
from execution.venue_adapter_binance_spot.venue_adapter_binance_spot import (
    build_spot_cancel_wire,
    build_spot_amend_wire,
    classify_spot_cancel_ack,
    BinanceSpotStatus,
    sign_binance_spot_request,
)
from execution.venue_adapter_hyperliquid.venue_adapter_hyperliquid import (
    build_hl_cancel_action,
    build_hl_amend_action,
    classify_hl_cancel_response,
    HyperliquidStatus,
    derive_cloid,
)


# ---------------------------------------------------------------------------
# Paper mode
# ---------------------------------------------------------------------------


class TestPaperMode(unittest.TestCase):

    def setUp(self):
        self.t = HttpTransport.paper()

    def test_paper_new_order(self):
        ack = self.t({
            "client_order_id": "coid-1",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": 0.1,
            "price": 50000,
        })
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["status"], "FILLED")
        self.assertEqual(ack["clientOrderId"], "coid-1")

    def test_paper_cancel(self):
        ack = self.t({"action": "cancel", "client_order_id": "coid-1", "symbol": "BTCUSDT"})
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["status"], "CANCELED")

    def test_paper_amend(self):
        ack = self.t({
            "action": "amend",
            "client_order_id": "coid-1",
            "symbol": "BTCUSDT",
            "price": 51000,
            "qty": 0.2,
        })
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["status"], "NEW")


# ---------------------------------------------------------------------------
# Live mode credential enforcement
# ---------------------------------------------------------------------------


class TestLiveModeCredentials(unittest.TestCase):

    def test_live_requires_api_key(self):
        with self.assertRaises(ValueError) as ctx:
            HttpTransport.live(api_secret="secret")
        self.assertIn("api_key is required", str(ctx.exception))

    def test_live_requires_api_secret(self):
        with self.assertRaises(ValueError) as ctx:
            HttpTransport.live(api_key="key")
        self.assertIn("api_secret is required", str(ctx.exception))

    def test_live_reads_env_vars(self):
        with patch.dict(os.environ, {"TEST_KEY": "envkey", "TEST_SECRET": "envsecret"}):
            t = HttpTransport.live(env_key="TEST_KEY", env_secret="TEST_SECRET")
            self.assertEqual(t.api_key, "envkey")
            self.assertEqual(t.api_secret, "envsecret")
            self.assertFalse(t.paper)

    def test_live_env_missing_raises(self):
        # Ensure the env vars are not set.
        env = {k: v for k, v in os.environ.items()
               if k not in ("MISSING_KEY_XYZ", "MISSING_SECRET_XYZ")}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                HttpTransport.live(
                    env_key="MISSING_KEY_XYZ",
                    env_secret="MISSING_SECRET_XYZ",
                )


# ---------------------------------------------------------------------------
# Mock HTTP poster for live-mode tests
# ---------------------------------------------------------------------------


class _ScriptedPoster:
    """Returns scripted HttpResponses, recording calls."""

    def __init__(self, responses):
        """``responses`` is a list of HttpResponse or Exception instances."""
        self.responses = list(responses)
        self.calls = []

    def __call__(self, *, url, method, headers, body, timeout_s):
        self.calls.append({
            "url": url, "method": method, "headers": headers,
            "body": body, "timeout_s": timeout_s,
        })
        if not self.responses:
            raise AssertionError("no more scripted responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _ok_json(body: dict) -> HttpResponse:
    return HttpResponse(
        status=200,
        body=json.dumps(body).encode("utf-8"),
    )


def _err_json(code: int, msg: str) -> HttpResponse:
    return HttpResponse(
        status=code,
        body=json.dumps({"code": code, "msg": msg}).encode("utf-8"),
    )


class TestLiveModeHttp(unittest.TestCase):

    def _make_transport(self, poster):
        return HttpTransport(
            base_url="https://fapi.binance.com",
            default_path="/fapi/v1/order",
            api_key="testkey",
            api_secret="testsecret",
            signer=sign_binance_perp_request,
            paper=False,
            policy=HttpTransportPolicy(
                timeout_s=1.0,
                max_retries=3,
                backoff_base_s=0.0,  # no real sleeping
            ),
            _http_post=poster,
        )

    def test_success_post(self):
        poster = _ScriptedPoster([
            _ok_json({"symbol": "BTCUSDT", "orderId": 123, "status": "NEW"}),
        ])
        t = self._make_transport(poster)

        ack = t({
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "quantity": 0.1,
            "price": 50000,
            "timeInForce": "GTC",
        })

        self.assertTrue(ack["ok"])
        self.assertEqual(ack["status"], "NEW")
        self.assertEqual(ack["orderId"], 123)
        # Verify the URL includes the signed query string.
        self.assertIn("signature=", poster.calls[0]["url"])
        self.assertIn("X-MBX-APIKEY", poster.calls[0]["headers"])

    def test_429_retry_then_success(self):
        poster = _ScriptedPoster([
            _RateLimited(retry_after_s=0.0),
            _ok_json({"status": "NEW"}),
        ])
        t = self._make_transport(poster)

        ack = t({"symbol": "BTCUSDT", "quantity": 0.1, "price": 50000})

        self.assertTrue(ack["ok"])
        self.assertEqual(len(poster.calls), 2)

    def test_5xx_retry_then_success(self):
        poster = _ScriptedPoster([
            _TransientError("HTTP 503"),
            _ok_json({"status": "FILLED"}),
        ])
        t = self._make_transport(poster)

        ack = t({"symbol": "BTCUSDT", "quantity": 0.1, "price": 50000})

        self.assertTrue(ack["ok"])
        self.assertEqual(ack["status"], "FILLED")
        self.assertEqual(len(poster.calls), 2)

    def test_4xx_non_retryable_returns_error_body(self):
        poster = _ScriptedPoster([
            _HttpError(400, {"code": -1021, "msg": "timestamp error"}),
        ])
        t = self._make_transport(poster)

        ack = t({"symbol": "BTCUSDT", "quantity": 0.1, "price": 50000})

        # Non-retryable: the error body is returned as the ack.
        self.assertEqual(ack["code"], -1021)
        self.assertEqual(len(poster.calls), 1)

    def test_exhausted_retries_raises(self):
        poster = _ScriptedPoster([
            _TransientError("HTTP 503"),
            _TransientError("HTTP 503"),
            _TransientError("HTTP 503"),
            _TransientError("HTTP 503"),
        ])
        t = self._make_transport(poster)

        with self.assertRaises(HttpTransportError):
            t({"symbol": "BTCUSDT", "quantity": 0.1, "price": 50000})

    def test_oserror_retry(self):
        poster = _ScriptedPoster([
            OSError("connection reset"),
            _ok_json({"status": "NEW"}),
        ])
        t = self._make_transport(poster)

        ack = t({"symbol": "BTCUSDT", "quantity": 0.1, "price": 50000})

        self.assertTrue(ack["ok"])
        self.assertEqual(len(poster.calls), 2)

    def test_json_body_mode_for_hl(self):
        """HL requests with 'action' key use JSON body, no query-string signing."""
        poster = _ScriptedPoster([
            _ok_json({"status": "ok", "response": {"type": "cancel"}}),
        ])
        t = HttpTransport(
            base_url="https://api.hyperliquid.xyz",
            default_path="/exchange",
            api_key="hlkey",
            api_secret="hlsecret",
            signer=None,  # HL signs in the adapter, not here
            paper=False,
            policy=HttpTransportPolicy(max_retries=0, backoff_base_s=0.0),
            _http_post=poster,
        )

        ack = t({
            "action": {"type": "cancel", "cancels": [{"a": 0, "o": "123"}]},
            "nonce": 1700000000000,
            "signature": {"r": "0x1", "s": "0x2", "v": 27},
        })

        self.assertTrue(ack["ok"])
        self.assertIn("Content-Type", poster.calls[0]["headers"])
        self.assertEqual(poster.calls[0]["headers"]["Content-Type"], "application/json")
        # Body should be valid JSON.
        body = json.loads(poster.calls[0]["body"])
        self.assertEqual(body["action"]["type"], "cancel")


# ---------------------------------------------------------------------------
# Venue cancel/amend wire builders
# ---------------------------------------------------------------------------


class TestPerpCancelWire(unittest.TestCase):

    def test_basic_cancel_wire(self):
        wire = build_perp_cancel_wire({
            "client_order_id": "my-coid",
            "symbol": "BTCUSDT",
        })
        self.assertEqual(wire["symbol"], "BTCUSDT")
        self.assertEqual(wire["origClientOrderId"], "my-coid")

    def test_cancel_wire_missing_coid(self):
        with self.assertRaises(ValueError):
            build_perp_cancel_wire({"symbol": "BTCUSDT"})

    def test_cancel_wire_missing_symbol(self):
        with self.assertRaises(ValueError):
            build_perp_cancel_wire({"client_order_id": "x"})

    def test_cancel_wire_with_order_id(self):
        wire = build_perp_cancel_wire({
            "client_order_id": "x", "symbol": "BTCUSDT", "orderId": 12345,
        })
        self.assertEqual(wire["orderId"], 12345)

    def test_cancel_wire_signed(self):
        wire = build_perp_cancel_wire({
            "client_order_id": "my-coid", "symbol": "BTCUSDT",
        })
        signed = sign_binance_perp_request(wire, api_secret="testsecret")
        self.assertIn("signature", signed)
        self.assertIn("timestamp", signed)

    def test_classify_cancel_success(self):
        status, reason, code = classify_perp_cancel_ack({"status": "CANCELED"})
        self.assertEqual(status, BinancePerpStatus.CANCELED)
        self.assertIsNone(reason)

    def test_classify_cancel_unknown_order(self):
        status, reason, code = classify_perp_cancel_ack({"code": -2011, "msg": "x"})
        self.assertEqual(status, BinancePerpStatus.REJECTED)
        self.assertEqual(reason, "UNKNOWN_ORDER")

    def test_classify_cancel_rate_limited(self):
        status, reason, code = classify_perp_cancel_ack({"code": -1003, "msg": "x"})
        self.assertEqual(status, BinancePerpStatus.REJECTED)
        self.assertEqual(reason, "RATE_LIMITED")


class TestPerpAmendWire(unittest.TestCase):

    def test_basic_amend_wire(self):
        wire = build_perp_amend_wire({
            "client_order_id": "coid-1",
            "symbol": "BTCUSDT",
            "price": 51000,
            "qty": 0.15,
            "side": "BUY",
        })
        self.assertEqual(wire["price"], 51000)
        self.assertEqual(wire["quantity"], 0.15)
        self.assertEqual(wire["newClientOrderId"], "coid-1")
        self.assertEqual(wire["type"], "LIMIT")

    def test_amend_wire_requires_fields(self):
        with self.assertRaises(ValueError):
            build_perp_amend_wire({"client_order_id": "x", "symbol": "BTCUSDT"})


class TestSpotCancelWire(unittest.TestCase):

    def test_basic_cancel_wire(self):
        wire = build_spot_cancel_wire({
            "client_order_id": "spot-coid",
            "symbol": "ETHUSDT",
        })
        self.assertEqual(wire["symbol"], "ETHUSDT")
        self.assertEqual(wire["origClientOrderId"], "spot-coid")

    def test_cancel_wire_missing_coid(self):
        with self.assertRaises(ValueError):
            build_spot_cancel_wire({"symbol": "ETHUSDT"})

    def test_classify_cancel_success(self):
        status, reason, code = classify_spot_cancel_ack({"status": "CANCELED"})
        self.assertEqual(status, BinanceSpotStatus.CANCELED)

    def test_classify_cancel_unknown_order(self):
        status, reason, code = classify_spot_cancel_ack({"code": -2011, "msg": "x"})
        self.assertEqual(status, BinanceSpotStatus.REJECTED)
        self.assertEqual(reason, "UNKNOWN_ORDER")


class TestSpotAmendWire(unittest.TestCase):

    def test_basic_amend_wire(self):
        wire = build_spot_amend_wire({
            "client_order_id": "coid-s",
            "symbol": "ETHUSDT",
            "price": 3000,
            "qty": 1.0,
            "side": "SELL",
        })
        self.assertEqual(wire["price"], 3000)
        self.assertEqual(wire["quantity"], 1.0)
        self.assertEqual(wire["newClientOrderId"], "coid-s")


class TestHLCancelAction(unittest.TestCase):

    def test_cancel_by_cloid(self):
        action = build_hl_cancel_action({
            "client_order_id": "hl-coid",
            "symbol": "BTC",
            "asset": 0,
        })
        self.assertEqual(action["type"], "cancel")
        cancel = action["cancels"][0]
        self.assertEqual(cancel["a"], 0)
        self.assertIn("cloid", cancel)
        # cloid is derived from client_order_id
        self.assertEqual(cancel["cloid"], derive_cloid("hl-coid"))

    def test_cancel_by_oid(self):
        action = build_hl_cancel_action({
            "client_order_id": "hl-coid",
            "symbol": "BTC",
            "asset": 0,
            "venue_order_id": "12345",
            "cloid": None,
        })
        cancel = action["cancels"][0]
        self.assertEqual(cancel["o"], "12345")

    def test_cancel_resolves_asset_from_index(self):
        action = build_hl_cancel_action(
            {"client_order_id": "x", "symbol": "ETH"},
            asset_index={"ETH": 1},
        )
        self.assertEqual(action["cancels"][0]["a"], 1)

    def test_cancel_missing_asset_raises(self):
        with self.assertRaises(ValueError):
            build_hl_cancel_action({"client_order_id": "x", "symbol": "UNKNOWN"})

    def test_classify_cancel_success(self):
        resp = {
            "status": "ok",
            "response": {"type": "cancel", "data": {"statuses": ["success"]}},
        }
        status, reason, code = classify_hl_cancel_response(resp)
        self.assertEqual(status, HyperliquidStatus.CANCELED)

    def test_classify_cancel_error(self):
        resp = {
            "status": "ok",
            "response": {"type": "cancel", "data": {"statuses": [
                {"error": "order does not exist"},
            ]}},
        }
        status, reason, code = classify_hl_cancel_response(resp)
        self.assertEqual(status, HyperliquidStatus.REJECTED)

    def test_classify_envelope_error(self):
        resp = {"status": "err", "response": "rate limit exceeded"}
        status, reason, code = classify_hl_cancel_response(resp)
        self.assertEqual(status, HyperliquidStatus.REJECTED)
        self.assertEqual(reason, "RATE_LIMITED")


class TestHLAmendAction(unittest.TestCase):

    def test_amend_produces_order_action_with_same_cloid(self):
        action = build_hl_amend_action({
            "client_order_id": "hl-amend",
            "symbol": "BTC",
            "asset": 0,
            "price": 51000,
            "qty": 0.2,
            "side": "BUY",
        })
        self.assertEqual(action["type"], "order")
        order = action["orders"][0]
        self.assertEqual(order["a"], 0)
        self.assertEqual(order["b"], True)
        # Same cloid as derived from client_order_id
        self.assertEqual(order["c"], derive_cloid("hl-amend"))

    def test_amend_requires_price_or_qty(self):
        with self.assertRaises(ValueError):
            build_hl_amend_action({
                "client_order_id": "x", "symbol": "BTC", "asset": 0,
            })


# ---------------------------------------------------------------------------
# Integration: runner + HttpTransport.paper()
# ---------------------------------------------------------------------------


class TestRunnerWithPaperTransport(unittest.TestCase):

    def test_cancel_via_paper_http_transport(self):
        from execution.runner import ExecutionRunner, OrderJournal, OutboundTransport
        paper = HttpTransport.paper()
        runner = ExecutionRunner(
            journal=OrderJournal(":memory:"),
            transport=OutboundTransport(callable_send=paper),
        )

        ack = runner.cancel_order("coid-paper", symbol="BTCUSDT")

        self.assertEqual(ack["status"], "CANCELED")
        self.assertEqual(ack["clientOrderId"], "coid-paper")

    def test_amend_via_paper_http_transport(self):
        from execution.runner import ExecutionRunner, OrderJournal, OutboundTransport
        paper = HttpTransport.paper()
        runner = ExecutionRunner(
            journal=OrderJournal(":memory:"),
            transport=OutboundTransport(callable_send=paper),
        )

        ack = runner.amend_order("coid-paper", new_price=50000, new_qty=0.1, symbol="BTCUSDT")

        self.assertEqual(ack["status"], "NEW")


if __name__ == "__main__":
    unittest.main()
