"""Tests for venue_adapter_binance_spot (E2 + E8) — fully offline.

Covers:

* pure helpers: validate_spot_intent (TIF matrix incl. IOC/FOK and
  post-only LIMIT_MAKER), sign_binance_spot_request (HMAC pinned),
  build_spot_order_wire, classify_binance_spot_rest_ack;
* additive journal DDL installs idempotently;
* adapter on_request / on_fill / record_reject journal the correct
  rows and are idempotent under duplicate callbacks;
* paper transport TIF semantics (E8): IOC partial -> EXPIRED,
  FOK all-or-nothing, LIMIT_MAKER rests, MARKET fills;
* the outbound transport signs when api_secret is set and never
  touches the network (injected stub sender).

Run:
    python3 -m pytest execution/venue_adapter_binance_spot/test_venue_adapter_binance_spot.py -q
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import sqlite3  # noqa: E402
import urllib.parse  # noqa: E402

import pytest  # noqa: E402

from execution.venue_adapter_binance_spot import (  # noqa: E402
    DEFAULT_BINANCE_SPOT_ADAPTER_POLICY,
    T_ACK_OK,
    T_ACK_REJECT,
    T_INTENT_TAGGED,
    T_VALIDATION_FAILED,
    BinanceSpotAdapter,
    BinanceSpotAdapterPolicy,
    BinanceSpotPaperTransport,
    BinanceSpotPaperTransportFillModel as FillModel,
    BinanceSpotStatus,
    OutboundBinanceSpotTransport,
    bootstrap_journal,
    build_spot_order_wire,
    classify_binance_spot_rest_ack,
    policy_fingerprint,
    sign_binance_spot_request,
    validate_spot_intent,
)


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


class _StubJournal:
    """Duck-typed journal: just a .conn SQLite connection."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row


def _make_request(**overrides):
    req = {
        "client_order_id": "spot-001",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": 0.05,
        "price": 50000.0,
        "venue": "binance_spot",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
    }
    req.update(overrides)
    return req


# ---------------------------------------------------------------------------
# validate_spot_intent
# ---------------------------------------------------------------------------


def test_validate_accepts_canonical_gtc_limit():
    ok, reason = validate_spot_intent(_make_request())
    assert ok and reason == ""


def test_validate_accepts_ioc_and_fok_on_limit():  # E8
    for tif in ("IOC", "FOK"):
        ok, _ = validate_spot_intent(_make_request(time_in_force=tif))
        assert ok, tif


def test_validate_rejects_bad_tif_on_limit():  # E8
    ok, reason = validate_spot_intent(
        _make_request(time_in_force="GTX"),
    )
    assert not ok
    assert "time_in_force_unsupported" in reason


def test_validate_limit_maker_is_post_only_and_takes_no_tif():
    ok, _ = validate_spot_intent(_make_request(
        order_type="LIMIT_MAKER", time_in_force=None,
    ))
    assert ok
    ok, reason = validate_spot_intent(_make_request(
        order_type="LIMIT_MAKER", time_in_force="GTC",
    ))
    assert not ok
    assert reason == "limit_maker_takes_no_time_in_force"


def test_validate_market_takes_no_tif_or_price():
    ok, _ = validate_spot_intent(_make_request(
        order_type="MARKET", time_in_force=None, price=None,
    ))
    assert ok
    ok, reason = validate_spot_intent(_make_request(
        order_type="MARKET", time_in_force="IOC",
    ))
    assert not ok and reason == "market_takes_no_time_in_force"


def test_validate_market_buy_may_use_quote_order_qty():
    ok, _ = validate_spot_intent(_make_request(
        order_type="MARKET", time_in_force=None, price=None,
        qty=None, quote_order_qty=100.0,
    ))
    assert ok


