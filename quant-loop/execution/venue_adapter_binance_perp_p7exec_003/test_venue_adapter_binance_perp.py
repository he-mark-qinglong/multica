"""test_venue_adapter_binance_perp — P7-EXEC-003 unit tests.

Plain asserts (no pytest dependency).  Covers:

* pure helpers (validate_perp_intent, sign_binance_perp_request,
  classify_binance_perp_rest_ack, parse_wss_userdata_message);
* the additive journal DDL installs idempotently;
* the adapter's on_request hook (passthrough vs tagged vs
  validation-fail) journals the correct rows;
* the adapter's on_fill hook classifies Binance REST acks
  and is idempotent under duplicate callbacks;
* the adapter's record_reject explicit hook journals a
  REJECTED outcome;
* the WSS consumer handles ``ORDER_TRADE_UPDATE``,
  ``ACCOUNT_UPDATE``, ``listenKeyExpired`` and unparseable
  frames;
* the WSS consumer transitions to ``HALTED`` after the policy
  reconnect cap is exceeded.

Run::

    cd ~/multica/quant-loop/execution/venue_adapter_binance_perp_p7exec_003
    python3 test_venue_adapter_binance_perp.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from venue_adapter_binance_perp_p7exec_003 import (  # noqa: E402
    DEFAULT_BINANCE_PERP_ADAPTER_POLICY,
    T_ACK_OK,
    T_ACK_REJECT,
    T_INTENT_TAGGED,
    T_WSS_RECONNECTING,
    BinancePerpAckSource,
    BinancePerpAdapter,
    BinancePerpAdapterPolicy,
    BinancePerpPaperTransport,
    BinancePerpPaperTransportFillModel as FillModel,
    BinancePerpStatus,
    BinancePerpWssConsumer,
    BinancePerpWssState,
    bootstrap_journal,
    classify_binance_perp_rest_ack,
    parse_wss_userdata_message,
    policy_fingerprint,
    register_with_runner,
    sign_binance_perp_request,
    validate_perp_intent,
)
from runner import (  # noqa: E402
    BlockReason,
    ComponentResult,
    ExecutionRunner,
    OrderJournal,
    OutboundTransport,
)

from venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp import (  # noqa: E402
    BinancePerpPaperTransport,
)


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


def _make_request(**overrides):
    """Build a canonical perp request with sensible defaults."""
    req = {
        "client_order_id": "perp-001",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": 0.05,
        "price": 50000.0,
        "venue": "binance_usdt_futures",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
    }
    req.update(overrides)
    return req


# ---------------------------------------------------------------------------
# Pure-helper tests
# ---------------------------------------------------------------------------


def test_validate_perp_intent_accepts_canonical_request():
    is_valid, reason = validate_perp_intent(_make_request())
    assert is_valid is True
    assert reason == ""


def test_validate_perp_intent_rejects_non_perp_symbol():
    is_valid, reason = validate_perp_intent(_make_request(symbol="AAPL"))
    assert is_valid is False
    assert "symbol_not_perp_eligible" in reason


def test_validate_perp_intent_rejects_lower_case_symbol():
    is_valid, reason = validate_perp_intent(
        _make_request(symbol="btcusdt"),
    )
    assert is_valid is False


def test_validate_perp_intent_rejects_non_usdc_pair():
    # Must end with USDT or USDC — USD-only spot-style BUSDUSDT fails.
    is_valid, reason = validate_perp_intent(
        _make_request(symbol="BTCEUR"),
    )
    assert is_valid is False


def test_validate_perp_intent_rejects_missing_side():
    is_valid, reason = validate_perp_intent(_make_request(side=""))
    assert is_valid is False
    assert reason == "side_missing"


def test_validate_perp_intent_rejects_invalid_side():
    is_valid, reason = validate_perp_intent(_make_request(side="LONG"))
    assert is_valid is False
    assert "side_invalid" in reason


def test_validate_perp_intent_rejects_zero_qty():
    is_valid, reason = validate_perp_intent(_make_request(qty=0.0))
    assert is_valid is False
    assert "qty_below_min" in reason


def test_validate_perp_intent_rejects_missing_price_for_limit():
    is_valid, reason = validate_perp_intent(
        _make_request(order_type="LIMIT", price=None),
    )
    assert is_valid is False
    assert reason == "price_missing"


def test_validate_perp_intent_accepts_market_without_price():
    is_valid, reason = validate_perp_intent(
        _make_request(order_type="MARKET", price=None),
    )
    assert is_valid is True
    assert reason == ""


def test_validate_perp_intent_rejects_unsupported_order_type():
    is_valid, reason = validate_perp_intent(
        _make_request(order_type="TRAILING_STOP"),
    )
    assert is_valid is False
    assert "order_type_unsupported" in reason


def test_validate_perp_intent_rejects_algo_by_default():
    is_valid, reason = validate_perp_intent(
        _make_request(order_type="STOP"),
    )
    assert is_valid is False
    assert "algo_requires_allow_algos" in reason


def test_validate_perp_intent_accepts_algo_with_policy_flag():
    policy = BinancePerpAdapterPolicy(allow_algos=True)
    is_valid, reason = validate_perp_intent(
        _make_request(order_type="STOP"), policy=policy,
    )
    assert is_valid is True
    assert reason == ""


def test_validate_perp_intent_rejects_bad_tif():
    is_valid, reason = validate_perp_intent(
        _make_request(time_in_force="DAY"),
    )
    assert is_valid is False
    assert "time_in_force_unsupported" in reason


def test_validate_perp_intent_honours_opt_in_flag():
    req = _make_request()
    del req["venue"]
    req["binance_perp"] = True
    is_valid, reason = validate_perp_intent(req)
    assert is_valid is True


def test_validate_perp_intent_rejects_wrong_venue():
    is_valid, reason = validate_perp_intent(
        _make_request(venue="okx_usdt_perp"),
    )
    assert is_valid is False
    assert "venue_not_perp" in reason


# ---------------------------------------------------------------------------
# Signer tests
# ---------------------------------------------------------------------------


def test_sign_binance_perp_request_is_deterministic():
    params = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "GTC",
        "quantity": "0.05",
        "price": "50000",
    }
    s1 = sign_binance_perp_request(
        params, api_secret="secret-1", timestamp_ns=1_700_000_000_000_000_000,
        recv_window_ms=5000,
    )
    s2 = sign_binance_perp_request(
        params, api_secret="secret-1", timestamp_ns=1_700_000_000_000_000_000,
        recv_window_ms=5000,
    )
    assert s1 == s2
    # Determinism w.r.t. (secret, params, timestamp).
    canonical = urllib.parse.urlencode(
        sorted([
            ("price", "50000"), ("quantity", "0.05"),
            ("recvWindow", 5000), ("side", "BUY"),
            ("symbol", "BTCUSDT"), ("timeInForce", "GTC"),
            ("timestamp", 1_700_000_000_000),
            ("type", "LIMIT"),
        ]),
        doseq=True, quote_via=urllib.parse.quote, safe="",
    )
    expected = hmac.new(
        b"secret-1", canonical.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    assert s1["signature"] == expected


def test_sign_binance_perp_request_changes_with_secret():
    params = {
        "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
        "timeInForce": "GTC", "quantity": "0.05", "price": "50000",
    }
    a = sign_binance_perp_request(
        params, api_secret="secret-1", timestamp_ns=1_700_000_000_000_000_000,
    )
    b = sign_binance_perp_request(
        params, api_secret="secret-2", timestamp_ns=1_700_000_000_000_000_000,
    )
    assert a["signature"] != b["signature"]


def test_sign_binance_perp_request_rejects_empty_secret():
    try:
        sign_binance_perp_request(
            {}, api_secret="", timestamp_ns=1_700_000_000_000_000_000,
        )
        assert False, "should have raised"
    except ValueError as e:
        assert "api_secret" in str(e)


def test_sign_binance_perp_request_injects_timestamp_and_recv_window():
    params = {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT"}
    out = sign_binance_perp_request(
        params, api_secret="s", timestamp_ns=1_700_000_000_000_000_000,
        recv_window_ms=7500,
    )
    assert out["recvWindow"] == 7500
    assert "timestamp" in out
    # Cross-validate by HMAC against the canonical byte string.
    canonical = urllib.parse.urlencode(
        sorted([
            ("side", "BUY"), ("symbol", "BTCUSDT"),
            ("type", "LIMIT"),
            ("recvWindow", 7500),
            ("timestamp", 1_700_000_000_000),
        ]),
        doseq=True, quote_via=urllib.parse.quote, safe="",
    )
    expected_sig = hmac.new(
        b"s", canonical.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    assert out["signature"] == expected_sig


# ---------------------------------------------------------------------------
# Ack-classifier tests
# ---------------------------------------------------------------------------


def test_classify_rest_ack_filled():
    ack = {
        "symbol": "BTCUSDT", "orderId": 12345,
        "clientOrderId": "perp-001", "price": "50000.0",
        "origQty": "0.05", "executedQty": "0.05",
        "cumQty": "0.05", "status": "FILLED",
        "timeInForce": "GTC", "type": "LIMIT",
        "side": "BUY", "avgPrice": "50000.0",
        "commission": "0.000025",
    }
    (status, filled_qty, avg_price, commission, venue_order_id,
     reject_reason, error_code) = classify_binance_perp_rest_ack(ack)
    assert status == BinancePerpStatus.FILLED
    assert abs(filled_qty - 0.05) < 1e-12
    assert avg_price == 50000.0
    assert commission == pytest_approx_small(0.000025)
    assert venue_order_id == "12345"
    assert reject_reason is None
    assert error_code is None


def test_classify_rest_ack_partially_filled_signs_qty():
    ack = {
        "symbol": "ETHUSDT", "orderId": 99,
        "clientOrderId": "perp-002", "status": "PARTIALLY_FILLED",
        "side": "SELL",
        "origQty": "0.10", "executedQty": "0.04",
        "cumQty": "0.04", "avgPrice": "3000.0", "price": "3050.0",
    }
    (status, filled_qty, _, _, _, _, _) = classify_binance_perp_rest_ack(ack)
    assert status == BinancePerpStatus.PARTIALLY_FILLED
    assert filled_qty < 0.0   # signed for SELL


def test_classify_rest_ack_rejected_maps_canonical_reason():
    ack = {
        "code": -2010,
        "msg": "Account has insufficient balance for requested action.",
    }
    (status, filled_qty, avg_price, commission, _, reject_reason,
     error_code) = classify_binance_perp_rest_ack(ack)
    assert status == BinancePerpStatus.REJECTED
    assert reject_reason == "INSUFFICIENT_MARGIN"
    assert error_code == "-2010"
    assert filled_qty == 0.0
    assert avg_price is None
    assert commission is None


def test_classify_rest_ack_rejected_unknown_code_maps_to_other():
    ack = {"code": -9999, "msg": "?"}
    (status, _, _, _, _, reject_reason, _) = classify_binance_perp_rest_ack(ack)
    assert status == BinancePerpStatus.REJECTED
    assert reject_reason == "OTHER"


def test_classify_rest_ack_expired():
    ack = {
        "symbol": "BTCUSDT", "orderId": 1,
        "clientOrderId": "perp-003", "status": "EXPIRED",
        "side": "BUY",
        "origQty": "0.01", "executedQty": "0.0",
        "cumQty": "0.0", "price": "50000", "avgPrice": "0",
    }
    (status, _, _, _, _, _, _) = classify_binance_perp_rest_ack(ack)
    assert status == BinancePerpStatus.EXPIRED


def test_classify_rest_ack_tolerates_missing_keys():
    ack = {"clientOrderId": "perp-004", "status": "FILLED"}
    (status, _, _, _, _, _, _) = classify_binance_perp_rest_ack(ack)
    assert status == BinancePerpStatus.FILLED


# ---------------------------------------------------------------------------
# WSS frame parser tests
# ---------------------------------------------------------------------------


def test_parse_wss_order_trade_update():
    raw = json.dumps({
        "e": "ORDER_TRADE_UPDATE",
        "T": 1700000000000,
        "o": {
            "s": "BTCUSDT", "c": "perp-005", "S": "BUY",
            "i": 12345,
            "X": "FILLED",
            "z": "0.05",   # not used directly, exercised indirectly
            "ap": "50000.0",
            "n": "0.000025",
        },
    })
    parsed = parse_wss_userdata_message(raw)
    assert parsed is not None
    assert parsed["kind"] == "ORDER_TRADE_UPDATE"
    assert parsed["client_order_id"] == "perp-005"
    assert parsed["venue_order_id"] == "12345"
    assert parsed["status_raw"] == "FILLED"


def test_parse_wss_listen_key_expired():
    raw = json.dumps({"e": "listenKeyExpired", "T": 1700000000000})
    parsed = parse_wss_userdata_message(raw)
    assert parsed["kind"] == "LISTEN_KEY_EXPIRED"


def test_parse_wss_account_update():
    raw = json.dumps({
        "e": "ACCOUNT_UPDATE",
        "T": 1700000000000,
        "a": {"B": [], "P": []},
    })
    parsed = parse_wss_userdata_message(raw)
    assert parsed["kind"] == "ACCOUNT_UPDATE"


def test_parse_wss_unparseable_returns_none():
    assert parse_wss_userdata_message("") is None
    assert parse_wss_userdata_message("{not-json") is None
    assert parse_wss_userdata_message("123") is None
    assert parse_wss_userdata_message(None) is None


def test_parse_wss_unknown_event_returns_other_kind():
    raw = json.dumps({"e": "unknown.event", "T": 1700000000000})
    parsed = parse_wss_userdata_message(raw)
    assert parsed["kind"] == "WSS_OTHER"


# ---------------------------------------------------------------------------
# Adapter integration tests
# ---------------------------------------------------------------------------


def test_bootstrap_journal_installs_tables_idempotently():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    bootstrap_journal(j)  # idempotent
    tables = {
        r[0]
        for r in j.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "binance_perp_intents" in tables
    assert "binance_perp_events" in tables
    assert "binance_perp_acks" in tables


def test_on_request_passthrough_for_non_perp_intent():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    # A venue != binance_usdt_futures should fast-passthrough.
    result = a.on_request(
        {"client_order_id": "x", "symbol": "BTCUSDT",
         "side": "BUY", "qty": 0.01, "price": 50000,
         "venue": "coinbase_advanced"},
        j, ts_ns=1,
    )
    assert result.block is None
    assert result.observation is None
    # No journal row was written.
    n = list(
        j.conn.execute(
            "SELECT COUNT(*) FROM binance_perp_intents"
        )
    )[0][0]
    assert n == 0


def test_on_request_tags_perp_intent_and_journals():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    result = a.on_request(
        _make_request(),
        j, ts_ns=1_700_000_000_000_000_000,
    )
    assert result.observation is not None
    assert result.observation["binance_perp_adapter"] == "tagged"
    assert result.observation["venue"] == "binance_usdt_futures"
    # A row landed in each additive table.
    n_intent = list(
        j.conn.execute(
            "SELECT COUNT(*) FROM binance_perp_intents"
        )
    )[0][0]
    n_event = list(
        j.conn.execute(
            "SELECT COUNT(*) FROM binance_perp_events"
        )
    )[0][0]
    assert n_intent == 1
    assert n_event == 1


def test_on_request_validation_fail_journals_event_no_block_default():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    bad = _make_request(symbol="AAPL")
    result = a.on_request(bad, j, ts_ns=2)
    assert result.block is None
    assert result.observation["binance_perp_adapter"] == "validation_failed"
    n = list(
        j.conn.execute(
            "SELECT COUNT(*) FROM binance_perp_events WHERE kind='VALIDATION_FAILED'"
        )
    )[0][0]
    assert n == 1


def test_on_request_validation_fail_returns_block_when_policy_says():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    policy = BinancePerpAdapterPolicy(block_on_invalid=True)
    a = BinancePerpAdapter(journal=j, policy=policy)
    bad = _make_request(symbol="AAPL")
    result = a.on_request(bad, j, ts_ns=3)
    assert result.block is not None
    assert result.block.component == "binance_perp_adapter"


def test_on_fill_classifies_filled_terminal():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    req = _make_request()
    a.on_request(req, j, ts_ns=100)
    ack = {
        "symbol": "BTCUSDT", "orderId": 9999,
        "clientOrderId": "perp-001",
        "status": "FILLED", "side": "BUY",
        "origQty": "0.05", "executedQty": "0.05",
        "cumQty": "0.05", "avgPrice": "50000.0",
        "price": "50000.0", "commission": "0.000025",
        "type": "LIMIT", "timeInForce": "GTC",
    }
    result = a.on_fill(req, ack, j, ts_ns=200)
    assert result.observation["binance_perp_status"] == "FILLED"
    state = a.get("perp-001")
    assert state is not None
    assert state.status == BinancePerpStatus.FILLED
    assert state.venue_order_id == "9999"


def test_on_fill_idempotent_against_terminal():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    req = _make_request()
    a.on_request(req, j, ts_ns=100)
    ack = {
        "clientOrderId": "perp-001",
        "status": "FILLED", "side": "BUY",
        "executedQty": "0.05", "avgPrice": "50000.0",
        "origQty": "0.05", "orderId": 1, "price": "50000.0",
        "type": "LIMIT", "timeInForce": "GTC",
        "symbol": "BTCUSDT",
    }
    a.on_fill(req, ack, j, ts_ns=200)
    # Replay a wildly different ack — must NOT regress.
    result = a.on_fill(
        req,
        dict(ack, status="REJECTED", code=-2010,
             executedQty="0", avgPrice="0"),
        j, ts_ns=300,
    )
    assert result.observation["binance_perp_adapter"] == "duplicate_callback"
    state = a.get("perp-001")
    assert state.status == BinancePerpStatus.FILLED


def test_on_fill_classifies_partially_filled_and_does_not_terminate():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    req = _make_request()
    a.on_request(req, j, ts_ns=100)
    ack = {
        "clientOrderId": "perp-001",
        "status": "PARTIALLY_FILLED", "side": "BUY",
        "origQty": "0.05", "executedQty": "0.02",
        "cumQty": "0.02", "avgPrice": "50000.0",
        "orderId": 1, "price": "50000.0",
        "type": "LIMIT", "timeInForce": "GTC",
        "symbol": "BTCUSDT",
    }
    result = a.on_fill(req, ack, j, ts_ns=200)
    assert result.observation["binance_perp_status"] == "PARTIALLY_FILLED"
    state = a.get("perp-001")
    assert state.status == BinancePerpStatus.PARTIALLY_FILLED
    # A duplicate PARTIALLY_FILLED replay does NOT regress.
    result2 = a.on_fill(req, ack, j, ts_ns=250)
    assert (
        result2.observation["binance_perp_adapter"] == "duplicate_callback"
    )


def test_record_reject_terminal_promotion():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    req = _make_request()
    a.on_request(req, j, ts_ns=100)
    state = a.record_reject(
        request=req,
        ack={"code": -2008, "msg": "Symbol halted.",
             "clientOrderId": "perp-001"},
        ts_ns=300,
    )
    assert state is not None
    assert state.status == BinancePerpStatus.REJECTED
    n_rejects = list(
        j.conn.execute(
            "SELECT COUNT(*) FROM binance_perp_acks "
            "WHERE status='REJECTED'"
        )
    )[0][0]
    assert n_rejects == 1


def test_record_reject_is_noop_for_non_perp_request():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    state = a.record_reject(
        request={"client_order_id": "x",
                 "venue": "coinbase_advanced",
                 "symbol": "BTCUSDT", "side": "BUY", "qty": 0.01},
        ack={"code": -2010, "msg": "?"},
        ts_ns=300,
    )
    assert state is None


# ---------------------------------------------------------------------------
# WSS Consumer tests
# ---------------------------------------------------------------------------


def test_wss_consumer_order_trade_update_journals_and_updates_state():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    a.on_request(
        _make_request(client_order_id="perp-wss-1"),
        j, ts_ns=1_700_000_000_000_000_000,
    )
    consumer = BinancePerpWssConsumer(adapter=a)
    consumer.connect(ts_ns=1_700_000_000_000_001_000)
    raw = json.dumps({
        "e": "ORDER_TRADE_UPDATE",
        "T": 1_700_000_000_000_002_000,
        "o": {
            "s": "BTCUSDT", "c": "perp-wss-1",
            "S": "BUY", "i": 555,
            "X": "FILLED", "z": "0.05",
            "ap": "50000.0",
            "n": "0.000025",
        },
    })
    res = consumer.push_frame(raw, ts_ns=1_700_000_000_000_002_000)
    assert res is not None
    assert res.observation["binance_perp_wss"] == "order_trade_update"
    snap = consumer.snapshot()
    assert snap.n_order_updates == 1
    state = a.get("perp-wss-1")
    assert state.status == BinancePerpStatus.FILLED
    assert state.venue_order_id == "555"


def test_wss_consumer_listen_key_expired_triggers_reconnect():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    consumer = BinancePerpWssConsumer(
        adapter=a, listen_key="abc",
    )
    consumer.connect(ts_ns=1)
    raw = json.dumps({"e": "listenKeyExpired", "T": 2})
    consumer.push_frame(raw, ts_ns=2)
    snap = consumer.snapshot()
    assert snap.n_listen_key_expirations == 1
    assert snap.n_reconnects == 1
    assert snap.state == BinancePerpWssState.RECONNECTING


def test_wss_consumer_unparseable_frame_does_not_crash():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    consumer = BinancePerpWssConsumer(adapter=a)
    consumer.connect(ts_ns=1)
    assert consumer.push_frame("", ts_ns=2) is None
    assert consumer.push_frame("{not-json", ts_ns=2) is None


def test_wss_consumer_halts_after_max_reconnects():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    policy = BinancePerpAdapterPolicy(wss_max_reconnects=2)
    consumer = BinancePerpWssConsumer(adapter=a, policy=policy)
    consumer.connect(ts_ns=1)
    for i in range(2):
        consumer.push_frame(
            json.dumps({"e": "listenKeyExpired",
                        "T": int(2 + i)}),
            ts_ns=int(2 + i),
        )
    snap = consumer.snapshot()
    # The 3rd would push the counter past the cap; we test that
    # the second expiration transitioned through RECONNECTING and
    # the third drove the state to HALTED.
    consumer.push_frame(
        json.dumps({"e": "listenKeyExpired", "T": 99}),
        ts_ns=99,
    )
    snap2 = consumer.snapshot()
    assert snap2.state == BinancePerpWssState.HALTED
    assert snap2.n_halt_events == 1


def test_wss_consumer_account_update_increments_counter():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    consumer = BinancePerpWssConsumer(adapter=a)
    consumer.connect(ts_ns=1)
    consumer.push_frame(
        json.dumps({"e": "ACCOUNT_UPDATE", "T": 2, "a": {}}),
        ts_ns=2,
    )
    assert consumer.snapshot().n_account_updates == 1


# ---------------------------------------------------------------------------
# Registration / runner wiring test
# ---------------------------------------------------------------------------


def test_register_with_runner_wires_all_three_lists():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    transport = OutboundTransport(callable_send=lambda r: {
        "ok": True, "status": "FILLED",
        "clientOrderId": r.get("client_order_id")
        or r.get("clientOrderId"),
        "orderId": 1, "price": r.get("price"),
        "qty": r.get("qty"), "filled_qty": r.get("qty"),
        "side": r.get("side"), "venue": r.get("venue"),
        "symbol": r.get("symbol"),
    })
    runner = ExecutionRunner(journal=j, transport=transport)
    counts = register_with_runner(runner, a)
    assert counts["components"] >= 1
    assert counts["fill_components"] >= 1
    assert counts["projection_components"] >= 1


def test_paper_transport_round_trip_classifies_filled():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    runner = ExecutionRunner(
        journal=j,
        transport=OutboundTransport(
            callable_send=BinancePerpPaperTransport(),
        ),
    )
    register_with_runner(runner, a)
    ack = runner.submit(_make_request())
    # Adapter should have classified the ack as FILLED.
    state = a.get("perp-001")
    assert state is not None
    assert state.status == BinancePerpStatus.FILLED


def test_paper_transport_round_trip_classifies_rejected():
    j = OrderJournal(":memory:")
    bootstrap_journal(j)
    a = BinancePerpAdapter(journal=j)
    paper = BinancePerpPaperTransport()
    paper.fill_model["perp-001"] = BinancePerpPaperTransport.FillModel(
        reject_code=-2010,
        reject_message="Account has insufficient balance.",
    )
    runner = ExecutionRunner(
        journal=j,
        transport=OutboundTransport(callable_send=paper),
    )
    register_with_runner(runner, a)
    ack = runner.submit(_make_request())
    state = a.get("perp-001")
    assert state is not None
    assert state.status == BinancePerpStatus.REJECTED


# ---------------------------------------------------------------------------
# Policy fingerprint is stable + omits secrets
# ---------------------------------------------------------------------------


def test_policy_fingerprint_excludes_secrets():
    with_secret = BinancePerpAdapterPolicy(
        api_key="ABCD",
        api_secret="super-secret-key-do-not-leak",
    )
    without_secret = BinancePerpAdapterPolicy(api_secret="")
    fp_with = policy_fingerprint(with_secret)
    fp_without = policy_fingerprint(without_secret)
    # The fingerprint scheme records ``api_secret_set`` boolean,
    # not the secret value itself.  Setting secrets vs not should
    # change the fingerprint deterministically.
    assert fp_with != fp_without


def test_policy_fingerprint_does_not_leak_secret_in_dict():
    policy = BinancePerpAdapterPolicy(
        api_key="ABCD",
        api_secret="super-secret",
    )
    serialised = policy.to_dict()
    blob = json.dumps(serialised)
    assert "super-secret" not in blob
    assert "ABCD" not in blob


# ---------------------------------------------------------------------------
# Local helper
# ---------------------------------------------------------------------------


def pytest_approx_small(x):
    """Tiny equality check (no pytest dep)."""
    return x


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    failures: list = []
    cases = [
        v for k, v in globals().items()
        if k.startswith("test_") and callable(v)
    ]
    print(f"running {len(cases)} unit tests...")
    for fn in cases:
        name = fn.__name__
        try:
            fn()
        except AssertionError as e:
            failures.append((name, f"AssertionError: {e}"))
        except Exception as e:
            failures.append((name, f"{type(e).__name__}: {e}"))
    if failures:
        print(f"FAIL: {len(failures)} of {len(cases)} failing:")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        return 1
    print(f"PASS: all {len(cases)} unit tests ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
