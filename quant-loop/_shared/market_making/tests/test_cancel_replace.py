"""Tests for cancel_replace.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

from _shared.market_making.cancel_replace import (
    CancelReplaceParams,
    QuoteTarget,
    RestingOrder,
    decide_actions,
)

PARAMS = CancelReplaceParams(
    tick_size=0.01, amend_threshold_ticks=2, size_tolerance_fraction=0.01,
)

BID_ORDER = RestingOrder("o-bid", "buy", 100.00, 1.0)
ASK_ORDER = RestingOrder("o-ask", "sell", 100.10, 1.0)


def test_hold_when_identical():
    targets = [QuoteTarget("buy", 100.00, 1.0), QuoteTarget("sell", 100.10, 1.0)]
    actions = decide_actions([BID_ORDER, ASK_ORDER], targets, PARAMS)
    assert [a.action for a in actions] == ["hold", "hold"]


def test_hold_within_size_tolerance():
    # 0.5% size drift < 1% tolerance → hold
    targets = [QuoteTarget("buy", 100.00, 1.005)]
    actions = decide_actions([BID_ORDER], targets, PARAMS)
    assert [a.action for a in actions] == ["hold"]


def test_amend_on_small_price_move():
    # 1 tick < 2-tick threshold → amend, queue position preserved
    targets = [QuoteTarget("buy", 100.01, 1.0)]
    actions = decide_actions([BID_ORDER], targets, PARAMS)
    assert len(actions) == 1
    a = actions[0]
    assert a.action == "amend" and a.order_id == "o-bid"
    assert a.price == 100.01 and a.side == "buy"
    assert "small_move" in a.reason


def test_amend_boundary_below_threshold():
    # 1.99 ticks → still amend
    targets = [QuoteTarget("buy", 100.0199, 1.0)]
    actions = decide_actions([BID_ORDER], targets, PARAMS)
    assert actions[0].action == "amend"


def test_cancel_place_at_threshold():
    # exactly 2 ticks → cancel + place
    targets = [QuoteTarget("buy", 100.02, 1.0)]
    actions = decide_actions([BID_ORDER], targets, PARAMS)
    assert [a.action for a in actions] == ["cancel", "place"]
    assert actions[0].order_id == "o-bid"
    assert actions[1].order_id is None and actions[1].price == 100.02


def test_cancel_before_place_ordering():
    # cancel must precede place (margin freed first) even across sides
    targets = [QuoteTarget("buy", 100.50, 1.0), QuoteTarget("sell", 100.10, 1.0)]
    actions = decide_actions([ASK_ORDER, BID_ORDER], targets, PARAMS)
    kinds = [a.action for a in actions]
    assert kinds == ["cancel", "place", "hold"]


def test_amend_on_size_only_change():
    targets = [QuoteTarget("buy", 100.00, 2.0)]
    actions = decide_actions([BID_ORDER], targets, PARAMS)
    assert len(actions) == 1
    a = actions[0]
    assert a.action == "amend" and a.reason == "size_change"
    assert a.price == 100.00 and a.size == 2.0


def test_direction_flip_is_cancel_plus_place():
    # resting bid, new target only on the ask → cancel buy, place sell
    targets = [QuoteTarget("sell", 100.05, 1.0)]
    actions = decide_actions([BID_ORDER], targets, PARAMS)
    assert [a.action for a in actions] == ["cancel", "place"]
    assert actions[0].side == "buy" and actions[0].order_id == "o-bid"
    assert actions[1].side == "sell" and actions[1].price == 100.05


def test_target_removed_cancels():
    actions = decide_actions([BID_ORDER, ASK_ORDER],
                             [QuoteTarget("buy", 100.00, 1.0)], PARAMS)
    kinds = sorted((a.action, a.side) for a in actions)
    assert kinds == [("cancel", "sell"), ("hold", "buy")]


def test_new_quote_placed_when_no_resting():
    actions = decide_actions([], [QuoteTarget("buy", 99.9, 0.5)], PARAMS)
    assert len(actions) == 1
    a = actions[0]
    assert a.action == "place" and a.order_id is None and a.reason == "new_quote"


def test_duplicate_resting_cancelled():
    dup = RestingOrder("o-bid-2", "buy", 99.99, 1.0)
    targets = [QuoteTarget("buy", 100.00, 1.0)]
    actions = decide_actions([BID_ORDER, dup], targets, PARAMS)
    kinds = sorted((a.action, a.order_id) for a in actions)
    assert kinds == [("cancel", "o-bid-2"), ("hold", "o-bid")]