def test_validate_rejects_non_spot_venue_and_symbol():
    ok, reason = validate_spot_intent(_make_request(
        venue="binance_usdt_futures",
    ))
    assert not ok and "venue_not_spot" in reason
    ok, reason = validate_spot_intent(_make_request(symbol="btcusdt"))
    assert not ok and "symbol_not_spot_eligible" in reason
    ok, reason = validate_spot_intent(_make_request(symbol="BTC-USDT"))
    assert not ok


def test_validate_accepts_non_usdt_quote_spot_symbol():
    # spot has no quote-suffix rule (ETHBTC is legal)
    ok, _ = validate_spot_intent(_make_request(symbol="ETHBTC"))
    assert ok


def test_validate_rejects_bad_qty_price_side():
    ok, reason = validate_spot_intent(_make_request(qty=0.0))
    assert not ok and "qty_below_min" in reason
    ok, reason = validate_spot_intent(_make_request(price=-1.0))
    assert not ok and "price_non_positive" in reason
    ok, reason = validate_spot_intent(_make_request(side="LONG"))
    assert not ok and "side_invalid" in reason
    ok, reason = validate_spot_intent(_make_request(order_type="STOP"))
    assert not ok and "order_type_unsupported" in reason


def test_opt_in_flag_tags_without_venue_key():
    req = _make_request()
    del req["venue"]
    req["binance_spot"] = True
    ok, _ = validate_spot_intent(req)
    assert ok


# ---------------------------------------------------------------------------
# sign_binance_spot_request
# ---------------------------------------------------------------------------


def test_sign_matches_pinned_hmac():
    secret = "s3cr3t"
    params = {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
              "quantity": 0.05, "price": 50000.0}
    signed = sign_binance_spot_request(
        params, api_secret=secret, timestamp_ns=1_700_000_000_000_000_000,
        recv_window_ms=5000,
    )
    ordered = sorted(
        (k, v) for k, v in params.items()
    ) + [("recvWindow", 5000), ("timestamp", 1700000000000)]
    # signer sorts alphabetically itself; rebuild the canonical string
    canonical = urllib.parse.urlencode(
        sorted(ordered, key=lambda kv: kv[0]),
        doseq=True, quote_via=urllib.parse.quote, safe="",
    )
    expected = hmac.new(
        secret.encode(), canonical.encode(), hashlib.sha256,
    ).hexdigest()
    assert signed["signature"] == expected
    assert signed["timestamp"] == 1700000000000
    assert signed["recvWindow"] == 5000
    # input mapping not mutated
    assert "signature" not in params


def test_sign_is_order_independent():
    secret = "abc"
    a = sign_binance_spot_request(
        {"b": 2, "a": 1}, api_secret=secret, timestamp_ns=0,
    )
    b = sign_binance_spot_request(
        {"a": 1, "b": 2}, api_secret=secret, timestamp_ns=0,
    )
    assert a["signature"] == b["signature"]


def test_sign_requires_secret():
    with pytest.raises(ValueError):
        sign_binance_spot_request({"a": 1}, api_secret="")


# ---------------------------------------------------------------------------
# build_spot_order_wire
# ---------------------------------------------------------------------------


def test_wire_limit_carries_tif_ioc_fok():  # E8
    for tif in ("IOC", "FOK", "GTC"):
        wire = build_spot_order_wire(_make_request(time_in_force=tif))
        assert wire["timeInForce"] == tif
        assert wire["type"] == "LIMIT"
        assert wire["newClientOrderId"] == "spot-001"
        assert wire["quantity"] == 0.05
        assert wire["price"] == 50000.0
        assert wire["newOrderRespType"] == "RESULT"


def test_wire_limit_maker_is_post_only_without_tif():
    wire = build_spot_order_wire(_make_request(
        order_type="LIMIT_MAKER", time_in_force=None,
    ))
    assert wire["type"] == "LIMIT_MAKER"
    assert "timeInForce" not in wire
    assert wire["price"] == 50000.0


