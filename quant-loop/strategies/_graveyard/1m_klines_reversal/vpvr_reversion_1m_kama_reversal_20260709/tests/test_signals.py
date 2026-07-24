"""Unit tests for vpvr_reversion_1m_kama_reversal_20260709 build_signals.

B3 evidence gate requires >= 1 PASS via `pytest -v`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_signals import build_signals


PARAMS = {
    "kama_period": 10,
    "kama_fast": 2,
    "kama_slow": 30,
    "kama_slope_lookback": 3,
    "kama_turn_threshold_atr": 0.20,
    "vpvr_window_bars": 1440,
    "vpvr_bins": 24,
    "atr_period": 14,
    "poc_atr_buffer": 0.6,
    "take_profit_atr_k": 1.5,
    "hard_stop_atr_k": 1.0,
    "max_hold_bars": 30,
    "risk_target_pct": 0.005,
    "cooldown_bars": 5,
    "fee_bps_per_fill": 4.0,
    "slippage_bps_per_fill": 1.0,
}


def _make_synthetic(n: int = 5000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2023-01-01", periods=n, freq="1min")
    returns = rng.normal(0.0, 0.0008, size=n)
    close = 100.0 * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0001, 0.0010, size=n))
    low = close * (1 - rng.uniform(0.0001, 0.0010, size=n))
    open_ = close * (1 + rng.normal(0.0, 0.0003, size=n))
    volume = rng.uniform(50.0, 150.0, size=n)
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dt)


def test_build_signals_columns():
    df = _make_synthetic(5000)
    out = build_signals(df, PARAMS)
    expected = {
        "signal", "kama", "kama_slope", "kama_turn",
        "vpvr_poc", "atr", "poc_distance_atr", "kama_slope_atr",
    }
    assert set(out.columns) >= expected


def test_signal_values_in_set():
    df = _make_synthetic(5000)
    out = build_signals(df, PARAMS)
    assert set(out["signal"].dropna().unique()).issubset({-1, 0, 1})


def test_kama_turn_values_in_set():
    df = _make_synthetic(5000)
    out = build_signals(df, PARAMS)
    valid = out["kama_turn"].dropna().unique()
    assert set(valid.tolist()).issubset({-1, 0, 1})


def test_warmup_window_has_zero_signal():
    """During warmup the rolling windows must not produce signals."""
    df = _make_synthetic(5000)
    out = build_signals(df, PARAMS)
    warmup = max(PARAMS["vpvr_window_bars"], PARAMS["kama_period"], PARAMS["atr_period"]) + PARAMS["kama_slope_lookback"]
    head = out["signal"].iloc[:warmup]
    # Allow NaN → coerce and check
    assert (head.fillna(0) == 0).all()


def test_poc_distance_atr_nonnegative():
    df = _make_synthetic(5000)
    out = build_signals(df, PARAMS)
    valid = out["poc_distance_atr"].dropna()
    assert (valid >= 0).all()


def test_atr_seed_window_yields_nan_then_finite():
    df = _make_synthetic(5000)
    out = build_signals(df, PARAMS)
    # First 13 bars the ATR window isn't seeded → NaN, bars 14+ finite.
    assert out["atr"].iloc[: PARAMS["atr_period"] - 1].isna().all()
    assert out["atr"].iloc[PARAMS["atr_period"] + 50 :].notna().all()
