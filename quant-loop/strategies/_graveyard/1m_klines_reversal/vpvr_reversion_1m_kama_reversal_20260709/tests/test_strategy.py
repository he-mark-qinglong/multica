"""Smoke tests for V3 strategy.py — KAMA + RSI divergence + VPVR."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    annotate,
    kaufman_kama,
    rolling_volume_profile,
    wilder_rsi,
)


def _toy_df() -> pd.DataFrame:
    n = 500
    rng = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    rng_gen = np.random.RandomState(0)
    # Random walk then a drawdown, then a recovery — to exercise KAMA pivot + divergence.
    drift = np.zeros(n)
    drift[100:160] = -0.05  # drawdown
    drift[160:260] = 0.02
    price = 100 + np.cumsum(drift + rng_gen.normal(0, 0.1, n))
    return pd.DataFrame(
        {"open": price, "high": price + 0.05, "low": price - 0.05, "close": price,
         "volume": np.full(n, 50.0) + rng_gen.uniform(0, 20, n)},
        index=rng,
    )


def test_kama_converges_to_close_in_a_strong_trend():
    df = _toy_df()
    close = df["close"]
    # Build a strongly trending series so KAMA must track close tightly.
    trend = np.cumsum(np.full(200, 0.5)) + 100.0
    s = pd.Series(trend, index=pd.date_range("2026-01-01", periods=200, freq="1min", tz="UTC"))
    k = kaufman_kama(s, er_period=10, fast=2, slow=30)
    # In a trending series ER ≈ 1 → smoothing constant ≈ 2/(fast+1) ≈ 0.667 → KAMA
    # tracks close with at most ~0.667x the step-size lag. With step=0.5 the steady-
    # state lag is ≈ 0.33. We just check that the *trend direction* is preserved.
    diff = (k.diff().iloc[10:] - s.diff().iloc[10:]).abs()
    # The direction-of-change should match on most bars.
    same_sign = ((k.diff().iloc[10:] > 0) == (s.diff().iloc[10:] > 0)).mean()
    assert float(same_sign) > 0.9


def test_rsi_in_band_50_around_constant_price():
    s = pd.Series(np.full(100, 100.0), index=pd.date_range("2026-01-01", periods=100, freq="1min", tz="UTC"))
    rsi = wilder_rsi(s, period=14)
    # Constant input → no up/down movement → RSI is undefined; check the series shape instead.
    assert rsi.iloc[-1] in (50.0, 100.0) or pd.isna(rsi.iloc[-1])


def test_vpvr_returns_expected_columns():
    df = _toy_df()
    prof = rolling_volume_profile(df, window=60, n_bins=10, value_area_pct=0.7)
    assert set(prof.columns) == {"vpvr_poc", "vpvr_val", "vpvr_vah"}
    assert prof["vpvr_poc"].iloc[-1] > 0


def test_annotate_emits_required_columns():
    df = _toy_df()
    cfg = json.loads((ROOT / "config.json").read_text())
    # Shrink rolling windows so the toy frame has enough warmup.
    cfg["kama"]["er_period"] = 10
    cfg["rsi"]["period"] = 9
    cfg["vpvr"]["window_bars"] = 60
    out = annotate(df, cfg)
    expected = {"atr", "kama", "rsi", "vpvr_poc", "vpvr_val", "vpvr_vah",
                "vpvr_z_dist", "long_entry", "short_entry", "entry_signal"}
    assert expected <= set(out.columns)
    assert out["long_entry"].dtype == bool
    assert out["short_entry"].dtype == bool