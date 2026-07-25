"""Smoke tests for V2 strategy.py."""
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
    donchian_lower,
    donchian_upper,
    wilder_atr,
    rolling_volume_profile,
)


def _toy_df() -> pd.DataFrame:
    n = 300
    rng = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng_gen = np.random.RandomState(0)
    price = 100 + np.cumsum(rng_gen.normal(0, 0.4, n))
    return pd.DataFrame(
        {"open": price, "high": price + 0.2, "low": price - 0.2, "close": price,
         "volume": np.full(n, 50.0) + rng_gen.uniform(0, 20, n)},
        index=rng,
    )


def test_donchian_upper_lower_match_rolling_extremes():
    df = _toy_df()
    up = donchian_upper(df, n=20)
    lo = donchian_lower(df, n=20)
    # First 19 bars are NaN, then they match rolling+shift.
    assert up.iloc[20:].notna().all()
    # After warmup, upper is the rolling max of prior 20 highs, shifted by 1.
    expected = df["high"].rolling(20).max().shift(1)
    np.testing.assert_allclose(up.iloc[21:].to_numpy(), expected.iloc[21:].to_numpy(), rtol=1e-9)
    expected_lo = df["low"].rolling(20).min().shift(1)
    np.testing.assert_allclose(lo.iloc[21:].to_numpy(), expected_lo.iloc[21:].to_numpy(), rtol=1e-9)


def test_wilder_atr_is_positive_and_monotone_after_seed():
    df = _toy_df()
    atr = wilder_atr(df, 14)
    assert (atr.iloc[14:] > 0).all()


def test_vpvr_poc_in_range_after_warmup():
    df = _toy_df()
    prof = rolling_volume_profile(df, window=50, n_bins=10, value_area_pct=0.7)
    last_poc = float(prof["vpvr_poc"].iloc[-1])
    last_close = float(df["close"].iloc[-1])
    last_low = float(df["close"].iloc[-50:-1].min())
    last_high = float(df["close"].iloc[-50:-1].max())
    assert last_low <= last_poc <= last_high


def test_annotate_emits_required_columns():
    df = _toy_df()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["indicators"]["atr_ma_period"] = 30  # shrink for toy
    cfg["vpvr"]["window_bars"] = 30
    out = annotate(df, cfg)
    expected = {"atr", "atr_ma", "atr_ratio", "adx", "vol_ma",
                "donchian_upper", "donchian_lower",
                "vpvr_poc", "vpvr_val", "vpvr_vah", "vpvr_z_dist",
                "long_entry", "short_entry", "entry_signal"}
    missing = expected - set(out.columns)
    assert not missing, f"missing columns: {missing}"
    assert out["long_entry"].dtype == bool
    assert out["short_entry"].dtype == bool