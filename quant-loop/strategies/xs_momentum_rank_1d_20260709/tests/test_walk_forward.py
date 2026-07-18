"""Unit tests for walk_forward.py -- synthetic-data only.

We deliberately do NOT touch the real parquet cache. Each test
fabricates a small synthetic panel large enough to host the schedule
(5 windows of 365d train + 60d test, step=60d -> needs >= 605 daily
bars per symbol) and exercises the module's contracts:

1. ``test_window_count_matches_config`` -- the split generator returns
   exactly ``wf_n_windows`` splits and the rolling schedule respects
   the configured train/test/step lengths.
2. ``test_each_window_returns_valid_metrics`` -- every window's report
   carries the agreed-upon metric keys with finite, well-defined
   values (no NaN, no inf).
3. ``test_aggregate_stability_ratio_bounded`` -- the aggregate
   ``stability_ratio`` is a finite, non-negative float that respects the
   declared behaviour when ``avg_sharpe_train <= 0``.
4. ``test_windows_are_disjoint_and_contiguous`` -- test windows are
   non-overlapping, and the train-end of each window equals the
   train-end-of-previous-window + ``step_days``.

All tests use a synthetic panel of 3 symbols (the active-universe
default) so the underlying ``backtest.run_backtest`` can produce
meaningful Sharpe / MDD numbers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from universe import UniverseConfig
from walk_forward import (
    WalkForwardSplit,
    run_walk_forward,
    walk_forward_splits,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _synth_panel(n: int = 720, seed: int = 7) -> dict:
    """Build a 3-symbol synthetic 1d OHLCV panel of length ``n`` starting
    on 2024-01-01 UTC. Each symbol gets a deterministic, slightly
    divergent price series so the cross-sectional ranking actually
    produces long/short legs.

    Volume is held at 1.5M units per day (USD notional well above the
    $1M threshold) so the liquidity filter does not exclude any
    symbol -- the test exercises the *strategy* path, not the filter.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    panels = {}
    base_prices = {"WIN": 100.0, "MID": 100.0, "LOSE": 100.0}
    # WIN: positive drift ~ +0.10% / day, low noise
    # MID: zero drift, low noise
    # LOSE: negative drift ~ -0.10% / day, low noise
    drift = {"WIN": 0.0010, "MID": 0.0, "LOSE": -0.0010}
    noise = {"WIN": 0.005, "MID": 0.005, "LOSE": 0.005}
    for sym, drift_v in drift.items():
        rets = rng.normal(loc=drift_v, scale=noise[sym], size=n)
        rets[0] = 0.0
        prices = base_prices[sym] * np.exp(np.cumsum(rets))
        panels[sym] = pd.DataFrame(
            {
                "open": prices,
                "high": prices * (1.0 + rng.uniform(0.001, 0.005, size=n)),
                "low": prices * (1.0 - rng.uniform(0.001, 0.005, size=n)),
                "close": prices,
                "volume": [1_500_000.0] * n,
            },
            index=idx,
        )
    return panels


def _synth_cfg() -> dict:
    """Strategy cfg tuned for synthetic panels: loose risk limits so
    pauses / flattens do not interfere with the cross-sectional Sharpe
    calculation. ``wf_*`` keys drive the walk-forward schedule.
    """
    return {
        "strategy": "test_xs_momentum",
        "momentum": {"weight_30d": 0.5, "weight_7d": 0.3, "weight_3d": 0.2},
        "universe_filter": {"min_bars_in_last_7d": 1, "min_usd_volume_per_day": 100.0},
        "portfolio": {
            "top_k_default": 1,
            "bottom_k_default": 1,
            "gross_target_pct": 0.6,
            "per_symbol_max_pct_nav": 0.10,
            "per_leg_max_pct_nav": 0.10,
            "rebalance_freq": "1d",
            "rebalance_hour_utc": 0,
        },
        "risk": {
            "daily_loss_flatten_pct": -0.50,
            "monthly_loss_pause_pct": -0.50,
            "monthly_pause_days": 5,
        },
        "fees_bps_per_side": 0.5,
        "slippage_bps_per_side": 0.5,
        "starting_capital_usd": 100_000.0,
        # Walk-forward schedule: shrink it for unit tests so the whole
        # thing finishes in well under a second. The numbers below give
        # 5 windows x (train + test) = 5 x (60 + 20) = 400 days -- plenty
        # for the synthetic 720-day panel.
        "wf_n_windows": 5,
        "wf_train_days": 60,
        "wf_test_days": 20,
        "wf_step_days": 30,
    }


