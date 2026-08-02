"""Tests for portfolio/exposure.py (I13)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.portfolio.exposure import (
    ExposureLimiter, ExposureLimits, Position, Rejection, check_exposure,
)


def _pos(sym, qty, price=100.0):
    return Position(sym, qty, price)


def test_no_limits_allows_everything():
    ok, reason = check_exposure({}, _pos("BTC", 100.0), ExposureLimits(), 1000.0)
    assert ok and reason == ""


def test_symbol_cap():
    lim = ExposureLimits(max_symbol_notional=500.0)
    ok, reason = check_exposure({}, _pos("BTC", 6.0), lim, 10_000.0)
    assert not ok and "symbol cap" in reason
    ok, _ = check_exposure({}, _pos("BTC", 4.0), lim, 10_000.0)
    assert ok


def test_total_cap_counts_existing_book():
    lim = ExposureLimits(max_total_notional=1000.0)
    book = {"ETH": _pos("ETH", 6.0)}  # 600 existing
    ok, reason = check_exposure(book, _pos("BTC", 5.0), lim, 10_000.0)
    assert not ok and "total cap" in reason
    ok, _ = check_exposure(book, _pos("BTC", 4.0), lim, 10_000.0)
    assert ok  # 600 + 400 = 1000, at the cap


def test_direction_cap_separate_sides():
    lim = ExposureLimits(max_direction_notional=800.0)
    book = {"BTC": _pos("BTC", 5.0), "ETH": _pos("ETH", -5.0)}  # 500L / 500S
    ok, reason = check_exposure(book, _pos("SOL", 4.0), lim, 10_000.0)
    assert not ok and "long-side cap" in reason   # 500 + 400 > 800
    ok, reason = check_exposure(book, _pos("SOL", -4.0), lim, 10_000.0)
    assert not ok and "short-side cap" in reason  # 500 + 400 > 800
    ok, _ = check_exposure(book, _pos("SOL", 3.0), lim, 10_000.0)
    assert ok


def test_leverage_cap():
    lim = ExposureLimits(max_leverage=2.0)
    book = {"BTC": _pos("BTC", 10.0)}  # 1000 notional
    ok, reason = check_exposure(book, _pos("ETH", 11.0), lim, 1000.0)
    assert not ok and "leverage cap" in reason  # 2100/1000 = 2.1x
    ok, _ = check_exposure(book, _pos("ETH", 10.0), lim, 1000.0)
    assert ok  # exactly 2.0x


def test_leverage_rejects_nonpositive_equity():
    lim = ExposureLimits(max_leverage=2.0)
    ok, reason = check_exposure({}, _pos("BTC", 1.0), lim, 0.0)
    assert not ok and "equity" in reason


def test_replacing_position_not_additive():
    lim = ExposureLimits(max_total_notional=1000.0)
    book = {"BTC": _pos("BTC", 9.0)}  # 900
    # Replacing BTC 9 -> 8 should pass (800, not 1700).
    ok, _ = check_exposure(book, _pos("BTC", 8.0), lim, 10_000.0)
    assert ok


def test_closing_position_always_allowed():
    lim = ExposureLimits(max_total_notional=100.0)
    book = {"BTC": _pos("BTC", 50.0)}  # already over — closing must pass
    ok, _ = check_exposure(book, _pos("BTC", 0.0), lim, 10_000.0)
    assert ok


def test_limiter_records_rejections():
    lim = ExposureLimiter(ExposureLimits(max_symbol_notional=500.0))
    ok, _ = lim.check(_pos("BTC", 10.0), equity=10_000.0)
    assert not ok
    assert len(lim.rejections) == 1
    rej = lim.rejections[0]
    assert isinstance(rej, Rejection)
    assert rej.symbol == "BTC" and "symbol cap" in rej.reason
    # Book unchanged after rejection.
    assert lim.positions == {}


def test_limiter_apply_and_aggregates():
    lim = ExposureLimiter(ExposureLimits())
    lim.apply(_pos("BTC", 2.0))
    lim.apply(_pos("ETH", -3.0))
    assert lim.gross_notional() == pytest.approx(500.0)
    assert lim.net_notional() == pytest.approx(-100.0)
    lim.apply(_pos("BTC", 0.0))
    assert "BTC" not in lim.positions
