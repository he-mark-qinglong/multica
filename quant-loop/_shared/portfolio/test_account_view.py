"""Tests for portfolio/account_view.py (I12)."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pandas as pd
import pytest

from _shared.portfolio.account_view import (
    POOL_ID, AccountView, Fill, build_account_views,
)


def _fill(day, sid, sym, qty, price, fee=0.0):
    return Fill(pd.Timestamp(f"2026-01-{day:02d}"), sid, sym, qty, price, fee)


def test_fill_validation():
    with pytest.raises(ValueError):
        _fill(1, "s", "BTC", 1.0, -100.0)
    with pytest.raises(ValueError):
        _fill(1, "s", "BTC", 1.0, 100.0, fee=-1.0)


def test_isolated_single_strategy_roundtrip():
    fills = [
        _fill(1, "alpha", "BTC", 1.0, 100.0, fee=1.0),
        _fill(2, "alpha", "BTC", -1.0, 120.0, fee=1.0),
    ]
    views = build_account_views(fills, initial_capital=1000.0, mode="isolated")
    v = views["alpha"]
    assert isinstance(v, AccountView)
    # cash: 1000 - 100 - 1 + 120 - 1 = 1018; realized = 20
    assert v.final_equity == pytest.approx(1018.0)
    assert v.realized_pnl == pytest.approx(20.0)
    assert v.total_fees == pytest.approx(2.0)
    assert v.positions == {}
    assert v.total_return == pytest.approx(0.018)
    assert len(v.equity_curve) == 2


def test_isolated_multi_strategy_independent():
    fills = [
        _fill(1, "alpha", "BTC", 1.0, 100.0),
        _fill(1, "beta", "ETH", 10.0, 10.0),
        _fill(2, "alpha", "BTC", -1.0, 110.0),
        _fill(2, "beta", "ETH", -10.0, 5.0),
    ]
    views = build_account_views(fills, 1000.0, mode="isolated")
    assert set(views) == {"alpha", "beta"}
    assert views["alpha"].realized_pnl == pytest.approx(10.0)
    assert views["beta"].realized_pnl == pytest.approx(-50.0)
    # Strategies do not see each other's cash.
    assert views["alpha"].initial_capital == 1000.0
    assert views["beta"].initial_capital == 1000.0


def test_unrealized_marked_to_last_price():
    fills = [
        _fill(1, "alpha", "BTC", 2.0, 100.0),
        _fill(2, "alpha", "BTC", -1.0, 130.0),  # half closed, half open
    ]
    views = build_account_views(fills, 1000.0)
    v = views["alpha"]
    assert v.positions == {"BTC": pytest.approx(1.0)}
    assert v.realized_pnl == pytest.approx(30.0)
    assert v.unrealized_pnl == pytest.approx(30.0)  # 1 * (130 - 100)
    # equity = cash (1000 - 200 + 130) + 1 * 130 = 1060
    assert v.final_equity == pytest.approx(1060.0)


def test_short_position_pnl():
    fills = [
        _fill(1, "s", "BTC", -1.0, 100.0),
        _fill(2, "s", "BTC", 1.0, 80.0),
    ]
    v = build_account_views(fills, 1000.0)["s"]
    assert v.realized_pnl == pytest.approx(20.0)
    assert v.final_equity == pytest.approx(1020.0)


def test_flip_position_resets_cost_basis():
    fills = [
        _fill(1, "s", "BTC", 1.0, 100.0),   # long 1 @100
        _fill(2, "s", "BTC", -3.0, 120.0),  # close long, open short 2 @120
        _fill(3, "s", "BTC", 2.0, 110.0),   # close short
    ]
    v = build_account_views(fills, 1000.0)["s"]
    # realized: +20 on long close, +20 on short close
    assert v.realized_pnl == pytest.approx(40.0)
    assert v.positions == {}


def test_shared_mode_pool_and_weights():
    fills = [
        _fill(1, "alpha", "BTC", 1.0, 100.0),
        _fill(1, "beta", "ETH", 10.0, 10.0),
        _fill(2, "alpha", "BTC", -1.0, 120.0),
        _fill(2, "beta", "ETH", -10.0, 12.0),
    ]
    views = build_account_views(
        fills, 1000.0, mode="shared",
        capital_weights={"alpha": 3.0, "beta": 1.0},
    )
    assert POOL_ID in views
    assert views["alpha"].initial_capital == pytest.approx(750.0)
    assert views["beta"].initial_capital == pytest.approx(250.0)
    pool = views[POOL_ID]
    assert pool.final_equity == pytest.approx(1040.0)
    assert pool.realized_pnl == pytest.approx(40.0)
    assert pool.total_return == pytest.approx(0.04)


def test_shared_equal_weight_default():
    fills = [_fill(1, "a", "BTC", 1.0, 100.0), _fill(1, "b", "ETH", 1.0, 50.0)]
    views = build_account_views(fills, 1000.0, mode="shared")
    assert views["a"].initial_capital == pytest.approx(500.0)
    assert views["b"].initial_capital == pytest.approx(500.0)


def test_bad_inputs():
    with pytest.raises(ValueError):
        build_account_views([_fill(1, "a", "BTC", 1.0, 100.0)], 1000.0, mode="nope")
    with pytest.raises(ValueError):
        build_account_views([_fill(1, "a", "BTC", 1.0, 100.0)], -5.0)
    with pytest.raises(ValueError):
        build_account_views(
            [_fill(1, "a", "BTC", 1.0, 100.0)], 1000.0, mode="shared",
            capital_weights={"other": 1.0},
        )
    assert build_account_views([], 1000.0) == {}
