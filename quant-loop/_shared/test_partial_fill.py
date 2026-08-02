"""Tests for _shared/partial_fill.py (B5) — synthetic bars only.

Run:
    python3 -m pytest _shared/test_partial_fill.py -q
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

from _shared.partial_fill import (  # noqa: E402
    DEFAULT_PARTIAL_FILL_POLICY,
    FILL_FILLED,
    FILL_NOT_TOUCHED,
    FILL_PARTIAL_TOUCH,
    FILL_PARTIAL_VOLUME_CAP,
    Bar,
    OrderSpec,
    PartialFillPolicy,
    fill_price,
    is_touched,
    simulate_bar_fill,
    simulate_bar_fills,
)

# A bar trading 100 -> [98 .. 102] with 400 units of volume.
BAR = Bar(ts_ns=1_000, open=100.0, high=102.0, low=98.0, close=101.0, volume=400.0)


def test_market_order_fills_fully_when_volume_is_ample():
    order = OrderSpec(order_id="m1", side="BUY", qty=10.0, order_type="MARKET")
    fill = simulate_bar_fill(order, BAR)
    assert fill.is_filled
    assert fill.qty == 10.0
    assert fill.price == BAR.open
    assert fill.fill_ratio == 1.0
    assert fill.remaining_qty == 0.0


def test_market_order_partial_when_order_exceeds_participation_budget():
    # budget = 400 * 0.25 = 100 < 150 requested
    order = OrderSpec(order_id="m2", side="SELL", qty=150.0, order_type="MARKET")
    fill = simulate_bar_fill(order, BAR)
    assert fill.reason == FILL_PARTIAL_VOLUME_CAP
    assert fill.qty == 100.0
    assert fill.remaining_qty == 50.0
    assert abs(fill.fill_ratio - 100.0 / 150.0) < 1e-12


def test_limit_buy_traded_through_fills_fully():
    order = OrderSpec(order_id="b1", side="BUY", qty=50.0, price=99.0)
    fill = simulate_bar_fill(order, BAR)
    assert fill.reason == FILL_FILLED
    assert fill.qty == 50.0
    # conservative: no price improvement, fills at the limit price
    assert fill.price == 99.0


def test_limit_buy_marginal_touch_gets_touch_factor_of_budget():
    # bar.low == price == 98.0 exactly: marginal touch -> budget halved
    order = OrderSpec(order_id="b2", side="BUY", qty=60.0, price=98.0)
    fill = simulate_bar_fill(order, BAR)
    assert fill.reason == FILL_PARTIAL_TOUCH
    # budget = 100 * touch_fill_factor(0.5) = 50
    assert fill.qty == 50.0
    assert fill.remaining_qty == 10.0


def test_limit_buy_marginal_touch_fills_fully_if_qty_fits_touch_budget():
    order = OrderSpec(order_id="b3", side="BUY", qty=40.0, price=98.0)
    fill = simulate_bar_fill(order, BAR)
    assert fill.reason == FILL_FILLED
    assert fill.qty == 40.0


def test_limit_not_touched_yields_zero_fill():
    order = OrderSpec(order_id="b4", side="BUY", qty=10.0, price=95.0)
    fill = simulate_bar_fill(order, BAR)
    assert fill.reason == FILL_NOT_TOUCHED
    assert fill.qty == 0.0
    assert fill.fill_ratio == 0.0
    assert fill.remaining_qty == 10.0


def test_limit_sell_symmetric_touch_logic():
    assert is_touched(OrderSpec("s1", "SELL", 1.0, 101.5), BAR)
    assert not is_touched(OrderSpec("s2", "SELL", 1.0, 103.0), BAR)
    # marginal touch at the high
    order = OrderSpec(order_id="s3", side="SELL", qty=60.0, price=102.0)
    fill = simulate_bar_fill(order, BAR)
    assert fill.reason == FILL_PARTIAL_TOUCH
    assert fill.qty == 50.0


def test_conservative_mode_partial_when_bar_volume_below_order_qty():
    # The B5 acceptance case: limit touched, but bar volume (even fully
    # traded-through) cannot cover the order under the participation cap.
    tiny_bar = Bar(ts_ns=2_000, open=100.0, high=101.0, low=99.0,
                   close=100.5, volume=8.0)
    order = OrderSpec(order_id="b5", side="BUY", qty=10.0, price=99.5)
    fill = simulate_bar_fill(order, tiny_bar)
    # budget = 8 * 0.25 = 2 -> partial via volume cap (price inside range)
    assert fill.reason == FILL_PARTIAL_VOLUME_CAP
    assert fill.qty == 2.0


def test_price_improvement_opt_in_fills_at_gapped_open():
    gapped = Bar(ts_ns=3_000, open=97.0, high=101.0, low=96.5,
                 close=100.0, volume=400.0)
    order = OrderSpec(order_id="g1", side="BUY", qty=10.0, price=99.0)
    conservative = simulate_bar_fill(order, gapped)
    assert conservative.price == 99.0
    improving = simulate_bar_fill(
        order, gapped,
        PartialFillPolicy(allow_price_improvement=True),
    )
    assert improving.price == 97.0


def test_price_improvement_never_worsens_price():
    policy = PartialFillPolicy(allow_price_improvement=True)
    order = OrderSpec(order_id="g2", side="BUY", qty=10.0, price=99.0)
    assert fill_price(order, BAR, policy) == 99.0


def test_zero_volume_bar_fills_nothing():
    dead = Bar(ts_ns=4_000, open=100.0, high=100.0, low=100.0,
               close=100.0, volume=0.0)
    order = OrderSpec(order_id="z1", side="BUY", qty=1.0, price=100.0)
    fill = simulate_bar_fill(order, dead)
    assert fill.qty == 0.0
    assert fill.reason in (FILL_PARTIAL_TOUCH, FILL_PARTIAL_VOLUME_CAP)


def test_multiple_orders_share_the_bar_budget():
    # budget = 100; first order takes 80, second gets only the 20 left
    orders = [
        OrderSpec(order_id="q1", side="BUY", qty=80.0, price=99.0),
        OrderSpec(order_id="q2", side="BUY", qty=80.0, price=99.0),
        OrderSpec(order_id="q3", side="BUY", qty=10.0, price=95.0),  # untouched
    ]
    fills = simulate_bar_fills(orders, BAR)
    assert fills[0].reason == FILL_FILLED and fills[0].qty == 80.0
    assert fills[1].reason == FILL_PARTIAL_VOLUME_CAP and fills[1].qty == 20.0
    assert fills[2].reason == FILL_NOT_TOUCHED and fills[2].qty == 0.0


def test_validation_rejects_bad_inputs():
    for ctor in (
        lambda: OrderSpec(order_id="x", side="LONG", qty=1.0, price=1.0),
        lambda: OrderSpec(order_id="x", side="BUY", qty=0.0, price=1.0),
        lambda: OrderSpec(order_id="x", side="BUY", qty=1.0, price=-1.0),
        lambda: OrderSpec(order_id="x", side="BUY", qty=1.0,
                          order_type="MARKET", price=1.0),
        lambda: Bar(ts_ns=0, open=1.0, high=0.5, low=1.0, close=1.0,
                    volume=1.0),
        lambda: PartialFillPolicy(participation_rate=0.0),
        lambda: PartialFillPolicy(participation_rate=1.5),
    ):
        try:
            ctor()
        except ValueError:
            continue
        raise AssertionError(f"{ctor} did not raise ValueError")


def test_default_policy_is_conservative():
    assert DEFAULT_PARTIAL_FILL_POLICY.allow_price_improvement is False
    assert DEFAULT_PARTIAL_FILL_POLICY.touch_fill_factor < 1.0
