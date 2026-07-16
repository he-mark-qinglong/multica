"""Quick smoke tests for vpvr_levels.

Three scenarios:
  1. Synthetic bar: single bar with known volume split into a known
     number of bins → POC must equal the bar mid and value area must
     cover it.
  2. Two-cluster synthetic: clear HVN/LVN structure → POC and zones
     should be at expected centers.
  3. Real BTCUSDT 4h bars: integration smoke test that the module
     runs end-to-end without throwing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[4] / "quant-loop"
DATASET = Path(
    os.environ.get(
        "VPVR_BTC_4H_DATA",
        ROOT / "live_data" / "BTCUSD_4h.parquet",
    )
)
sys.path.insert(0, str(ROOT / "strategies"))

from _indicators.vpvr_levels import (  # noqa: E402
    build_volume_profile,
    compute_vpvr_levels,
    find_hvn_lvn,
    find_poc,
    find_value_area,
)


def _assert_close(actual: float, expected: float, tol: float, msg: str) -> None:
    if abs(actual - expected) > tol:
        raise AssertionError(f"{msg}: expected {expected} ± {tol}, got {actual}")


def test_single_bar_profile() -> None:
    """A single bar's volume should land entirely in its [low, high] span,
    with equal weight in each bin it covers (so the POC is the first
    covered bin by argmax)."""
    high = pd.Series([110.0])
    low = pd.Series([100.0])
    volume = pd.Series([1000.0])
    centers, profile, bin_width = build_volume_profile(high, low, volume, num_bins=20)
    assert bin_width > 0
    assert np.isclose(profile.sum(), 1000.0, rtol=1e-6), profile.sum()
    # All bins fully inside [100, 110] should hold equal volume
    covered = (centers >= 100.0) & (centers <= 110.0)
    assert covered.all(), "every bin center should be inside the bar"
    covered_profile = profile[covered]
    assert np.allclose(covered_profile, covered_profile[0]), (
        f"equal-distribution violated: {covered_profile}"
    )


def test_two_cluster_profile() -> None:
    """Synthetic 3-bar dataset should yield a POC near the heavier cluster.

    Bars:
      1. 100..110, vol=100   (light cluster)
      2. 195..205, vol=900   (heavy cluster — POC expected here)
      3. 100..110, vol=100   (light cluster)
    """
    high = pd.Series([110.0, 205.0, 110.0])
    low = pd.Series([100.0, 195.0, 100.0])
    volume = pd.Series([100.0, 900.0, 100.0])
    centers, profile, bin_width = build_volume_profile(high, low, volume, num_bins=21)
    poc = find_poc(centers, profile)
    _assert_close(poc, 200.0, tol=bin_width * 1.5, msg="two-cluster POC")
    # Value area on a 70% rule should clearly include the heavy cluster.
    poc_idx = int(np.argmin(np.abs(centers - poc)))
    val, vah = find_value_area(centers, profile, bin_width, poc_idx, 0.70)
    assert val <= 200.0 <= vah, f"value area [{val}, {vah}] must include POC=200"


def test_hvn_lvn_extremes() -> None:
    """On the two-cluster dataset, HVN must include the heavy cluster
    and LVN must include the light cluster."""
    high = pd.Series([110.0, 205.0, 110.0])
    low = pd.Series([100.0, 195.0, 100.0])
    volume = pd.Series([100.0, 900.0, 100.0])
    centers, profile, bin_width = build_volume_profile(high, low, volume, num_bins=21)
    hvn_zones, lvn_zones = find_hvn_lvn(
        centers, profile, bin_width, hvn_quantile=0.80, lvn_quantile=0.20,
    )
    assert any(zone[0] <= 205.0 and zone[1] >= 195.0 for zone in hvn_zones), (
        f"HVN must include heavy cluster, got {hvn_zones}"
    )
    assert any(zone[0] <= 110.0 and zone[1] >= 100.0 for zone in lvn_zones), (
        f"LVN must include light cluster, got {lvn_zones}"
    )


def test_btc_4h_integration() -> None:
    """End-to-end smoke test on real BTC 4h data."""
    if not DATASET.exists():
        pytest.skip(
            f"BTC 4h fixture not found: {DATASET}; "
            "set VPVR_BTC_4H_DATA to run the integration test"
        )
    df = pd.read_parquet(DATASET)
    result = compute_vpvr_levels(df["high"], df["low"], df["volume"])
    assert result.total_volume > 0
    assert result.val_price < result.poc_price < result.vah_price
    assert len(result.hvn_zones) > 0
    assert len(result.lvn_zones) > 0
    print(
        f"[btc 4h] bins={len(result.price_centers)} "
        f"bin_width=${result.bin_width:.2f} "
        f"POC=${result.poc_price:.0f} "
        f"VA=[${result.val_price:.0f}, ${result.vah_price:.0f}] "
        f"hvn={len(result.hvn_zones)} lvn={len(result.lvn_zones)}"
    )


if __name__ == "__main__":
    test_single_bar_profile()
    print("✓ single-bar profile")
    test_two_cluster_profile()
    print("✓ two-cluster profile")
    test_hvn_lvn_extremes()
    print("✓ HVN/LVN extremes")
    test_btc_4h_integration()
    print("✓ BTC 4h integration")
    print("\nALL TESTS PASSED")