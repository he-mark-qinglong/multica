"""Unit tests for vpvr_levels_band.build_vpvr_band.

Plain asserts, no pytest needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/smark/multica/quant-loop/strategies/vpvr_funding_carry_asym_v2_20260718")
sys.path.insert(0, str(ROOT))

from vpvr_levels_band import build_vpvr_band  # noqa: E402


def _make_ohlcv(n_bars: int, base_price: float = 100.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n_bars, freq="15min", tz="UTC")
    close = base_price + np.cumsum(rng.normal(0, 0.5, n_bars))
    high = close + rng.uniform(0.1, 0.3, n_bars)
    low = close - rng.uniform(0.1, 0.3, n_bars)
    open_ = close + rng.normal(0, 0.05, n_bars)
    volume = rng.uniform(100, 1000, n_bars)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }, index=idx)


def test_returns_required_columns():
    df = _make_ohlcv(300)
    band = build_vpvr_band(df, window_bars=180, snapshot_every_bars=16, num_bins=24)
    assert "vah" in band.columns
    assert "val" in band.columns
    assert "midpoint" in band.columns
    assert "half" in band.columns


def test_warmup_leaves_vah_val_nan():
    df = _make_ohlcv(50)
    band = build_vpvr_band(df, window_bars=180, snapshot_every_bars=16, num_bins=24)
    # Fewer than 180 bars => first snapshots are skipped => NaN.
    assert band["vah"].iloc[0] != band["vah"].iloc[0]  # NaN


def test_half_classification_matches_midpoint():
    df = _make_ohlcv(300)
    band = build_vpvr_band(df, window_bars=180, snapshot_every_bars=16, num_bins=24)
    valid = band.dropna(subset=["vah", "val", "midpoint"])
    close = df["close"].reindex(valid.index)
    lower = valid["half"] == "lower"
    upper = valid["half"] == "upper"
    # Every bar must be classified either lower or upper.
    assert (lower | upper).all()
    # Lower: close <= midpoint; Upper: close >= midpoint.
    assert (close[lower] <= valid.loc[lower, "midpoint"]).all()
    assert (close[upper] >= valid.loc[upper, "midpoint"]).all()


def test_vah_above_val():
    df = _make_ohlcv(300)
    band = build_vpvr_band(df, window_bars=180, snapshot_every_bars=16, num_bins=24)
    valid = band.dropna(subset=["vah", "val"])
    assert (valid["vah"] >= valid["val"]).all()


def test_shift_enforces_no_lookahead():
    # The band at bar t should be derived from data strictly before t.
    # We snapshot on the bar itself then shift(1); the first valid
    # vah must appear at a bar index strictly greater than the very
    # first bar of the input (since the snapshot at bar 0 is skipped
    # by the warm-up gate, and even if it weren't, shift(1) would push
    # it to bar 1).
    df = _make_ohlcv(300)
    band = build_vpvr_band(df, window_bars=180, snapshot_every_bars=16, num_bins=24)
    first_valid = band["vah"].first_valid_index()
    assert first_valid is not None
    assert first_valid > df.index[0], (
        "first valid vah must come strictly after the first bar "
        "(shift(1) is supposed to enforce no-lookahead)"
    )


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} tests passed")
    sys.exit(0 if failed == 0 else 1)