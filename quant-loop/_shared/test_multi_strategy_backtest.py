"""Unit tests for the multi-strategy portfolio backtest (B15)."""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.multi_strategy_backtest import (
    MultiStrategyConfig,
    StrategySpec,
    run_multi_strategy_backtest,
)
from _shared.run_backtest import Trade, run_backtest


def _bars(n: int = 600, seed: int = 21) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 50_000.0 * np.exp(np.cumsum(rng.normal(8e-5, 0.003, size=n)))
    return pd.DataFrame({"close": close}, index=idx)


def _trades(bars: pd.DataFrame, *, offset: int, step: int, hold: int,
            direction: str, size: float = 0.9) -> tuple[Trade, ...]:
    out = []
    ei = offset
    while ei + hold < len(bars) - 1:
        out.append(
            Trade(bars.index[ei], bars.index[ei + hold], direction, size)
        )
        ei += step
    return tuple(out)


def _specs(bars: pd.DataFrame) -> list[StrategySpec]:
    return [
        StrategySpec("trend", _trades(bars, offset=5, step=60, hold=40,
                                      direction="long")),
        StrategySpec("meanrev", _trades(bars, offset=25, step=50, hold=20,
                                        direction="short", size=0.6)),
        StrategySpec("carry", _trades(bars, offset=40, step=90, hold=60,
                                      direction="long", size=0.5)),
    ]


def test_equal_weight_portfolio_is_sum_of_slices() -> None:
    bars = _bars()
    specs = _specs(bars)
    out = run_multi_strategy_backtest(bars, specs)

    assert out["weights"] == pytest.approx({s.name: 1 / 3 for s in specs})
    expected = None
    for spec in specs:
        solo = run_backtest(
            bars, list(spec.trades), initial_capital=100_000.0 / 3
        )
        pd.testing.assert_series_equal(
            out["strategies"][spec.name]["equity"], solo["equity"]
        )
        expected = solo["equity"] if expected is None else expected + solo["equity"]
    pd.testing.assert_series_equal(out["portfolio_equity"], expected)


def test_explicit_weights_normalised() -> None:
    bars = _bars()
    specs = [
        StrategySpec("a", _trades(bars, offset=5, step=80, hold=30,
                                  direction="long"), weight=3.0),
        StrategySpec("b", _trades(bars, offset=20, step=70, hold=25,
                                  direction="short"), weight=1.0),
    ]
    out = run_multi_strategy_backtest(
        bars, specs, config=MultiStrategyConfig(weighting="explicit")
    )
    assert out["weights"]["a"] == pytest.approx(0.75)
    assert out["weights"]["b"] == pytest.approx(0.25)
    assert out["strategies"]["a"]["capital"] == pytest.approx(75_000.0)


def test_erc_weights_sum_to_one_and_differ_from_equal() -> None:
    bars = _bars()
    specs = _specs(bars)
    out = run_multi_strategy_backtest(
        bars, specs, config=MultiStrategyConfig(weighting="erc")
    )
    w = out["weights"]
    assert sum(w.values()) == pytest.approx(1.0)
    assert all(0.0 < v < 1.0 for v in w.values())
    # Different per-strategy vol -> weights must not all be exactly 1/3.
    assert any(abs(v - 1 / 3) > 1e-6 for v in w.values())


def test_erc_falls_back_to_equal_on_degenerate_covariance() -> None:
    bars = _bars()
    specs = [
        StrategySpec("flat", ()),  # no trades -> zero variance
        StrategySpec("active", _trades(bars, offset=5, step=60, hold=30,
                                       direction="long")),
    ]
    out = run_multi_strategy_backtest(
        bars, specs, config=MultiStrategyConfig(weighting="erc")
    )
    assert out["weights"] == {"flat": 0.5, "active": 0.5}


def test_decomposition_metrics_present_and_consistent() -> None:
    bars = _bars()
    specs = _specs(bars)
    out = run_multi_strategy_backtest(bars, specs)
    keys = {"sharpe", "annualised_pct", "total_return_pct",
            "max_drawdown_pct", "n_bars"}
    assert keys <= set(out["portfolio_metrics"])
    for name, entry in out["strategies"].items():
        assert keys <= set(entry["metrics"])
        assert entry["metrics"]["n_bars"] == len(bars)
    # Portfolio equity endpoints consistent with strategy slices.
    total = sum(e["equity"].iloc[-1] for e in out["strategies"].values())
    assert out["portfolio_equity"].iloc[-1] == pytest.approx(total)


def test_pool_account_cross_check() -> None:
    bars = _bars()
    specs = _specs(bars)
    out = run_multi_strategy_backtest(bars, specs)
    pool = out["pool_account"]
    assert pool["n_fills"] == 2 * sum(
        e["n_trades"] for e in out["strategies"].values()
    )
    assert pool["total_fees"] > 0.0
    # All positions closed -> no unrealized PnL; accounting final equity
    # should be in the same ballpark as the equity-walk portfolio result
    # (different conventions: average-cost cash ledger vs per-bar
    # compounding — allow a loose 5% band).
    walk_final = float(out["portfolio_equity"].iloc[-1])
    assert pool["unrealized_pnl"] == pytest.approx(0.0)
    assert pool["final_equity"] == pytest.approx(walk_final, rel=0.05)


def test_validation_errors() -> None:
    bars = _bars()
    with pytest.raises(ValueError, match="at least one"):
        run_multi_strategy_backtest(bars, [])
    dup = [StrategySpec("x", ()), StrategySpec("x", ())]
    with pytest.raises(ValueError, match="unique"):
        run_multi_strategy_backtest(bars, dup)
    bad = [StrategySpec("x", _trades(bars, offset=5, step=60, hold=30,
                                     direction="long"), weight=0.0)]
    with pytest.raises(ValueError, match="positive"):
        run_multi_strategy_backtest(
            bars, bad, config=MultiStrategyConfig(weighting="explicit")
        )
