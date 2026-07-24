"""Smoke tests for V5 strategy.py — HVN levels + breakout logic."""
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
    rolling_hvn_levels,
    wilder_atr,
)


def _toy_df() -> pd.DataFrame:
    n = 600
    rng = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    rng_gen = np.random.RandomState(0)
    price = 100 + np.cumsum(rng_gen.normal(0, 0.2, n))
    # Force a high-volume region around index 200-220 (a "node").
    vol = np.full(n, 50.0)
    vol[200:220] = 200.0
    return pd.DataFrame(
        {"open": price, "high": price + 0.1, "low": price - 0.1, "close": price,
         "volume": vol},
        index=rng,
    )


def test_rolling_hvn_levels_emit_columns():
    df = _toy_df()
    out = rolling_hvn_levels(df, window=120, n_bins=20, z_threshold=1.5)
    assert set(out.columns) == {"hvn_upper", "hvn_lower", "vpvr_poc"}
    # After warmup, upper >= lower, and both should be in the recent price range.
    valid = out.iloc[200:].dropna()
    assert (valid["hvn_upper"] >= valid["hvn_lower"]).all()


def test_wilder_atr_is_finite():
    df = _toy_df()
    atr = wilder_atr(df, 14)
    assert np.isfinite(atr.iloc[14:]).all()


def test_annotate_emits_required_columns():
    df = _toy_df()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["vpvr"]["window_bars"] = 120
    out = annotate(df, cfg)
    expected = {"atr", "vol_ma", "vol_spike",
                "hvn_upper", "hvn_lower", "vpvr_poc",
                "long_entry", "short_entry", "entry_signal"}
    assert expected <= set(out.columns)
    assert out["long_entry"].dtype == bool
    assert out["short_entry"].dtype == bool


def test_long_entry_only_when_close_above_hvn_upper_plus_buffer():
    df = _toy_df()
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["vpvr"]["window_bars"] = 120
    out = annotate(df, cfg)
    # On bars where long_entry fires, close > hvn_upper + buffer must hold.
    fired = out[out["long_entry"].fillna(False)].index
    for date in fired[:10]:
        row = out.loc[date]
        buf = cfg["entry"]["hvn_break_buffer_atr"] * float(row["atr"])
        assert float(row["close"]) > float(row["hvn_upper"]) + buf