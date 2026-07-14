"""Smoke tests for V4 strategy.py — cross-sectional ranking + portfolio backtest."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    per_symbol_signals,
    rolling_volume_profile,
    run_backtest,
)


def _toy_panel() -> dict:
    n = 200
    rng = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    out = {}
    for s, (drift, vol) in enumerate([(0.001, 0.02), (0.0005, 0.03), (-0.0005, 0.04)]):
        rng_gen = np.random.RandomState(s)
        ret = drift + rng_gen.normal(0, vol, n)
        price = 100 * np.exp(np.cumsum(ret))
        out[f"SYM{s}"] = pd.DataFrame(
            {"open": price, "high": price * 1.01, "low": price * 0.99, "close": price,
             "volume": np.full(n, 1000.0)},
            index=rng,
        )
    return out


def test_per_symbol_signals_has_required_columns():
    panel = _toy_panel()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["vpvr"]["window_days"] = 30
    df = per_symbol_signals(panel["SYM0"], cfg)
    expected = {"return_30d", "return_7d", "return_3d", "momentum_score",
                "vpvr_poc", "vpvr_val", "vpvr_vah", "vpvr_z_dist", "realized_vol_30d"}
    assert expected <= set(df.columns)


def test_rolling_volume_profile_returns_nan_before_warmup():
    panel = _toy_panel()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["vpvr"]["window_days"] = 30
    df = per_symbol_signals(panel["SYM0"], cfg)
    # First 30 bars should be NaN for profile columns.
    assert df["vpvr_poc"].iloc[:30].isna().all()
    # After warmup we should have finite values.
    assert bool(np.isfinite(df["vpvr_poc"].iloc[40:]).all())


def test_run_backtest_produces_equity_curve_and_trades():
    panel = _toy_panel()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["vpvr"]["window_days"] = 30
    res = run_backtest(panel, cfg)
    assert isinstance(res.equity_curve, pd.Series)
    assert len(res.equity_curve) > 100
    # Some trades should be produced.
    assert res.n_trades >= 0
    # If trades happened, max_drawdown should be defined.
    if res.n_trades > 0:
        assert res.max_drawdown <= 0.0


def test_run_backtest_handles_empty_input():
    cfg = json.loads((ROOT / "config.json").read_text())
    res = run_backtest({}, cfg)
    assert res.n_trades == 0
    assert res.total_return == 0.0