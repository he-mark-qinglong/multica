"""Unit tests for the queue-position-aware backtest wrapper (B6)."""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.queue_position import fill_probability
from _shared.queue_aware_backtest import (
    LimitTrade,
    QueueAwareConfig,
    compare_queue_impact,
    run_queue_aware_backtest,
)
from _shared.run_backtest import Trade, run_backtest


def _bars(n: int = 400, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 50_000.0 * np.exp(np.cumsum(rng.normal(1e-4, 0.003, size=n)))
    return pd.DataFrame({"close": close}, index=idx)


def _schedule(bars: pd.DataFrame, n_trades: int = 12, **kw) -> list[LimitTrade]:
    """Non-overlapping limit-entry trades every ~30 bars."""
    trades = []
    for i in range(n_trades):
        ei = 10 + i * 30
        trades.append(
            LimitTrade(
                entry_ts=bars.index[ei],
                exit_ts=bars.index[ei + 20],
                direction="long" if i % 2 == 0 else "short",
                size_fraction=0.8,
                **kw,
            )
        )
    return trades


def test_market_orders_pass_through_unchanged() -> None:
    bars = _bars()
    trades = _schedule(bars, order_type="market")
    qa = run_queue_aware_backtest(bars, trades)
    naive = run_backtest(
        bars,
        [Trade(t.entry_ts, t.exit_ts, t.direction, t.size_fraction) for t in trades],
    )
    pd.testing.assert_series_equal(qa["equity"], naive["equity"])
    assert qa["fill_rate"] == 1.0
    assert all(d.fill_probability == 1.0 for d in qa["decisions"])


def test_expected_mode_scales_size_by_fill_probability() -> None:
    bars = _bars()
    trades = _schedule(bars, ticks_from_best=1, seconds_in_queue=10.0)
    qa = run_queue_aware_backtest(bars, trades, config=QueueAwareConfig(mode="expected"))
    p = fill_probability(10.0, 1, 0.13)
    assert 0.0 < p < 1.0
    for d in qa["decisions"]:
        assert d.filled
        assert d.fill_ratio == pytest.approx(p)
    # Queue-aware sizes are strictly smaller than naive -> different equity.
    naive = run_backtest(
        bars,
        [Trade(t.entry_ts, t.exit_ts, t.direction, t.size_fraction) for t in trades],
    )
    assert not np.allclose(qa["equity"].to_numpy(), naive["equity"].to_numpy())
    assert qa["n_trades"] == naive["n_trades"]  # same count, smaller notional


def test_simulated_mode_drops_unfilled_entries() -> None:
    bars = _bars()
    # Deep queue + long wait -> P(fill) ~ 0: everything is dropped.
    trades = _schedule(bars, ticks_from_best=5, seconds_in_queue=3600.0)
    qa = run_queue_aware_backtest(
        bars, trades, config=QueueAwareConfig(mode="simulated", seed=5)
    )
    assert qa["n_entries_filled"] == 0
    assert qa["n_trades"] == 0
    assert (qa["equity"] == 100_000.0).all()


def test_simulated_mode_is_seed_reproducible() -> None:
    bars = _bars()
    trades = _schedule(bars, ticks_from_best=0, seconds_in_queue=1.0)
    cfg = QueueAwareConfig(mode="simulated", seed=123)
    a = run_queue_aware_backtest(bars, trades, config=cfg)
    b = run_queue_aware_backtest(bars, trades, config=cfg)
    assert [d.filled for d in a["decisions"]] == [d.filled for d in b["decisions"]]
    pd.testing.assert_series_equal(a["equity"], b["equity"])


def test_expected_mode_min_probability_threshold() -> None:
    bars = _bars()
    trades = _schedule(bars, ticks_from_best=3, seconds_in_queue=600.0)
    qa = run_queue_aware_backtest(
        bars,
        trades,
        config=QueueAwareConfig(mode="expected", min_fill_probability=0.5),
    )
    assert qa["n_entries_filled"] == 0  # all below threshold


def test_compare_queue_impact_report() -> None:
    bars = _bars()
    trades = _schedule(bars, ticks_from_best=1, seconds_in_queue=20.0)
    report = compare_queue_impact(
        bars, trades, config=QueueAwareConfig(mode="expected")
    )
    fr = report["fill_report"]
    assert len(fr) == len(trades)
    assert (fr["queue_aware_size"] <= fr["naive_size"]).all()
    assert (fr["queue_aware_size"] < fr["naive_size"]).any()
    assert report["n_trades_naive"] == report["n_trades_queue_aware"]
    assert report["fill_rate"] == 1.0  # expected mode: everything "fills" partially
    assert report["return_diff_pct"] == pytest.approx(
        report["total_return_queue_aware_pct"] - report["total_return_naive_pct"]
    )


def test_compare_queue_impact_simulated_counts_differ() -> None:
    bars = _bars()
    trades = _schedule(bars, ticks_from_best=2, seconds_in_queue=120.0)
    report = compare_queue_impact(
        bars, trades, config=QueueAwareConfig(mode="simulated", seed=7)
    )
    assert report["n_trades_queue_aware"] <= report["n_trades_naive"]
    assert report["fill_rate"] <= 1.0
    assert report["fill_report"]["filled"].dtype == bool
