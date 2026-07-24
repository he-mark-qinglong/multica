"""Tests for the VPVR migration to ``_shared/indicators/vpvr``.

Phase D of PLAN_20260724: ``strategies/_indicators/vpvr_levels.py`` is
now a re-export shim; these tests pin functional parity between the
canonical module and the shim, plus a minimal smoke check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # quant-loop/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategies" / "_indicators"))

from _shared.indicators import vpvr  # noqa: E402
import vpvr_levels  # noqa: E402  — the compatibility shim


def _synthetic_ohlcv(n: int = 300, seed: int = 7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = pd.Series(close * 1.005, index=idx)
    low = pd.Series(close * 0.995, index=idx)
    vol = pd.Series(np.abs(rng.normal(1000, 100, n)), index=idx)
    return high, low, vol


def test_shim_reexports_canonical_objects():
    """Every public name on the shim must be the *same* object as canonical."""
    for name in vpvr.__all__:
        assert getattr(vpvr_levels, name) is getattr(vpvr, name), name


def test_compute_vpvr_levels_smoke():
    high, low, vol = _synthetic_ohlcv()
    prof = vpvr.compute_vpvr_levels(high, low, vol)
    assert isinstance(prof, vpvr.VolumeProfile)
    assert prof.val_price < prof.poc_price < prof.vah_price
    assert prof.total_volume > 0
    assert len(prof.hvn_zones) > 0
    assert len(prof.lvn_zones) > 0


def test_shim_and_canonical_results_identical():
    high, low, vol = _synthetic_ohlcv()
    a = vpvr.compute_vpvr_levels(high, low, vol)
    b = vpvr_levels.compute_vpvr_levels(high, low, vol)
    assert a.poc_price == b.poc_price
    assert a.vah_price == b.vah_price
    assert a.val_price == b.val_price
    np.testing.assert_array_equal(a.volume, b.volume)
