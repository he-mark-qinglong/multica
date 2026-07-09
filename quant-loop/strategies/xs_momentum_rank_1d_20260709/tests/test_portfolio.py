"""Unit tests for portfolio.py allocation, gross cap, and risk overlays."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from portfolio import (
    PortfolioTarget,
    TargetPosition,
    daily_loss_breach,
    enforce_gross_cap,
    equal_weight_allocation,
    gross_exposure,
    monthly_pause_active,
)


def test_equal_weight_allocation_three_long_three_short():
    out = equal_weight_allocation(
        long_symbols=["A", "B", "C"],
        short_symbols=["D", "E", "F"],
        gross_target_pct=0.6,
        per_symbol_max_pct_nav=0.10,
    )
    # 3 longs, 3 shorts
    assert len(out) == 6
    longs = [p for p in out if p.side == "LONG"]
    shorts = [p for p in out if p.side == "SHORT"]
    assert len(longs) == 3
    assert len(shorts) == 3
    # With gross=0.6, K=3 each side, per_leg = 0.6/2/3 = 0.1 (matches the cap).
    for p in longs:
        assert p.weight == pytest.approx(+0.1)
        assert p.symbol in {"A", "B", "C"}
    for p in shorts:
        assert p.weight == pytest.approx(-0.1)
        assert p.symbol in {"D", "E", "F"}


def test_equal_weight_allocation_respects_per_symbol_cap():
    out = equal_weight_allocation(
        long_symbols=["A", "B", "C", "D"],
        short_symbols=[],
        gross_target_pct=0.6,
        per_symbol_max_pct_nav=0.05,
    )
    # 4 longs only on the long side, gross=0.6, per_leg would be 0.6/2/4=0.075
    # but cap is 5% so each leg clamps to 5%.
    for p in out:
        assert p.weight == pytest.approx(+0.05)


def test_gross_exposure_sums_absolute_weights():
    out = equal_weight_allocation(
        ["A", "B"], ["C", "D"], gross_target_pct=0.4, per_symbol_max_pct_nav=0.10
    )
    # per_leg = 0.4/2/2 = 0.1 -> fits under cap.
    assert gross_exposure(PortfolioTarget(asof=pd.Timestamp("2025-01-01"), positions=out)) == pytest.approx(0.4)


def test_enforce_gross_cap_scales_when_cap_violated():
    # Construct a target that exceeds gross by design.
    out = [
        TargetPosition("A", "LONG", +0.30),
        TargetPosition("B", "LONG", +0.30),
        TargetPosition("C", "SHORT", -0.30),
    ]
    t = PortfolioTarget(asof=pd.Timestamp("2025-01-01"), positions=out)
    assert gross_exposure(t) == pytest.approx(0.90)
    capped = enforce_gross_cap(t, gross_target_pct=0.6)
    assert gross_exposure(capped) == pytest.approx(0.6)
    # Each weight scales by 0.6/0.9.
    expected_scale = 0.6 / 0.9
    for orig, new in zip(out, capped.positions):
        assert new.weight == pytest.approx(orig.weight * expected_scale)


def test_enforce_gross_cap_no_op_when_under_cap():
    out = equal_weight_allocation(["A"], ["B"], gross_target_pct=0.4, per_symbol_max_pct_nav=0.20)
    t = PortfolioTarget(asof=pd.Timestamp("2025-01-01"), positions=out)
    capped = enforce_gross_cap(t, gross_target_pct=0.4)
    # 0.4 == 0.4 already, no rescaling.
    assert capped.positions[0].weight == pytest.approx(out[0].weight)
    assert capped.positions[1].weight == pytest.approx(out[1].weight)


def test_daily_loss_breach_true_when_loss_exceeds_threshold():
    # -2.5% loss on the day vs -2% threshold -> breach.
    assert daily_loss_breach(100_000.0, 97_500.0, daily_loss_flatten_pct=-0.02)
    # -1.5% loss on the day vs -2% threshold -> no breach (we haven't lost 2%).
    assert not daily_loss_breach(100_000.0, 98_500.0, daily_loss_flatten_pct=-0.02)
    # -1.5% loss on the day vs -1% threshold -> breach (-1.5% < -1%).
    assert daily_loss_breach(100_000.0, 98_500.0, daily_loss_flatten_pct=-0.01)
    # Positive return -> never a breach.
    assert not daily_loss_breach(100_000.0, 101_000.0, daily_loss_flatten_pct=-0.02)
    # Zero prior equity -> safe no-op, no breach.
    assert not daily_loss_breach(0.0, 0.0, daily_loss_flatten_pct=-0.02)


def test_monthly_pause_active_breach_only_on_dd_threshold():
    idx = pd.date_range("2025-01-01", periods=40, freq="1D", tz="UTC")
    # Equity flat for 30 bars, then a 20% drop over the next 4 bars. At idx=33
    # the equity is 80 vs the 30d-window peak of 100, i.e. a -20% drawdown.
    # Note: the index values below are arranged so equity at idx 33 is exactly 80.
    #   idx  0..29 -> 100.0 (30 bars, the warm-up)
    #   idx 30    -> 100.0
    #   idx 31    ->  93.0
    #   idx 32    ->  86.0
    #   idx 33    ->  80.0   <-- this is the bar we test on
    #   idx 34..39 -> 80.0
    eq = pd.Series(
        [100.0] * 30 + [100.0, 93.0, 86.0, 80.0] + [80.0] * 6,
        index=idx,
    )
    breach, dd = monthly_pause_active(eq, idx[33], monthly_loss_pause_pct=-0.10)
    # Peak in trailing 30d window is 100; current at idx 33 is 80 -> dd = -0.20 -> breach.
    assert breach
    assert dd == pytest.approx(-0.20)
    # Early bar (idx=5): no drop yet -> no breach.
    breach2, _ = monthly_pause_active(eq, idx[5], monthly_loss_pause_pct=-0.10)
    assert not breach2


def test_monthly_pause_active_handles_empty_series():
    breach, dd = monthly_pause_active(pd.Series(dtype=float), pd.Timestamp("2025-01-01", tz="UTC"), -0.05)
    assert not breach
    assert dd == 0.0