def test_wire_market_carries_no_tif_no_price():
    wire = build_spot_order_wire(_make_request(
        order_type="MARKET", time_in_force=None, price=None,
    ))
    assert wire["type"] == "MARKET"
    assert "timeInForce" not in wire
    assert "price" not in wire


def test_wire_rejects_invalid_intent():
    with pytest.raises(ValueError):
        build_spot_order_wire(_make_request(time_in_force="GTX"))


# ---------------------------------------------------------------------------
# classify_binance_spot_rest_ack
# ---------------------------------------------------------------------------


def test_classify_filled_result_ack_with_fills_array():
    ack = {
        "symbol": "BTCUSDT", "orderId": 28457,
        "clientOrderId": "spot-001", "status": "FILLED",
        "executedQty": "0.05", "side": "BUY", "type": "LIMIT",
        "fills": [
            {"price": "50000.0", "qty": "0.03",
             "commission": "0.6", "commissionAsset": "USDT"},
            {"price": "50010.0", "qty": "0.02",
             "commission": "0.4", "commissionAsset": "USDT"},
        ],
    }
    (status, filled, avg, comm, oid, rej, code) = (
        classify_binance_spot_rest_ack(ack)
    )
    assert status == BinanceSpotStatus.FILLED
    assert filled == pytest.approx(0.05)
    assert avg == pytest.approx((50000.0 * 0.03 + 50010.0 * 0.02) / 0.05)
    assert comm == pytest.approx(1.0)
    assert oid == "28457"
    assert rej is None and code is None


def test_classify_sell_signs_filled_qty_negative():
    ack = {"status": "FILLED", "executedQty": "0.05", "side": "SELL",
           "orderId": 1}
    _, filled, *_ = classify_binance_spot_rest_ack(ack)
    assert filled == pytest.approx(-0.05)


def test_classify_ioc_expired_with_partial_fill():  # E8
    ack = {"status": "EXPIRED", "executedQty": "0.03", "side": "BUY",
           "orderId": 7, "timeInForce": "IOC"}
    status, filled, *_ = classify_binance_spot_rest_ack(ack)
    assert status == BinanceSpotStatus.EXPIRED
    assert filled == pytest.approx(0.03)


def test_classify_reject_error_codes():
    for code, reason in (
        (-2010, "INSUFFICIENT_BALANCE"),
        (-1013, "FILTER_FAILURE"),
        (-1021, "TIMESTAMP_OUTSIDE_RECVWINDOW"),
        (-1022, "INVALID_SIGNATURE"),
        (-1003, "RATE_LIMITED"),
        (-9999, "OTHER"),
    ):
        status, _, _, _, _, rej, err = classify_binance_spot_rest_ack(
            {"code": code, "msg": "boom"},
        )
        assert status == BinanceSpotStatus.REJECTED
        assert rej == reason
        assert err == str(code)


def test_classify_limit_maker_would_cross_expired_in_match():
    ack = {"status": "EXPIRED_IN_MATCH", "executedQty": "0",
           "side": "BUY", "orderId": 9, "type": "LIMIT_MAKER"}
    status, filled, *_ = classify_binance_spot_rest_ack(ack)
    assert status == BinanceSpotStatus.EXPIRED
    assert filled == 0.0


# ---------------------------------------------------------------------------
# Adapter + journal
# ---------------------------------------------------------------------------


def _make_adapter(**policy_kw):
    journal = _StubJournal()
    policy = BinanceSpotAdapterPolicy(**policy_kw)
    return BinanceSpotAdapter(journal=journal, policy=policy), journal


def test_bootstrap_is_idempotent():
    journal = _StubJournal()
    bootstrap_journal(journal)
    bootstrap_journal(journal)
    tables = {
        r[0] for r in journal.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        )
    }
    assert {"binance_spot_intents", "binance_spot_events",
            "binance_spot_acks"} <= tables


