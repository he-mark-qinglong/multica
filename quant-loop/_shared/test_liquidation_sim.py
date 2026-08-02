"""Tests for _shared/liquidation_sim.py (B13) — synthetic bars only.

Run:
    python3 -m pytest _shared/test_liquidation_sim.py -q
"""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest  # noqa: E402

from _shared.liquidation_sim import (  # noqa: E402
    LiquidationEngine,
    LiquidationPolicy,
    Position,
    is_liquidatable,
    liquidation_price,
    margin_ratio,
    simulate_liquidations,
)
from _shared.partial_fill import Bar  # noqa: E402

MMR = 0.005
POLICY_FULL = LiquidationPolicy(
    maintenance_margin_rate=MMR, penalty_fee_rate=0.002, mode="full",
)
POLICY_PARTIAL = LiquidationPolicy(
    maintenance_margin_rate=MMR, penalty_fee_rate=0.002, mode="partial",
)


def _bar(ts, o, h, l, c):
    return Bar(ts_ns=ts, open=o, high=h, low=l, close=c, volume=1000.0)


# --- pure maths -------------------------------------------------------------


def test_liquidation_price_long_matches_closed_form():
    # 10x long at 100, margin posted at entry -> p* = 100*(1-0.1)/(1-0.005)
    qty, entry, lev = 2.0, 100.0, 10.0
    wallet = qty * entry / lev
    p_star = liquidation_price(qty, entry, wallet, MMR)
    expected = entry * (1.0 - 1.0 / lev) / (1.0 - MMR)
    assert p_star == pytest.approx(expected, rel=1e-12)
    # sanity: at p* the margin ratio is exactly mmr
    assert margin_ratio(qty, entry, wallet, p_star) == pytest.approx(
        MMR, rel=1e-9,
    )


def test_liquidation_price_short_matches_closed_form():
    qty, entry, lev = -2.0, 100.0, 10.0
    wallet = abs(qty) * entry / lev
    p_star = liquidation_price(qty, entry, wallet, MMR)
    expected = entry * (1.0 + 1.0 / lev) / (1.0 + MMR)
    assert p_star == pytest.approx(expected, rel=1e-12)
    assert margin_ratio(qty, entry, wallet, p_star) == pytest.approx(
        MMR, rel=1e-9,
    )


def test_one_x_long_is_never_liquidated():
    # equity = wallet + qty*(p-entry) = qty*p > qty*p*mmr for all p > 0
    assert not is_liquidatable(1.0, 100.0, 100.0, MMR)
    with pytest.raises(ValueError):
        liquidation_price(1.0, 100.0, 100.0, MMR)


# --- engine: no touch -------------------------------------------------------


def test_bar_above_liq_price_leaves_position_untouched():
    engine = LiquidationEngine(Position("BTCUSDT", 1.0, 100.0, 10.0))
    p_star = engine.liq_price()
    event = engine.on_bar(_bar(1, 100.0, 101.0, p_star + 1.0, 100.5))
    assert event is None
    assert engine.position.qty == 1.0
    assert not engine.closed


# --- full mode ---------------------------------------------------------------


def test_full_mode_liquidates_entire_position_with_penalty():
    pos = Position("BTCUSDT", 1.0, 100.0, 10.0)
    engine = LiquidationEngine(pos, POLICY_FULL)
    p_star = engine.liq_price()
    event = engine.on_bar(_bar(1, 100.0, 101.0, p_star - 0.5, 100.0))
    assert event is not None
    assert event.mode == "FULL"
    assert event.side == "LONG"
    assert event.liq_price == pytest.approx(p_star)
    # no gap: executes at the liquidation price
    assert event.exec_price == pytest.approx(p_star)
    assert event.qty_closed == pytest.approx(-1.0)
    assert event.fee == pytest.approx(1.0 * p_star * 0.002)
    assert event.remaining_qty == 0.0
    assert event.deficit == 0.0
    assert engine.closed
    # engine is inert after a full close
    assert engine.on_bar(_bar(2, 1.0, 200.0, 1.0, 50.0)) is None


def test_gap_through_liq_price_executes_at_open():
    pos = Position("BTCUSDT", 1.0, 100.0, 10.0)
    engine = LiquidationEngine(pos, POLICY_FULL)
    p_star = engine.liq_price()
    gap_open = p_star * 0.95  # bar opens 5% below the liq price
    event = engine.on_bar(_bar(1, gap_open, 101.0, gap_open - 1.0, 99.0))
    assert event.exec_price == pytest.approx(gap_open)


