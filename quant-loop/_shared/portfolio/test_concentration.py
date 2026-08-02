"""Tests for portfolio/concentration.py (I14)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.portfolio.concentration import (
    DEFAULT_THEME, ConcentrationLimiter, ConcentrationLimits,
    ConcentrationRejection, check_concentration, theme_exposure,
)
from _shared.portfolio.exposure import Position

THEMES = {
    "BTC": "major", "ETH": "major",
    "SOL": "L1", "AVAX": "L1",
    "DOGE": "meme",
}


def _pos(sym, qty, price=100.0):
    return Position(sym, qty, price)


def test_theme_exposure_aggregates_notional():
    book = {"BTC": _pos("BTC", 2.0), "SOL": _pos("SOL", 3.0),
            "AVAX": _pos("AVAX", -1.0)}
    agg = theme_exposure(book, THEMES)
    assert agg == {"major": 200.0, "L1": 400.0}  # shorts count absolute


def test_theme_cap_rejects_and_allows():
    lim = ConcentrationLimits(theme_caps={"meme": 500.0})
    ok, reason = check_concentration({}, _pos("DOGE", 6.0), THEMES, lim)
    assert not ok and "theme cap" in reason and "meme" in reason
    ok, _ = check_concentration({}, _pos("DOGE", 5.0), THEMES, lim)
    assert ok


def test_aggregation_includes_existing_book():
    lim = ConcentrationLimits(theme_caps={"L1": 500.0})
    book = {"SOL": _pos("SOL", 3.0)}  # 300 existing in L1
    ok, reason = check_concentration(book, _pos("AVAX", 3.0), THEMES, lim)
    assert not ok  # 300 + 300 > 500
    ok, _ = check_concentration(book, _pos("AVAX", 2.0), THEMES, lim)
    assert ok


def test_replacing_position_not_additive():
    lim = ConcentrationLimits(theme_caps={"major": 1000.0})
    book = {"BTC": _pos("BTC", 9.0)}  # 900
    ok, _ = check_concentration(book, _pos("BTC", 10.0), THEMES, lim)
    assert ok  # 1000 replaces 900, at the cap


def test_other_theme_over_cap_does_not_block():
    lim = ConcentrationLimits(theme_caps={"meme": 100.0})
    book = {"DOGE": _pos("DOGE", 5.0)}  # meme already over its cap
    ok, _ = check_concentration(book, _pos("BTC", 1.0), THEMES, lim)
    assert ok


def test_closing_always_allowed():
    lim = ConcentrationLimits(theme_caps={"meme": 100.0})
    book = {"DOGE": _pos("DOGE", 5.0)}  # over cap already
    ok, _ = check_concentration(book, _pos("DOGE", 0.0), THEMES, lim)
    assert ok


def test_unmapped_symbol_uses_default_theme_and_cap():
    lim = ConcentrationLimits(default_cap=300.0)
    ok, reason = check_concentration({}, _pos("PEPE", 4.0), THEMES, lim)
    assert not ok and DEFAULT_THEME in reason
    ok, _ = check_concentration({}, _pos("PEPE", 3.0), THEMES, lim)
    assert ok


def test_no_caps_allows_everything():
    ok, reason = check_concentration(
        {}, _pos("DOGE", 1e6), THEMES, ConcentrationLimits()
    )
    assert ok and reason == ""


def test_limiter_records_rejections_and_book_unchanged():
    lim = ConcentrationLimiter(
        ConcentrationLimits(theme_caps={"meme": 500.0}), THEMES
    )
    ok, _ = lim.check(_pos("DOGE", 10.0))
    assert not ok
    assert len(lim.rejections) == 1
    rej = lim.rejections[0]
    assert isinstance(rej, ConcentrationRejection)
    assert rej.symbol == "DOGE" and rej.theme == "meme"
    assert lim.positions == {}


def test_limiter_apply_and_theme_notional():
    lim = ConcentrationLimiter(ConcentrationLimits(), THEMES)
    lim.apply(_pos("BTC", 2.0))
    lim.apply(_pos("ETH", 1.0))
    lim.apply(_pos("SOL", -3.0))
    assert lim.theme_notional() == {"major": 300.0, "L1": 300.0}
    lim.apply(_pos("BTC", 0.0))
    assert lim.theme_notional() == {"major": 100.0, "L1": 300.0}