def test_on_request_passthrough_non_spot():
    adapter, journal = _make_adapter()
    result = adapter.on_request(
        {"client_order_id": "x", "venue": "binance_usdt_futures"},
        journal, ts_ns=1,
    )
    assert result.observation is None
    n = journal.conn.execute(
        "SELECT COUNT(*) FROM binance_spot_intents",
    ).fetchone()[0]
    assert n == 0


def test_on_request_tags_and_journals_intent():
    adapter, journal = _make_adapter()
    result = adapter.on_request(_make_request(), journal, ts_ns=10)
    assert result.observation["binance_spot_adapter"] == "tagged"
    row = journal.conn.execute(
        "SELECT * FROM binance_spot_intents WHERE client_order_id='spot-001'",
    ).fetchone()
    assert row["status"] == "PENDING"
    assert row["qty"] == pytest.approx(0.05)
    assert row["time_in_force"] == "GTC"
    assert row["venue"] == "binance_spot"
    assert row["binance_spot_signature"]
    kinds = [r[0] for r in journal.conn.execute(
        "SELECT kind FROM binance_spot_events",
    )]
    assert kinds == [T_INTENT_TAGGED]
    state = adapter.get("spot-001")
    assert state.status == BinanceSpotStatus.PENDING


def test_on_request_ioc_journals_tif():  # E8
    adapter, journal = _make_adapter()
    adapter.on_request(
        _make_request(client_order_id="ioc-1", time_in_force="IOC"),
        journal, ts_ns=1,
    )
    row = journal.conn.execute(
        "SELECT time_in_force FROM binance_spot_intents "
        "WHERE client_order_id='ioc-1'",
    ).fetchone()
    assert row[0] == "IOC"


def test_on_request_validation_failure_journals_event():
    adapter, journal = _make_adapter()
    result = adapter.on_request(
        _make_request(time_in_force="GTX"), journal, ts_ns=5,
    )
    assert result.observation["binance_spot_adapter"] == "validation_failed"
    kinds = [r[0] for r in journal.conn.execute(
        "SELECT kind FROM binance_spot_events",
    )]
    assert kinds == [T_VALIDATION_FAILED]
    n = journal.conn.execute(
        "SELECT COUNT(*) FROM binance_spot_intents",
    ).fetchone()[0]
    assert n == 0


def test_on_request_block_on_invalid_returns_block():
    adapter, journal = _make_adapter(block_on_invalid=True)
    result = adapter.on_request(
        _make_request(symbol="btcusdt"), journal, ts_ns=5,
    )
    assert result.block is not None
    assert result.block.component == "binance_spot_adapter"


def test_on_fill_classifies_and_upserts_ack():
    adapter, journal = _make_adapter()
    adapter.on_request(_make_request(), journal, ts_ns=10)
    ack = {
        "status": "FILLED", "executedQty": "0.05", "side": "BUY",
        "orderId": 42, "clientOrderId": "spot-001",
        "fills": [{"price": "50000.0", "qty": "0.05",
                   "commission": "0.5", "commissionAsset": "USDT"}],
    }
    result = adapter.on_fill(_make_request(), ack, journal, ts_ns=20)
    assert result.observation["binance_spot_status"] == "FILLED"
    row = journal.conn.execute(
        "SELECT * FROM binance_spot_acks WHERE client_order_id='spot-001'",
    ).fetchone()
    assert row["status"] == "FILLED"
    assert row["filled_qty"] == pytest.approx(0.05)
    assert row["avg_price"] == pytest.approx(50000.0)
    assert row["commission"] == pytest.approx(0.5)
    assert row["venue_order_id"] == "42"
    assert row["source"] == "rest"
    kinds = [r[0] for r in journal.conn.execute(
        "SELECT kind FROM binance_spot_events ORDER BY id",
    )]
    assert kinds == [T_INTENT_TAGGED, T_ACK_OK]
    assert adapter.get("spot-001").status == BinanceSpotStatus.FILLED


