"""Smoke tests for the backtest performance benchmark (B16).

Full-scale numbers live in ``bench_backtest.py``'s docstring / CLI output;
here we only pin the benchmark's plumbing on a small synthetic sample so
CI stays fast.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pandas as pd
import pytest

from _shared.bench_backtest import (
    BenchConfig,
    benchmark,
    format_report,
    synthetic_bars,
)


def test_synthetic_bars_deterministic() -> None:
    a = synthetic_bars(1000, seed=3)
    b = synthetic_bars(1000, seed=3)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) == 1000
    assert (a["close"] > 0).all()


def test_benchmark_small_scale() -> None:
    out = benchmark(BenchConfig(n_bars=20_000, n_trades=100, repeat=1))
    assert out["n_bars"] == 20_000
    assert out["main"]["bars_per_sec"] > 0
    assert out["vectorized"]["bars_per_sec"] > 0
    # Both engines must agree on the same random strategy (<1% spec gate;
    # in practice machine precision).
    assert out["consistency_max_rel_err"] < 1e-9
    assert "run_backtest.py" in out["profile_table"]


def test_format_report_contains_key_fields() -> None:
    out = benchmark(BenchConfig(n_bars=5_000, n_trades=20, repeat=1))
    text = format_report(out)
    assert "bars/s" in text
    assert "run_backtest" in text
    assert "vectorized" in text
    assert "100,000" in text
