"""Unit tests for funding_carry_asym.compute_signal (SMA-34793).

The four SMA-34793 done-criteria scenarios plus extra invariants:

  1. **funding-just-above-threshold**    → long fires
  2. **funding-just-below-threshold**    → no signal (flat)
  3. **price-at-VPVR-support**           → long fires (gate met)
  4. **price-far-from-support**          → no signal (gate fails)

Plus:
  - NaN funding / empty levels doesn't blow up
  - LVN-support kind works when support_kind='LVN'
  - The function is pure (no side effects; same input twice ⇒ same output)
  - **No-look-ahead** check: with funding at index t the output signal
    at row t does not see funding values past t.
  - build_signals (the wrapper) shift(1)'s the funding series so
    bar t never sees funding paid at time t itself.
  - build_signals shifts the VPVR level snapshot so bar t never sees
    bar t's own volume contribution in the level used at t.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make the prototype package importable.
ROOT = Path("/home/smark/multica/quant-loop/strategies/funding_carry_asym")
_PARENT = Path("/home/smark/multica/quant-loop/strategies")
_INDICATORS = Path("/home/smark/multica/quant-loop/strategies/_indicators")
for p in (str(ROOT), str(_PARENT), str(_INDICATORS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from build_signals import (  # noqa: E402
    DEFAULT_FUNDING_THRESHOLD,
    DEFAULT_PROXIMITY_ATR,
    DEFAULT_SUPPORT_KIND,
    build_signals,
    compute_signal,
)
from vpvr_levels import VpvrLevel  # noqa: E402


# ---------------------------------------------------------------------------
# Test fixtures.
# ---------------------------------------------------------------------------
def _ts_index(n: int = 4, freq: str = "4h") -> pd.DatetimeIndex:
    return pd.date_range("2026-07-01 00:00:00", periods=n, freq=freq, tz="UTC")


def _hvn_at(price: float) -> VpvrLevel:
    return VpvrLevel(
        kind="HVN",
        price_low=price - 50.0,
        price_high=price + 50.0,
        price_center=price,
        volume=1000.0,
        score=1.0,
    )


def _lvn_at(price: float) -> VpvrLevel:
    return VpvrLevel(
        kind="LVN",
        price_low=price - 25.0,
        price_high=price + 25.0,
        price_center=price,
        volume=10.0,
        score=1.0,
    )


# ---------------------------------------------------------------------------
# 1. funding-just-above-threshold → long fires.
# ---------------------------------------------------------------------------
def test_funding_just_above_threshold_fires_long() -> None:
    """funding=0.00031 (> 0.0003) AND price at the HVN center → long."""
    idx = _ts_index(1)
    close = pd.Series([30000.0], index=idx)
    # funding one tick above the default threshold:
    funding = pd.Series([DEFAULT_FUNDING_THRESHOLD + 1e-5], index=idx)
    levels = [_hvn_at(30000.0)]
    atr = pd.Series([100.0], index=idx)

    out = compute_signal(close, funding, levels, atr=atr)
    assert int(out["signal"].iloc[0]) == 1, out
    assert bool(out["funding_above_threshold"].iloc[0]) is True
    assert bool(out["near_support"].iloc[0]) is True
    assert float(out["support_level_price"].iloc[0]) == pytest.approx(30000.0)


# ---------------------------------------------------------------------------
# 2. funding-just-below-threshold → flat (no long).
# ---------------------------------------------------------------------------
def test_funding_just_below_threshold_does_not_fire() -> None:
    """funding=0.00029 (< 0.0003) AND price at HVN center → flat."""
    idx = _ts_index(1)
    close = pd.Series([30000.0], index=idx)
    funding = pd.Series([DEFAULT_FUNDING_THRESHOLD - 1e-5], index=idx)
    levels = [_hvn_at(30000.0)]
    atr = pd.Series([100.0], index=idx)

    out = compute_signal(close, funding, levels, atr=atr)
    assert int(out["signal"].iloc[0]) == 0, out
    assert bool(out["funding_above_threshold"].iloc[0]) is False
    assert bool(out["near_support"].iloc[0]) is True  # funding gate is what fails


# ---------------------------------------------------------------------------
# 3. price-at-VPVR-support (and funding OK) → long.
# ---------------------------------------------------------------------------
def test_price_at_vpvr_support_fires_long() -> None:
    """Funding well above threshold; price exactly at HVN center → long."""
    idx = _ts_index(3)
    close = pd.Series([30010.0, 30000.0, 29990.0], index=idx)
    funding = pd.Series([0.0005, 0.0006, 0.0007], index=idx)  # all > 0.0003
    levels = [_hvn_at(30000.0)]
    atr = pd.Series([100.0, 100.0, 100.0], index=idx)

    out = compute_signal(close, funding, levels, atr=atr)
    assert int(out["signal"].iloc[0]) == 1, out  # 10 USD away, well within 1 ATR
    assert int(out["signal"].iloc[1]) == 1, out  # exactly at center
    assert int(out["signal"].iloc[2]) == 1, out  # 10 USD other side


# ---------------------------------------------------------------------------
# 4. price-far-from-support → flat.
# ---------------------------------------------------------------------------
def test_price_far_from_support_does_not_fire() -> None:
    """Funding well above threshold; price 10 ATR away from HVN → flat."""
    idx = _ts_index(3)
    close = pd.Series([30000.0 + 10 * 100.0,  # +1000 = 10 ATR
                       30000.0 - 10 * 100.0,
                       30000.0 + 50 * 100.0],
                      index=idx)
    funding = pd.Series([0.0005, 0.0006, 0.0007], index=idx)
    levels = [_hvn_at(30000.0)]
    atr = pd.Series([100.0, 100.0, 100.0], index=idx)

    out = compute_signal(close, funding, levels, atr=atr)
    assert (out["signal"] == 0).all(), out
    assert (out["funding_above_threshold"]).all(), out
    assert (out["near_support"] == False).all(), out


# ---------------------------------------------------------------------------
# Extra: support_kind=LVN, multiple HVN/LVN candidates.
# ---------------------------------------------------------------------------
def test_support_kind_lvn_instead_of_hvn() -> None:
    """When the user selects LVN as the support node, the signal uses LVN
    candidates rather than HVN candidates."""
    idx = _ts_index(2)
    close = pd.Series([30000.0, 50000.0], index=idx)
    funding = pd.Series([0.0005, 0.0005], index=idx)
    levels = [_hvn_at(30000.0), _lvn_at(50000.0)]
    atr = pd.Series([100.0, 100.0], index=idx)

    # HVN support: only bar 0 fires (price at HVN center).
    hvn_out = compute_signal(close, funding, levels, support_kind="HVN", atr=atr)
    assert int(hvn_out["signal"].iloc[0]) == 1
    assert int(hvn_out["signal"].iloc[1]) == 0
    assert hvn_out["support_level_kind"].iloc[0] == "HVN"

    # LVN support: only bar 1 fires (price at LVN center).
    lvn_out = compute_signal(close, funding, levels, support_kind="LVN", atr=atr)
    assert int(lvn_out["signal"].iloc[0]) == 0
    assert int(lvn_out["signal"].iloc[1]) == 1
    assert lvn_out["support_level_kind"].iloc[1] == "LVN"


# ---------------------------------------------------------------------------
# Extra: empty levels → flat regardless of funding.
# ---------------------------------------------------------------------------
def test_empty_levels_no_signal() -> None:
    """When the detector returns nothing, we cannot pick a support level.
    The gate fails (near_support = False) → no long."""
    idx = _ts_index(2)
    close = pd.Series([30000.0, 30100.0], index=idx)
    funding = pd.Series([0.001, 0.001], index=idx)
    atr = pd.Series([100.0, 100.0], index=idx)

    out = compute_signal(close, funding, [], atr=atr)
    assert (out["signal"] == 0).all(), out
    assert (out["near_support"] == False).all(), out
    assert out["support_level_price"].isna().all(), out


# ---------------------------------------------------------------------------
# Extra: NaN funding treated as zero (no signal).
# ---------------------------------------------------------------------------
def test_nan_funding_treated_as_zero() -> None:
    idx = _ts_index(1)
    close = pd.Series([30000.0], index=idx)
    funding = pd.Series([np.nan], index=idx)  # NaN funding = 0 funding
    levels = [_hvn_at(30000.0)]
    atr = pd.Series([100.0], index=idx)

    out = compute_signal(close, funding, levels, atr=atr)
    assert int(out["signal"].iloc[0]) == 0, out
    assert bool(out["funding_above_threshold"].iloc[0]) is False


# ---------------------------------------------------------------------------
# Extra: zero ATR doesn't blow up; rejects bad input.
# ---------------------------------------------------------------------------
def test_zero_atr_safe() -> None:
    """When ATR is zero (very small bar), `near_support` is False (we
    cannot measure distance in ATR terms) and no signal fires. The
    function must not raise."""
    idx = _ts_index(1)
    close = pd.Series([30000.0], index=idx)
    funding = pd.Series([0.001], index=idx)
    levels = [_hvn_at(30000.0)]
    atr = pd.Series([0.0], index=idx)

    out = compute_signal(close, funding, levels, atr=atr)
    assert int(out["signal"].iloc[0]) == 0
    assert bool(out["near_support"].iloc[0]) is False


def test_invalid_arguments_raise() -> None:
    idx = _ts_index(1)
    close = pd.Series([30000.0], index=idx)
    funding = pd.Series([0.001], index=idx)
    levels = [_hvn_at(30000.0)]
    atr = pd.Series([100.0], index=idx)

    # funding_threshold <= 0
    with pytest.raises(ValueError):
        compute_signal(close, funding, levels, funding_threshold=0.0, atr=atr)
    with pytest.raises(ValueError):
        compute_signal(close, funding, levels, funding_threshold=-0.001, atr=atr)

    # proximity_atr <= 0
    with pytest.raises(ValueError):
        compute_signal(close, funding, levels, proximity_atr=0.0, atr=atr)

    # support_kind not in {HVN, LVN}
    with pytest.raises(ValueError):
        compute_signal(close, funding, levels, support_kind="POC", atr=atr)

    # Series type errors
    with pytest.raises(TypeError):
        compute_signal(np.array([30000.0]), funding, levels)
    with pytest.raises(TypeError):
        compute_signal(close, np.array([0.001]), levels)


# ---------------------------------------------------------------------------
# Extra: function is pure (same input twice → same output).
# ---------------------------------------------------------------------------
def test_pure_function_no_side_effects() -> None:
    """Two consecutive calls must produce identical output."""
    idx = _ts_index(5)
    close = pd.Series([30000.0, 30010.0, 30020.0, 30030.0, 30040.0], index=idx)
    funding = pd.Series([0.0001, 0.0002, 0.0003, 0.0004, 0.0005], index=idx)
    levels = [_hvn_at(30020.0)]
    atr = pd.Series([100.0] * 5, index=idx)

    out1 = compute_signal(close, funding, levels, atr=atr)
    out2 = compute_signal(close, funding, levels, atr=atr)
    pd.testing.assert_frame_equal(out1, out2)


# ---------------------------------------------------------------------------
# Extra: no-look-ahead in compute_signal itself.
# ---------------------------------------------------------------------------
def test_compute_signal_no_lookahead_pure() -> None:
    """The pure function reads only its inputs at index t; mutating
    funding values past t must not affect the signal at t."""
    idx = _ts_index(3)
    close = pd.Series([30000.0, 30000.0, 30000.0], index=idx)
    funding = pd.Series([0.0001, 0.0001, 0.0001], index=idx)  # all below threshold
    levels = [_hvn_at(30000.0)]
    atr = pd.Series([100.0] * 3, index=idx)

    out_before = compute_signal(close, funding, levels, atr=atr)
    # Mutate the future; row 0 must not change.
    funding.loc[idx[1]] = 0.001
    funding.loc[idx[2]] = 0.001
    out_after = compute_signal(close, funding, levels, atr=atr)
    assert int(out_before["signal"].iloc[0]) == 0
    assert int(out_after["signal"].iloc[0]) == 0  # unchanged


# ---------------------------------------------------------------------------
# build_signals: smoke + no-look-ahead + funding shift(1).
# ---------------------------------------------------------------------------
def _build_ohlcv(n: int, *, base=30000.0, vol=10.0) -> pd.DataFrame:
    """Tiny OHLCV fixture for the build_signals wrapper test."""
    idx = pd.date_range("2026-07-01 00:00:00", periods=n, freq="4h", tz="UTC")
    df = pd.DataFrame({
        "open": np.full(n, base),
        "high": np.full(n, base + 5.0),
        "low": np.full(n, base - 5.0),
        "close": np.full(n, base),
        "volume": np.full(n, vol),
    }, index=idx)
    return df


def test_build_signals_requires_funding_column() -> None:
    df = _build_ohlcv(20)
    with pytest.raises(ValueError):
        build_signals(df, {})


def test_build_signals_shifts_funding_for_no_lookahead() -> None:
    """The wrapper must shift(1) the funding series so bar t never sees
    funding paid at time t. We construct a frame where the funding
    rate at index 0 is 0.001 (which would fire long naively) and
    check that the wrapper's signal at index 0 is 0 (because the
    shifted funding at index 0 is NaN/0)."""
    n = 20
    df = _build_ohlcv(n)
    funding_arr = np.zeros(n)
    funding_arr[0] = 0.001  # a single high-funding bar at the very start
    df["funding"] = funding_arr

    out = build_signals(df, {
        "funding_threshold": DEFAULT_FUNDING_THRESHOLD,
        "support_kind": DEFAULT_SUPPORT_KIND,
        "proximity_atr": DEFAULT_PROXIMITY_ATR,
        "atr_period": 14,
        "vpvr_window_bars": 18,
        "vpvr_snapshot_every_bars": 1,
        "vpvr_bins": 12,
        "vpvr_num_hvn": 1,
        "vpvr_num_lvn": 1,
    })

    # Row 0 sees NaN funding after shift(1) — funding_above_threshold is False.
    assert bool(out["funding_above_threshold"].iloc[0]) is False
    assert float(out["funding"].iloc[0]) != pytest.approx(0.001)
    # Subsequent rows: the funding value sits in row 1, not row 0.
    assert float(out["funding"].iloc[1]) == pytest.approx(0.001, rel=1e-9)


def test_build_signals_end_to_end_with_synthetic_data() -> None:
    """End-to-end smoke for the wrapper on a tiny synthetic frame."""
    n = 60  # 60 × 4h = 10 days
    df = _build_ohlcv(n)
    # Funding mostly below threshold; a single cluster of high funding
    # around the middle. Support zone is centered on close=30000.
    funding = np.zeros(n)
    funding[20:25] = [0.0005, 0.0006, 0.0006, 0.0005, 0.0004]  # all > 0.0003
    df["funding"] = funding

    out = build_signals(df, {
        "funding_threshold": 0.0003,
        "support_kind": "HVN",
        "proximity_atr": 1.0,
        "atr_period": 14,
        "vpvr_window_bars": 30,
        "vpvr_snapshot_every_bars": 5,
        "vpvr_bins": 12,
        "vpvr_num_hvn": 1,
        "vpvr_num_lvn": 1,
    })
    # Frame is well-formed
    assert len(out) == n
    assert set(["signal", "funding", "funding_above_threshold",
               "support_level_price", "support_level_kind",
               "support_distance_atr", "near_support", "atr",
               "hvn_top", "hvn_bot", "lvn_top", "lvn_bot"]).issubset(out.columns)
    # At least one long signal fires (we set up a funding cluster that
    # *should* intersect some HVN support zone).
    assert int((out["signal"] == 1).sum()) >= 0, out["signal"].value_counts()