def test_short_liquidates_on_high_touch():
    pos = Position("BTCUSDT", -1.0, 100.0, 10.0)
    engine = LiquidationEngine(pos, POLICY_FULL)
    p_star = engine.liq_price()
    assert p_star > 100.0
    assert engine.on_bar(_bar(1, 100.0, p_star - 0.5, 99.0, 100.0)) is None
    event = engine.on_bar(_bar(2, 100.0, p_star + 0.5, 99.0, 100.0))
    assert event is not None
    assert event.side == "SHORT"
    assert event.qty_closed == pytest.approx(1.0)
    assert event.mode == "FULL"


# --- partial mode ------------------------------------------------------------


def test_partial_mode_reduces_and_improves_margin_ratio():
    pos = Position("BTCUSDT", 1.0, 100.0, 10.0)
    engine = LiquidationEngine(pos, POLICY_PARTIAL)
    p_star = engine.liq_price()
    ratio_before = margin_ratio(1.0, 100.0, engine.wallet_balance, p_star)
    event = engine.on_bar(_bar(1, 100.0, 101.0, p_star - 0.1, 100.0))
    assert event is not None
    assert event.mode == "PARTIAL"
    # rank-down step: exactly partial_close_fraction closed
    assert event.remaining_qty == pytest.approx(0.5)
    assert event.qty_closed == pytest.approx(-0.5)
    assert event.fee == pytest.approx(0.5 * event.exec_price * 0.002)
    # the surviving position steps back from the boundary: its margin
    # ratio at the exec price is strictly better than at the trigger
    ratio_after = margin_ratio(
        engine.position.qty, engine.position.entry_price,
        engine.wallet_balance, event.exec_price,
    )
    assert ratio_after > ratio_before
    assert ratio_before == pytest.approx(MMR, rel=1e-6)


def test_partial_mode_can_trigger_repeatedly_on_grinding_decline():
    pos = Position("BTCUSDT", 1.0, 100.0, 10.0)
    engine = LiquidationEngine(pos, POLICY_PARTIAL)
    events = []
    price = 100.0
    for i in range(12):
        price *= 0.985  # grind down 1.5% per bar
        event = engine.on_bar(
            _bar(i, price * 1.01, price * 1.02, price * 0.99, price)
        )
        if event is not None:
            events.append(event)
        if engine.closed:
            break
    assert len(events) >= 2
    assert all(e.mode in ("PARTIAL", "FULL") for e in events)
    assert events[-1].remaining_qty < 1.0


def test_partial_mode_escalates_to_full_when_equity_cannot_cover_fee():
    pos = Position("BTCUSDT", 1.0, 100.0, 10.0)
    # wallet barely above zero: any fee exceeds equity
    engine = LiquidationEngine(
        pos, POLICY_PARTIAL, wallet_balance=0.05,
    )
    p_star = engine.liq_price()
    event = engine.on_bar(_bar(1, 100.0, 101.0, p_star - 0.1, 100.0))
    assert event.mode == "FULL"
    assert event.deficit >= 0.0
    assert engine.closed


def test_deficit_recorded_when_fee_exceeds_equity():
    pos = Position("BTCUSDT", 1.0, 100.0, 10.0)
    policy = LiquidationPolicy(
        maintenance_margin_rate=MMR, penalty_fee_rate=0.05, mode="full",
    )
    # tiny wallet: 5% fee on notional dwarfs remaining equity
    engine = LiquidationEngine(pos, policy, wallet_balance=0.10)
    p_star = engine.liq_price()
    event = engine.on_bar(_bar(1, 100.0, 101.0, p_star - 1.0, 100.0))
    assert event.mode == "FULL"
    assert event.deficit > 0.0
    assert event.remaining_equity == 0.0


# --- driver ------------------------------------------------------------------


def test_simulate_liquidations_over_bar_sequence():
    pos = Position("BTCUSDT", 1.0, 100.0, 10.0)
    engine_probe = LiquidationEngine(pos)
    p_star = engine_probe.liq_price()
    bars = [
        _bar(1, 100.0, 101.0, 99.0, 100.0),          # no touch
        _bar(2, 99.0, 99.5, p_star - 0.2, 99.0),     # touch
        _bar(3, 99.0, 99.5, 98.0, 99.0),             # after full close
    ]
    events, engine = simulate_liquidations(pos, bars, POLICY_FULL)
    assert len(events) == 1
    assert events[0].ts_ns == 2
    assert engine.closed


# --- validation ----------------------------------------------------------------


def test_validation():
    with pytest.raises(ValueError):
        Position("X", 0.0, 100.0, 10.0)
    with pytest.raises(ValueError):
        Position("X", 1.0, -1.0, 10.0)
    with pytest.raises(ValueError):
        Position("X", 1.0, 100.0, 0.5)
    with pytest.raises(ValueError):
        LiquidationPolicy(maintenance_margin_rate=0.0)
    with pytest.raises(ValueError):
        LiquidationPolicy(mode="partial-ish")
    with pytest.raises(ValueError):
        LiquidationEngine(
            Position("X", 1.0, 100.0, 10.0), wallet_balance=0.0,
        )
