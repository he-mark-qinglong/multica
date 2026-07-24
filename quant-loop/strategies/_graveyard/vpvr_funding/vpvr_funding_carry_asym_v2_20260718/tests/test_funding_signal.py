"""Unit tests for funding_signal.compute_funding_ema_signal.

Plain asserts, no pytest needed. Run with `python tests/test_funding_signal.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/smark/multica/quant-loop/strategies/vpvr_funding_carry_asym_v2_20260718")
sys.path.insert(0, str(ROOT))

from funding_signal import compute_funding_ema_signal  # noqa: E402


def _events(events):
    idx = pd.date_range("2025-01-01", periods=len(events), freq="8h", tz="UTC")
    return pd.DataFrame({"fundingRate": events}, index=idx)


def test_positive_constant_emits_above_threshold():
    events = _events([0.0002] * 30)  # constant > threshold
    bar_index = pd.date_range("2025-01-01", periods=200, freq="15min", tz="UTC")
    sig = compute_funding_ema_signal(events, bar_index, span_events=21, threshold=0.0001)
    # After warm-up, EMA ≈ 0.0002 > 0.0001.
    assert sig["funding_ema"].iloc[-1] > 0.0001
    assert sig["above_threshold"].iloc[-1]
    assert not sig["below_threshold"].iloc[-1]


def test_negative_constant_emits_below_threshold():
    events = _events([-0.0002] * 30)
    bar_index = pd.date_range("2025-01-01", periods=200, freq="15min", tz="UTC")
    sig = compute_funding_ema_signal(events, bar_index, span_events=21, threshold=0.0001)
    assert sig["funding_ema"].iloc[-1] < -0.0001
    assert sig["below_threshold"].iloc[-1]
    assert not sig["above_threshold"].iloc[-1]


def test_no_lookahead_event_aligned_with_bar_open():
    # Two events at 8h cadence; bar_index has bars straddling each event.
    # The EMA at a bar strictly BEFORE the first event must be NaN
    # (ffill of nothing) or, after shift(1), the bar that "owns" the
    # event sees the prior event's value.
    events = _events([0.0001, 0.0003])
    bar_index = pd.date_range("2025-01-01 00:00", periods=240, freq="15min", tz="UTC")
    sig = compute_funding_ema_signal(events, bar_index, span_events=21, threshold=0.0001, shift_bars=1)
    # The first bar of the index is before the first event at 00:00,
    # so after ffill-onto-bar it stays NaN. After shift(1), it must
    # still be NaN (no peek at the future event).
    assert pd.isna(sig["funding_ema"].iloc[0])


def test_warmup_first_event_seen_within_one_bar():
    events = _events([0.0002] * 30)
    bar_index = pd.date_range("2025-01-01", periods=200, freq="15min", tz="UTC")
    sig = compute_funding_ema_signal(events, bar_index, span_events=21, threshold=0.0001)
    # The first bar (00:00) coincides with the first event (00:00);
    # after ffill it sees 0.0002; after shift(1) the next bar sees
    # that value (no future peek).
    assert sig["funding_ema"].iloc[1] > 0


def test_threshold_zero_means_no_signal():
    events = _events([0.00005] * 30)  # below the default threshold
    bar_index = pd.date_range("2025-01-01", periods=200, freq="15min", tz="UTC")
    sig = compute_funding_ema_signal(events, bar_index, span_events=21, threshold=0.0001)
    # EMA ≈ 0.00005, below threshold.
    assert not sig["above_threshold"].iloc[-1]
    assert not sig["below_threshold"].iloc[-1]


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