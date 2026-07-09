"""Unit tests for strategy.py momentum score + ranking + selection."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strategy import (
    build_signals,
    compute_momentum_score,
    per_symbol_signals,
    rank_symbols_on,
    select_long_short,
    trailing_return,
)

CFG = {
    "momentum": {"weight_30d": 0.5, "weight_7d": 0.3, "weight_3d": 0.2},
}


def _toy_df(prices: list, start: str = "2025-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(prices), freq="1D", tz="UTC")
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1000.0] * len(prices),
        },
        index=idx,
    )


def test_trailing_return_shifts_correctly():
    s = pd.Series([100, 110, 121, 133.1], index=pd.date_range("2025-01-01", periods=4, freq="1D"))
    r = trailing_return(s, 2)
    # First two are NaN; index 2: 121/100 - 1 = 0.21; index 3: 133.1/110 - 1 = 0.21
    assert pd.isna(r.iloc[0])
    assert pd.isna(r.iloc[1])
    assert r.iloc[2] == pytest.approx(0.21)
    assert r.iloc[3] == pytest.approx(0.21)


def test_compute_momentum_score_weights():
    # At index 30 we have r30 = 0.6, r7 = 0.05, r3 = 0.01. Score = 0.5*0.6+0.3*0.05+0.2*0.01
    n = 35
    close = [100.0] * n
    close[29] = 160.0   # r30 at index 30: 160/100 - 1 = 0.6
    close[27] = 105.0   # r7 at index 30: 105/100 - 1 = 0.05
    close[29] = 101.0   # but r3 at index 30: 101/100 - 1 = 0.01
    # Actually overwrite: r3 uses index -4/-1, so set close[27] (i.e. 3 ago) to drive r3.
    close[27] = 101.0
    # final layout:
    #   r30: close[30]/close[0] - 1 = close[30]/100 - 1.0
    #   r7 : close[30]/close[23] - 1 = close[30]/100 - 1.0
    #   r3 : close[30]/close[27] - 1 = close[30]/101 - 1.0
    close[30] = 160.0
    s = pd.Series(close)
    score = compute_momentum_score(s, 0.5, 0.3, 0.2)
    r30 = 160.0 / 100.0 - 1.0
    r7 = 160.0 / 100.0 - 1.0
    r3 = 160.0 / 101.0 - 1.0
    expected = 0.5 * r30 + 0.3 * r7 + 0.2 * r3
    assert score.iloc[30] == pytest.approx(expected)


def test_per_symbol_signals_returns_expected_columns():
    df = _toy_df([100 + i for i in range(40)])
    sig = per_symbol_signals(df, CFG)
    for col in ("return_30d", "return_7d", "return_3d", "momentum_score"):
        assert col in sig.columns
    # The first 30 bars must have NaN momentum_score (no 30d return yet).
    assert sig["momentum_score"].iloc[:30].isna().all()
    # After that, some bars have real values.
    assert sig["momentum_score"].iloc[30:].notna().any()


def test_rank_symbols_on_orders_by_score():
    dates = pd.date_range("2025-01-01", periods=40, freq="1D", tz="UTC")
    sym_a = pd.Series([100.0] * 39 + [150.0], index=dates)   # best
    sym_b = pd.Series([100.0] * 39 + [120.0], index=dates)   # mid
    sym_c = pd.Series([100.0] * 39 + [80.0], index=dates)    # worst
    panel = pd.concat([sym_a.rename("A"), sym_b.rename("B"), sym_c.rename("C")], axis=1)
    rk = rank_symbols_on(panel, dates[-1])
    assert list(rk["symbol"]) == ["A", "B", "C"]
    assert list(rk["rank"]) == [1, 2, 3]


def test_select_long_short_short_universe_does_not_overlap():
    dates = pd.date_range("2025-01-01", periods=40, freq="1D", tz="UTC")
    syms = {}
    for i, name in enumerate(["A", "B", "C", "D", "E", "F"]):
        prices = [100.0 + j + i for j in range(40)]  # A highest, F lowest
        syms[name] = pd.Series(prices, index=dates)
    panel = pd.concat([s.rename(n) for n, s in syms.items()], axis=1)
    rk = rank_symbols_on(panel, dates[-1])
    ls = select_long_short(rk, top_k=3, bottom_k=3)
    # 3 longs, 3 shorts, disjoint.
    longs = {s for s, side in ls.items() if side == "LONG"}
    shorts = {s for s, side in ls.items() if side == "SHORT"}
    assert longs | shorts == {"A", "B", "C", "D", "E", "F"}
    assert longs & shorts == set()
    assert len(longs) == 3
    assert len(shorts) == 3


def test_select_long_short_handles_small_universe():
    dates = pd.date_range("2025-01-01", periods=40, freq="1D", tz="UTC")
    panel = pd.concat(
        [
            pd.Series([100.0 + j for j in range(40)], index=dates).rename("A"),
            pd.Series([100.0 - j for j in range(40)], index=dates).rename("B"),
        ],
        axis=1,
    )
    rk = rank_symbols_on(panel, dates[-1])
    # With N=2 and top_k=3/bot_k=3, the function should shrink to top=1 / bot=1.
    ls = select_long_short(rk, top_k=3, bottom_k=3)
    longs = {s for s, side in ls.items() if side == "LONG"}
    shorts = {s for s, side in ls.items() if side == "SHORT"}
    assert len(longs) == 1
    assert len(shorts) == 1
    assert longs | shorts == {"A", "B"}
    assert longs & shorts == set()


def test_select_long_short_empty_returns_empty():
    assert select_long_short(pd.DataFrame(columns=["symbol", "score", "rank"]), 3, 3) == {}


def test_build_signals_aligns_panel():
    dates = pd.date_range("2025-01-01", periods=40, freq="1D", tz="UTC")
    df = _toy_df([100 + i for i in range(40)])
    df2 = _toy_df([100 - i for i in range(40)])
    panel = build_signals({"A": df, "B": df2}, CFG)
    assert set(panel.columns) == {"A", "B"}
    # The first 30 bars must be NaN across the whole panel.
    assert panel.iloc[:30].isna().all().all()
    # After 30, at least one symbol has a real score.
    assert panel.iloc[30:].notna().any().any()