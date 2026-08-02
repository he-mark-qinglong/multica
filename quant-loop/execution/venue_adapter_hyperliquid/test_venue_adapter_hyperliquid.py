"""Tests for venue_adapter_hyperliquid (E4) — fully offline.

Covers:

* cloid discipline (is_valid_cloid / normalize_cloid / derive_cloid);
* EIP-712 abstraction (build_eip712_signable shape, sign_action with
  an injected stub signer — no private keys, no real crypto);
* action.order construction (wire keys, TIF Gtc/Ioc/Alo, grouping);
* validate_hl_intent (coin rule, asset resolution, TIF set, cloid);
* classify_exchange_response (resting / filled / per-order error /
  envelope err; MARGIN_REJECTED / RATE_LIMITED /
  POST_ONLY_WOULD_CROSS / IOC_NO_MATCH);
* parse_ws_message (orderUpdates, userFills, pong, error, junk);
* adapter hooks (on_request tag, on_fill classify + idempotency,
  record_reject, WS apply via cloid, cold-start recovery);
* HyperliquidPaperTransport (filled / resting / margin-reject /
  rate-limit envelopes, call recording — no network).

Run:
    python3 -m pytest execution/venue_adapter_hyperliquid/test_venue_adapter_hyperliquid.py -q
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import json  # noqa: E402
import sqlite3  # noqa: E402

import pytest  # noqa: E402

from execution.venue_adapter_hyperliquid.venue_adapter_hyperliquid import (  # noqa: E402
    IOC_NO_MATCH,
    MARGIN_REJECTED,
    POST_ONLY_WOULD_CROSS,
    RATE_LIMITED,
    HyperliquidAdapter,
    HyperliquidAdapterPolicy,
    HyperliquidPaperTransport,
    HyperliquidPaperTransportFillModel,
    HyperliquidStatus,
    SignedEnvelope,
    bootstrap_journal,
    build_eip712_signable,
    build_order_action,
    build_order_wire_order,
    classify_exchange_response,
    derive_cloid,
    is_valid_cloid,
    map_order_update_status,
    normalize_cloid,
    parse_ws_message,
    sign_action,
    validate_hl_intent,
)

POLICY = HyperliquidAdapterPolicy(asset_index={"BTC": 0, "ETH": 1})


class _Journal:
    """Duck-typed journal stub (mirrors the spot sibling's tests)."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row


def _stub_signer(signable):
    """Injected signer stub — echoes the payload hash; no crypto."""
    import hashlib

    digest = hashlib.sha256(
        json.dumps(signable, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return SignedEnvelope(r="0x" + digest, s="0x" + digest[::-1], v=27)


def _req(**overrides):
    req = {
        "venue": "hyperliquid",
        "client_order_id": "coid-hl-1",
        "symbol": "BTC",
        "side": "BUY",
        "qty": 0.05,
        "price": 50000.0,
        "order_type": "LIMIT",
        "time_in_force": "Gtc",
        "asset": 0,
    }
    req.update(overrides)
    return req


# -- cloid -------------------------------------------------------------------


def test_cloid_validation():
    good = "0x" + "ab" * 16
    assert is_valid_cloid(good)
    assert normalize_cloid(good.upper().replace("0X", "0x")) == good
    assert not is_valid_cloid("0x" + "ab" * 8)    # too short
    assert not is_valid_cloid("0x" + "ab" * 32)   # too long
    assert not is_valid_cloid("ab" * 16)           # no 0x prefix
    assert not is_valid_cloid("0x" + "zz" * 32)    # non-hex
    assert not is_valid_cloid(None)
    with pytest.raises(ValueError):
        normalize_cloid("not-a-cloid")


def test_derive_cloid_deterministic():
    c1 = derive_cloid("coid-hl-1")
    c2 = derive_cloid("coid-hl-1")
    assert c1 == c2
    assert is_valid_cloid(c1)
    assert derive_cloid("coid-hl-2") != c1
    with pytest.raises(ValueError):
        derive_cloid("")


# -- EIP-712 abstraction ------------------------------------------------------


def test_signable_shape():
    action = build_order_action([build_order_wire_order(
        asset=0, is_buy=True, limit_px=50000.0, sz=0.05, tif="Gtc",
        cloid=derive_cloid("coid-hl-1"),
    )])
    signable = build_eip712_signable(action, nonce_ms=1700000000000)
    assert signable["domain"]["name"] == "Exchange"
    assert signable["primaryType"] == "HyperliquidAction"
    assert signable["message"]["nonce"] == 1700000000000
    # action is embedded as canonical JSON
    embedded = json.loads(signable["message"]["action"])
    assert embedded["type"] == "order"
    with pytest.raises(ValueError):
        build_eip712_signable(action, nonce_ms=0)


def test_sign_action_uses_injected_signer():
    action = {"type": "order", "orders": [], "grouping": "na"}
    body = sign_action(action, nonce_ms=1700000000000,
                       signer=_stub_signer)
    assert body["nonce"] == 1700000000000
    sig = body["signature"]
    assert sig["r"].startswith("0x") and sig["v"] == 27
    assert body["action"] == action
    # signer must return SignedEnvelope
    with pytest.raises(TypeError):
        sign_action(action, nonce_ms=1, signer=lambda s: {"r": "0x"})


# -- action.order construction ------------------------------------------------


def test_build_order_wire_order():
    w = build_order_wire_order(
        asset=0, is_buy=False, limit_px=50000.0, sz=0.05,
        reduce_only=True, tif="Alo", cloid=derive_cloid("x"),
    )
    assert w["a"] == 0 and w["b"] is False
    assert w["t"] == {"limit": {"tif": "Alo"}}
    assert w["r"] is True
    assert is_valid_cloid(w["c"])
    with pytest.raises(ValueError):
        build_order_wire_order(asset=0, is_buy=True, limit_px=-1.0,
                               sz=0.05)
    with pytest.raises(ValueError):
        build_order_wire_order(asset=0, is_buy=True, limit_px=1.0,
                               sz=0.05, tif="FOK")


def test_build_order_action_grouping():
    w = build_order_wire_order(asset=1, is_buy=True, limit_px=3000.0,
                               sz=0.5, tif="Ioc")
    action = build_order_action([w])
    assert action["type"] == "order"
    assert action["grouping"] == "na"
    assert action["orders"][0] == w
    with pytest.raises(ValueError):
        build_order_action([])
    with pytest.raises(ValueError):
        build_order_action([w], grouping="bad")


# -- validate_hl_intent --------------------------------------------------------


def test_validate_happy_and_defaults():
    ok, reason = validate_hl_intent(_req(), POLICY)
    assert ok, reason
    # asset resolved from policy map when omitted
    ok, reason = validate_hl_intent(_req(asset=None), POLICY)
    assert ok, reason
    # tif case-insensitive
    ok, _ = validate_hl_intent(_req(time_in_force="gtc"), POLICY)
    assert ok
    ok, _ = validate_hl_intent(_req(time_in_force="Alo"), POLICY)
    assert ok


def test_validate_failures():
    assert validate_hl_intent(_req(venue="binance_spot"), POLICY)[0] is False
    assert validate_hl_intent(_req(symbol="btcusdt"), POLICY)[0] is False
    assert validate_hl_intent(_req(asset=None, symbol="DOGE"), POLICY)[1].startswith("asset_index_unknown")  # noqa: E501
    assert validate_hl_intent(_req(time_in_force="GTC5"), POLICY)[0] is False
    assert validate_hl_intent(_req(order_type="MARKET"), POLICY)[0] is False
    assert validate_hl_intent(_req(cloid="0x1234"), POLICY)[1].startswith("cloid_invalid")  # noqa: E501
    assert validate_hl_intent(_req(price=0.0), POLICY)[0] is False


# -- classify_exchange_response ------------------------------------------------


def test_classify_resting_and_filled():
    status, filled, *_ = classify_exchange_response({
        "status": "ok",
        "response": {"type": "order",
                     "data": {"statuses": [{"resting": {"oid": 7}}]}},
    })
    assert status == HyperliquidStatus.OPEN and filled == 0.0

    status, filled, avg_px, _, oid, _, _ = classify_exchange_response({
        "status": "ok",
        "response": {"type": "order",
                     "data": {"statuses": [{"filled": {
                         "totalSz": "0.05", "avgPx": "50000.0",
                         "oid": 8}}]}},
    })
    assert status == HyperliquidStatus.FILLED
    assert filled == 0.05 and avg_px == 50000.0 and oid == "8"


def test_classify_error_taxonomy():
    def _err(msg, envelope=False):
        if envelope:
            return {"status": "err", "response": msg}
        return {"status": "ok",
                "response": {"type": "order",
                             "data": {"statuses": [{"error": msg}]}}}

    assert classify_exchange_response(
        _err("Insufficient margin to place order"))[5] == MARGIN_REJECTED
    assert classify_exchange_response(
        _err("429 too many requests", envelope=True))[5] == RATE_LIMITED
    assert classify_exchange_response(
        _err("Order would have immediately matched with another "
             "order. (Post only order)"))[5] == POST_ONLY_WOULD_CROSS
    assert classify_exchange_response(
        _err("Order could not immediately match against any resting "
             "orders."))[5] == IOC_NO_MATCH


def test_classify_malformed():
    status, _, _, _, _, reason, _ = classify_exchange_response({})
    assert status == HyperliquidStatus.REJECTED
    assert reason == "OTHER"


# -- WS parsing -----------------------------------------------------------------


def test_parse_order_updates():
    frame = json.dumps({
        "channel": "orderUpdates",
        "data": [{
            "order": {"coin": "BTC", "side": "B", "limitPx": "50000",
                      "sz": "0.05", "oid": 123,
                      "cloid": derive_cloid("coid-hl-1"), "tif": "Gtc",
                      "timestamp": 1700000000000},
            "status": "open",
            "statusTimestamp": 1700000000001,
        }],
    })
    parsed = parse_ws_message(frame)
    assert parsed["kind"] == "ORDER_UPDATE"
    upd = parsed["updates"][0]
    assert upd["coin"] == "BTC" and upd["oid"] == "123"
    assert upd["status_raw"] == "open"


def test_parse_user_fills():
    parsed = parse_ws_message({
        "channel": "userFills",
        "data": {"user": "0xabc", "fills": [{
            "coin": "BTC", "px": "50000", "sz": "0.05", "side": "B",
            "time": 1700000000000, "oid": 123,
            "cloid": derive_cloid("coid-hl-1"), "fee": "0.01",
            "tid": 456, "crossed": False,
        }]},
    })
    assert parsed["kind"] == "USER_FILL"
    f = parsed["fills"][0]
    assert f["px"] == 50000.0 and f["fee"] == 0.01
    assert f["crossed"] is False


def test_parse_misc_frames():
    assert parse_ws_message('{"channel":"pong"}')["kind"] == "PONG"
    assert parse_ws_message(
        '{"channel":"subscriptionResponse"}')["kind"] == "SUBSCRIPTION"
    assert parse_ws_message('{"channel":"error"}')["kind"] == "WSS_ERROR"
    assert parse_ws_message("not json") is None
    assert parse_ws_message(None) is None


def test_map_order_update_status():
    assert map_order_update_status("open")[0] == HyperliquidStatus.OPEN
    st, reason = map_order_update_status("marginCanceled")
    assert st == HyperliquidStatus.CANCELED and reason == MARGIN_REJECTED
    assert map_order_update_status("filled")[0] == HyperliquidStatus.FILLED


# -- adapter hooks ---------------------------------------------------------------


def test_adapter_tag_and_fill():
    journal = _Journal()
    adapter = HyperliquidAdapter(journal=journal, policy=POLICY)
    res = adapter.on_request(_req(), journal, 1_000)
    assert res.observation["hyperliquid_adapter"] == "tagged"
    cloid = res.observation["cloid"]
    assert cloid == derive_cloid("coid-hl-1")

    ack = {
        "status": "ok",
        "response": {"type": "order", "data": {"statuses": [
            {"filled": {"totalSz": "0.05", "avgPx": "50000.0",
                        "oid": 42}}]}},
    }
    res2 = adapter.on_fill(_req(), ack, journal, 2_000)
    assert res2.observation["hyperliquid_status"] == "FILLED"
    assert res2.observation["hyperliquid_filled_qty"] == 0.05
    state = adapter.get("coid-hl-1")
    assert state.status == HyperliquidStatus.FILLED
    assert state.venue_order_id == "42"

    # terminal never regresses
    res3 = adapter.on_fill(_req(), ack, journal, 3_000)
    assert res3.observation["hyperliquid_adapter"] == "duplicate_callback"

    # journal rows exist
    n_intents = journal.conn.execute(
        "SELECT COUNT(*) FROM hyperliquid_intents").fetchone()[0]
    n_acks = journal.conn.execute(
        "SELECT COUNT(*) FROM hyperliquid_acks").fetchone()[0]
    assert n_intents == 1 and n_acks == 1


def test_adapter_sell_sign_and_resting():
    journal = _Journal()
    adapter = HyperliquidAdapter(journal=journal, policy=POLICY)
    req = _req(client_order_id="coid-sell", side="SELL")
    adapter.on_request(req, journal, 1_000)
    ack = {"status": "ok",
           "response": {"type": "order",
                        "data": {"statuses": [{"resting": {"oid": 5}}]}}}
    res = adapter.on_fill(req, ack, journal, 2_000)
    assert res.observation["hyperliquid_status"] == "OPEN"
    assert adapter.get("coid-sell").status == HyperliquidStatus.OPEN


def test_adapter_validation_failed_and_block():
    journal = _Journal()
    policy = HyperliquidAdapterPolicy(asset_index={"BTC": 0},
                                      block_on_invalid=True)
    adapter = HyperliquidAdapter(journal=journal, policy=policy)
    res = adapter.on_request(_req(price=-1.0), journal, 1_000)
    assert res.block is not None
    assert "price" in res.block.reason
    row = journal.conn.execute(
        "SELECT kind FROM hyperliquid_events").fetchone()
    assert row[0] == "VALIDATION_FAILED"


def test_adapter_passthrough_non_hl():
    journal = _Journal()
    adapter = HyperliquidAdapter(journal=journal, policy=POLICY)
    res = adapter.on_request(_req(venue="binance_spot",
                                  hyperliquid=False), journal, 1_000)
    assert res.observation is None
    n = journal.conn.execute(
        "SELECT COUNT(*) FROM hyperliquid_intents").fetchone()[0]
    assert n == 0


def test_record_reject():
    journal = _Journal()
    adapter = HyperliquidAdapter(journal=journal, policy=POLICY)
    req = _req(client_order_id="coid-rj")
    adapter.on_request(req, journal, 1_000)
    state = adapter.record_reject(
        request=req,
        ack={"status": "err", "response": "Insufficient margin"},
        ts_ns=2_000,
    )
    assert state.status == HyperliquidStatus.REJECTED
    row = journal.conn.execute(
        "SELECT reject_reason FROM hyperliquid_acks "
        "WHERE client_order_id='coid-rj'").fetchone()
    assert row[0] == MARGIN_REJECTED


def test_ws_apply_via_cloid_and_recovery():
    journal = _Journal()
    adapter = HyperliquidAdapter(journal=journal, policy=POLICY)
    req = _req()
    adapter.on_request(req, journal, 1_000)
    cloid = derive_cloid("coid-hl-1")

    # orderUpdates: open (reconciled by cloid)
    adapter.apply_ws_event(parse_ws_message({
        "channel": "orderUpdates",
        "data": [{"order": {"coin": "BTC", "side": "B", "oid": 77,
                            "cloid": cloid},
                  "status": "open", "statusTimestamp": 1}],
    }), ts_ns=2_000)
    assert adapter.get("coid-hl-1").status == HyperliquidStatus.OPEN
    assert adapter.get("coid-hl-1").venue_order_id == "77"

    # userFills: fill applied to ack projection
    adapter.apply_ws_event(parse_ws_message({
        "channel": "userFills",
        "data": {"user": "0xabc", "fills": [{
            "coin": "BTC", "px": "50000", "sz": "0.05", "side": "B",
            "time": 1, "oid": 77, "cloid": cloid, "fee": "0.01",
            "tid": 1, "crossed": False}]},
    }), ts_ns=3_000)
    row = journal.conn.execute(
        "SELECT filled_qty, avg_price, commission, source "
        "FROM hyperliquid_acks WHERE client_order_id='coid-hl-1'"
    ).fetchone()
    assert row[0] == 0.05 and row[1] == 50000.0
    assert row[2] == 0.01 and row[3] == "wss"

    # orderUpdates: filled (terminal)
    adapter.apply_ws_event(parse_ws_message({
        "channel": "orderUpdates",
        "data": [{"order": {"coin": "BTC", "side": "B", "oid": 77,
                            "cloid": cloid},
                  "status": "filled", "statusTimestamp": 2}],
    }), ts_ns=4_000)
    assert adapter.get("coid-hl-1").status == HyperliquidStatus.FILLED

    # cold-start recovery
    adapter2 = HyperliquidAdapter(journal=journal, policy=POLICY)
    snap = adapter2.snapshot()
    assert snap.n_filled == 1
    assert adapter2.get("coid-hl-1").venue_order_id == "77"


def test_bootstrap_idempotent():
    journal = _Journal()
    bootstrap_journal(journal)
    bootstrap_journal(journal)


# -- build_signed_order + paper transport ------------------------------------------


def test_build_signed_order_and_paper_transport():
    journal = _Journal()
    adapter = HyperliquidAdapter(journal=journal, policy=POLICY)
    adapter.on_request(_req(), journal, 1_000)
    body = adapter.build_signed_order(
        _req(), nonce_ms=1700000000000, signer=_stub_signer)
    order = body["action"]["orders"][0]
    assert order["a"] == 0 and order["b"] is True
    assert order["t"]["limit"]["tif"] == "Gtc"
    assert order["c"] == derive_cloid("coid-hl-1")
    assert body["signature"]["v"] == 27

    transport = HyperliquidPaperTransport()
    resp = transport(body)
    assert resp["status"] == "ok"
    status, filled, *_ = classify_exchange_response(resp)
    assert status == HyperliquidStatus.FILLED and filled == 0.05
    assert transport.n_calls == 1

    with pytest.raises(ValueError):
        adapter.build_signed_order(_req(price=-1.0),
                                 nonce_ms=1, signer=_stub_signer)


def test_paper_transport_outcomes():
    cloid_m = derive_cloid("margin")
    cloid_r = derive_cloid("rate")
    cloid_rest = derive_cloid("rest")
    transport = HyperliquidPaperTransport(fill_model={
        cloid_m: HyperliquidPaperTransportFillModel(
            kind="error", error="Insufficient margin to place order"),
        cloid_r: HyperliquidPaperTransportFillModel(
            kind="envelope_err", error="429 too many requests"),
        cloid_rest: HyperliquidPaperTransportFillModel(kind="resting"),
    })

    def _body(cloid):
        return {"action": {"type": "order", "grouping": "na",
                           "orders": [{"c": cloid, "p": "1.0",
                                       "s": "1.0"}]},
                "nonce": 1, "signature": {"r": "0x", "s": "0x", "v": 27}}

    assert classify_exchange_response(
        transport(_body(cloid_m)))[5] == MARGIN_REJECTED
    assert classify_exchange_response(
        transport(_body(cloid_r)))[5] == RATE_LIMITED
    assert classify_exchange_response(
        transport(_body(cloid_rest)))[0] == HyperliquidStatus.OPEN
    assert transport.n_calls == 3


def test_fill_model_frozen():
    m = HyperliquidPaperTransportFillModel(kind="resting")
    with pytest.raises(Exception):
        m.kind = "filled"  # type: ignore[misc]