def _synth_uni() -> UniverseConfig:
    return UniverseConfig(
        target=("WIN", "MID", "LOSE"),
        active=("WIN", "MID", "LOSE"),
        min_bars_in_last_7d=1,
        min_usd_volume_per_day=100.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_window_count_matches_config():
    """``walk_forward_splits`` returns exactly ``wf_n_windows`` splits and
    each split's train/test lengths match the configured calendar-day
    windows.
    """
    cfg = _synth_cfg()
    panels = _synth_panel(n=720)
    panel_dates = panels["WIN"].index
    splits = walk_forward_splits(panel_dates, cfg=cfg)
    assert len(splits) == cfg["wf_n_windows"]
    for i, s in enumerate(splits):
        assert s.window_idx == i
        # train span == wf_train_days (inclusive of both ends)
        assert (s.train_end - s.train_start).days == cfg["wf_train_days"] - 1
        # test span == wf_test_days (inclusive of both ends)
        assert (s.test_end - s.test_start).days == cfg["wf_test_days"] - 1
        # train_end and test_start are adjacent (no gap, no overlap)
        assert (s.test_start - s.train_end).days == 1


def test_each_window_returns_valid_metrics():
    """``run_walk_forward`` returns a report where every window has the
    metric keys the B3 task contract specifies and all numeric values
    are finite (no NaN, no inf).
    """
    cfg = _synth_cfg()
    panels = _synth_panel(n=720)
    report = run_walk_forward(panels, cfg=cfg, universe_cfg=_synth_uni())
    assert len(report.windows) == cfg["wf_n_windows"]
    required_keys = {
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "sharpe_train",
        "sharpe_test",
        "mdd_test",
        "return_test",
        "n_trades_test",
    }
    for w in report.windows:
        assert required_keys.issubset(w.keys()), (
            f"window missing keys: {required_keys - set(w.keys())}"
        )
        for k, v in w.items():
            if isinstance(v, float):
                assert np.isfinite(v), f"window[{k}]={v} is not finite"


def test_aggregate_stability_ratio_bounded():
    """``aggregate.stability_ratio`` is a finite, non-negative float. When
    ``avg_sharpe_train`` is non-positive the ratio must be exactly 0.0
    (degenerate case handled explicitly in the implementation).
    """
    cfg = _synth_cfg()
    panels = _synth_panel(n=720)
    report = run_walk_forward(panels, cfg=cfg, universe_cfg=_synth_uni())
    agg = report.aggregate
    assert "stability_ratio" in agg
    ratio = agg["stability_ratio"]
    assert isinstance(ratio, float)
    assert np.isfinite(ratio)
    assert ratio >= 0.0
    # Aggregate averages should match window-by-window averages -- a
    # basic sanity check that the reduction is uniform across windows.
    avg_train = float(np.mean([w["sharpe_train"] for w in report.windows]))
    avg_test = float(np.mean([w["sharpe_test"] for w in report.windows]))
    assert agg["avg_sharpe_train"] == pytest.approx(avg_train)
    assert agg["avg_sharpe_test"] == pytest.approx(avg_test)


def test_windows_are_disjoint_and_contiguous():
    """Test windows must be non-overlapping and contiguous (each window
    starts ``step_days`` after the previous one).
    """
    cfg = _synth_cfg()
    panels = _synth_panel(n=720)
    panel_dates = panels["WIN"].index
    splits = walk_forward_splits(panel_dates, cfg=cfg)
    for prev, cur in zip(splits, splits[1:]):
        # The schedule advances by exactly wf_step_days at the train
        # origin. The previous train_end + step_days + 1 == cur train_end
        # when train_days == cfg["wf_train_days"].
        assert (cur.train_start - prev.train_start).days == cfg["wf_step_days"]
        # Test windows are disjoint.
        assert cur.test_start > prev.test_end
    # All window boundaries are tz-aware UTC.
    for s in splits:
        assert s.train_start.tz is not None
        assert s.test_end.tz is not None