def test_on_fill_duplicate_terminal_callback_is_noop():
    adapter, journal = _make_adapter()
    adapter.on_request(_make_request(), journal, ts_ns=10)
    ack = {"status": "FILLED", "executedQty": "0.05", "side": "BUY",
           "orderId": 42, "clientOrderId": "spot-001"}
    adapter.on_fill(_make_request(), ack, journal, ts_ns=20)
    again = adapter.on_fill(_make_request(), ack, journal, ts_ns=21)
    assert again.observation["binance_spot_adapter"] == "duplicate_callback"
    n = journal.conn.execute(
        "SELECT COUNT(*) FROM binance_spot_acks",
    ).fetchone()[0]
    assert n == 1


def test_on_fill_ioc_partial_expired_keeps_partial_qty():  # E8
    adapter, journal = _make_adapter()
    req = _make_request(client_order_id="ioc-2", time_in_force="IOC",
                        qty=0.10)
    adapter.on_request(req, journal, ts_ns=1)
    ack = {"status": "EXPIRED", "executedQty": "0.04", "side": "BUY",
           "orderId": 8, "clientOrderId": "ioc-2", "timeInForce": "IOC"}
    result = adapter.on_fill(req, ack, journal, ts_ns=2)
    assert result.observation["binance_spot_status"] == "EXPIRED"
    row = journal.conn.execute(
        "SELECT * FROM binance_spot_acks WHERE client_order_id='ioc-2'",
    ).fetchone()
    assert row["status"] == "EXPIRED"
    assert row["filled_qty"] == pytest.approx(0.04)


def test_record_reject_journals_terminal_outcome():
    adapter, journal = _make_adapter()
    state = adapter.record_reject(
        request=_make_request(),
        ack={"code": -2010, "msg": "Account has insufficient balance"},
        ts_ns=30,
    )
    assert state.status == BinanceSpotStatus.REJECTED
    row = journal.conn.execute(
        "SELECT * FROM binance_spot_acks WHERE client_order_id='spot-001'",
    ).fetchone()
    assert row["status"] == "REJECTED"
    assert row["reject_reason"] == "INSUFFICIENT_BALANCE"
    assert row["error_code"] == "-2010"
    kinds = [r[0] for r in journal.conn.execute(
        "SELECT kind FROM binance_spot_events",
    )]
    assert kinds == [T_ACK_REJECT]


def test_cold_start_recovers_from_journal():
    adapter, journal = _make_adapter()
    adapter.on_request(_make_request(), journal, ts_ns=10)
    # a fresh adapter over the same journal rebuilds the live cache
    adapter2 = BinanceSpotAdapter(
        journal=journal, policy=DEFAULT_BINANCE_SPOT_ADAPTER_POLICY,
    )
    snap = adapter2.snapshot()
    assert snap.n_pending == 1
    assert snap.intents[0].client_order_id == "spot-001"


# ---------------------------------------------------------------------------
# Paper transport (mock; no network) — E8 semantics
# ---------------------------------------------------------------------------


def test_paper_transport_gtc_limit_rests_new():
    t = BinanceSpotPaperTransport()
    ack = t(_make_request())
    assert ack["status"] == "NEW"
    assert float(ack["executedQty"]) == 0.0


def test_paper_transport_ioc_partial_becomes_expired():  # E8
    t = BinanceSpotPaperTransport(fill_model={
        "spot-001": FillModel(filled_qty=0.02),
    })
    ack = t(_make_request(time_in_force="IOC"))
    assert ack["status"] == "EXPIRED"
    assert float(ack["executedQty"]) == pytest.approx(0.02)
    # and the partial is still journaled/classifiable
    status, filled, *_ = classify_binance_spot_rest_ack(ack)
    assert status == BinanceSpotStatus.EXPIRED
    assert filled == pytest.approx(0.02)


