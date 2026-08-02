"""Tests for _shared/ops_profile.py (J18)."""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/Users/mark/multica/quant-loop")

from _shared.ops_profile import (
    _synthetic_aggtrades,
    profile_callable,
    profile_section,
    report,
    reset,
)


def test_profile_callable_returns_result_and_table():
    def work(n):
        return sum(range(n))

    result, table = profile_callable(work, 10_000)
    assert result == sum(range(10_000))
    assert "work" in table
    assert "cumtime" in table  # function-level table header


def test_profile_section_accumulates():
    reset()

    @profile_section("busy")
    def busy(ms):
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) * 1000 < ms:
            pass
        return ms

    assert busy(1) == 1
    busy(1)

    text = report()
    assert "section: busy" in text
    assert "calls=2" in text
    assert "busy" in text
    reset()
    assert report() == "no profiled sections"


def test_synthetic_aggtrades_schema():
    df = _synthetic_aggtrades(n=100)
    assert set(df.columns) == {"ts", "price", "qty", "is_buyer_maker"}
    assert len(df) == 100
    assert df["ts"].is_monotonic_increasing
    assert (df["price"] > 0).all()
