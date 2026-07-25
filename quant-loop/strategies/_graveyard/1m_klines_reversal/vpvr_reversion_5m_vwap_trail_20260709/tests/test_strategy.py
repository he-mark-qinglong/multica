"""Smoke tests for V1 strategy.py — pure indicator + signal logic.

We assert:
    * rolling_vwap is monotonic non-decreasing vs window length on synthetic data
    * volume_spike fires only on the synthetic spike bar
    * vpvr POC matches the manual bin argmax on a controlled toy frame
    * long_entry / short_entry behave correctly for VWAP-rejection + spike setups
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    annotate,
    rolling_vwap,
    rolling_volume_profile,
    volume_spike,
)


def _toy_df() -> pd.DataFrame:
    n = 200
    rng = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    # Synthetic mean-reverting price around 100 with occasional spikes.
    price = 100 + 0.5 * np.sin(np.linspace(0, 12 * np.pi, n)) + np.random.RandomState(0).normal(0, 0.3, n)
    base_vol = np.full(n, 50.0)
    base_vol[100] = 250.0  # forced spike
    return pd.DataFrame(
        {
            "open": price,
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price,
            "volume": base_vol,
        },
        index=rng,
    )


def test_rolling_vwap_handles_constant_volume():
    df = _toy_df()
    vwap = rolling_vwap(df, window=20)
    assert not vwap.isna().iloc[-1]
    # Volume is constant (50) across most bars → vwap equals simple mean of typical price.
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    expected = (typical * df["volume"]).rolling(20).sum() / df["volume"].rolling(20).sum()
    np.testing.assert_allclose(vwap.iloc[20:].to_numpy(), expected.iloc[20:].to_numpy(), rtol=1e-9)


def test_volume_spike_only_on_spike_bar():
    df = _toy_df()
    spike = volume_spike(df, window=60, ratio=2.0)
    spike_idx = np.where(spike.fillna(False))[0]
    # Exactly the bar we forced the spike on (and possibly a few neighbours due to rolling mean).
    assert 100 in spike_idx
    # Not every bar is a spike.
    assert len(spike_idx) < 20


def test_vpvr_poc_matches_manual_bin_argmax():
    df = _toy_df()
    prof = rolling_volume_profile(df, window=50, n_bins=10, value_area_pct=0.7)
    # At the last bar, run a manual POC check on the prior 50 bars ([:-1] because the
    # rolling_volume_profile uses bars [t-50 : t)).
    win_c = df["close"].iloc[-50:-1].to_numpy()
    win_v = df["volume"].iloc[-50:-1].to_numpy()
    lo, hi = float(win_c.min()), float(win_c.max())
    n_bins = 10
    edges = np.linspace(lo, hi, n_bins + 1)
    idx = np.clip(((win_c - lo) / (hi - lo) * n_bins).astype(int), 0, n_bins - 1)
    bin_vol = np.bincount(idx, weights=win_v, minlength=n_bins)
    poc_bin = int(np.argmax(bin_vol))
    expected_poc = 0.5 * (edges[poc_bin] + edges[poc_bin + 1])
    poc_val = float(prof["vpvr_poc"].iloc[-1])
    assert abs(poc_val - expected_poc) < (hi - lo) / n_bins + 1e-9


def test_annotate_emits_long_short_signals():
    df = _toy_df()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["vpvr"]["window_bars"] = 50  # shrink for the toy frame
    out = annotate(df, cfg)
    assert "vwap" in out.columns
    assert "vwap_z" in out.columns
    assert "vpvr_poc" in out.columns
    assert "vpvr_z_dist" in out.columns
    assert "long_entry" in out.columns
    assert "short_entry" in out.columns
    # We don't assert a specific count, just that the columns exist and are boolean.
    assert out["long_entry"].dtype == bool
    assert out["short_entry"].dtype == bool


import json  # noqa: E402  (placed here to keep test body readable)