def test_paper_transport_ioc_full_becomes_filled():  # E8
    t = BinanceSpotPaperTransport()
    ack = t(_make_request(time_in_force="IOC"))
    assert ack["status"] == "FILLED"
    assert float(ack["executedQty"]) == pytest.approx(0.05)


def test_paper_transport_fok_all_or_nothing():  # E8
    t = BinanceSpotPaperTransport(fill_model={
        "spot-001": FillModel(filled_qty=0.01),
    })
    ack = t(_make_request(time_in_force="FOK"))
    assert ack["status"] == "EXPIRED"
    assert float(ack["executedQty"]) == 0.0
    t2 = BinanceSpotPaperTransport()
    ack2 = t2(_make_request(time_in_force="FOK"))
    assert ack2["status"] == "FILLED"


def test_paper_transport_limit_maker_rests_or_rejects():
    resting = BinanceSpotPaperTransport()
    ack = resting(_make_request(order_type="LIMIT_MAKER",
                                time_in_force=None))
    assert ack["status"] == "NEW"
    crossing = BinanceSpotPaperTransport(fill_model={
        "spot-001": FillModel(reject_code=-2010,
                              reject_message="Order would immediately match"),
    })
    ack2 = crossing(_make_request(order_type="LIMIT_MAKER",
                                  time_in_force=None))
    status, _, _, _, _, rej, _ = classify_binance_spot_rest_ack(ack2)
    assert status == BinanceSpotStatus.REJECTED
    assert rej == "INSUFFICIENT_BALANCE"  # same -2010 code family


def test_paper_transport_market_fills():
    t = BinanceSpotPaperTransport()
    ack = t(_make_request(order_type="MARKET", time_in_force=None,
                          price=None))
    assert ack["status"] == "FILLED"


def test_paper_transport_records_calls_without_network():
    t = BinanceSpotPaperTransport()
    t(_make_request())
    t(_make_request(client_order_id="spot-002"))
    assert t.n_calls == 2
    assert t.calls[0]["client_order_id"] == "spot-001"


# ---------------------------------------------------------------------------
# Outbound transport (stub sender; no network)
# ---------------------------------------------------------------------------


def test_outbound_transport_signs_and_posts_wire_shape():
    sent = []

    def stub_send(wire):
        sent.append(dict(wire))
        return {"status": "NEW", "orderId": 1,
                "clientOrderId": wire["newClientOrderId"]}

    transport = OutboundBinanceSpotTransport(
        callable_send=stub_send, api_key="k", api_secret="s3cr3t",
    )
    ack = transport(_make_request(time_in_force="IOC"))
    assert ack["status"] == "NEW"
    wire = sent[0]
    assert wire["timeInForce"] == "IOC"
    assert wire["newOrderRespType"] == "RESULT"
    assert "signature" in wire and len(wire["signature"]) == 64
    assert wire["timestamp"] and wire["recvWindow"] == 5000
    assert wire["X-MBX-APIKEY"] == "k"


def test_outbound_transport_unsigned_when_no_secret():
    sent = []
    transport = OutboundBinanceSpotTransport(
        callable_send=lambda wire: sent.append(wire) or {"status": "NEW"},
    )
    transport(_make_request(order_type="LIMIT_MAKER", time_in_force=None))
    wire = sent[0]
    assert "signature" not in wire
    assert wire["type"] == "LIMIT_MAKER"
    assert "timeInForce" not in wire


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def test_policy_fingerprint_stable_and_secret_free():
    p1 = BinanceSpotAdapterPolicy(api_key="k", api_secret="s")
    p2 = BinanceSpotAdapterPolicy(api_key="k2", api_secret="s2")
    # secrets never enter the fingerprint payload
    assert policy_fingerprint(p1) == policy_fingerprint(p2)
    assert "s3cr3t" not in json.dumps(p1.to_dict())
    with pytest.raises(ValueError):
        BinanceSpotAdapterPolicy(default_tif="GTX")
