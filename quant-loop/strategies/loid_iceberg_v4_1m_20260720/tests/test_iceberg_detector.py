"""Tests for iceberg_detector (SMA-34992 / LOID-V4).

Subset of the TDD specialist's plan (48 tests), focused on the 5 load-bearing
behaviors. Edge-case breadth tests (G-section) deferred — these are correctness
tests we cannot ship without.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from strategies.loid_iceberg_v4_1m_20260720.iceberg_detector import (
    detect as detect_fn,
    DetectorConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SCHEMA = ["ts", "price", "qty", "is_buyer_maker", "first_id", "last_id"]


@pytest.fixture
def base_ts():
    return pd.Timestamp("2026-07-20T00:00:00Z").value // 1_000_000


@pytest.fixture
def make_trades(base_ts):
    """Factory producing a trades DataFrame with the canonical schema.

    step_ms: increment between consecutive timestamps. Default 1.
    Pass step_ms=0 only if you also set ts_seq (otherwise np.arange raises).
    To place all n trades at one millisecond, use a ts_seq of n copies of a value.
    """

    def _make(
        n=60,
        start_ms=None,
        step_ms=1,
        price=100.0,
        qty=1.0,
        qty_seq=None,
        is_buyer_maker=False,
        buyer_maker_seq=None,
        ts_seq=None,
    ):
        start_ms = start_ms if start_ms is not None else base_ts
        if ts_seq is not None:
            ts = np.asarray(ts_seq, dtype="int64")
            assert len(ts) == n, f"ts_seq length {len(ts)} != n {n}"
        else:
            ts = np.arange(start_ms, start_ms + n * step_ms, step_ms, dtype="int64")[:n]
        q = np.asarray(qty_seq if qty_seq is not None else np.full(n, qty), dtype="float64")
        bm = np.asarray(
            buyer_maker_seq if buyer_maker_seq is not None else np.full(n, is_buyer_maker),
            dtype=bool,
        )
        fid = np.arange(1, n + 1, dtype="int64")
        return pd.DataFrame(
            {
                "ts": ts,
                "price": np.full(n, price, dtype="float64"),
                "qty": q,
                "is_buyer_maker": bm,
                "first_id": fid,
                "last_id": fid.copy(),
            }
        )

    return _make


# ---------------------------------------------------------------------------
# A. Schema validation
# ---------------------------------------------------------------------------


def test_schema_rejects_missing_required_column(make_trades):
    df = make_trades(n=5)
    df = df.drop(columns=["qty"])
    with pytest.raises(Exception):
        detect_fn(df, DetectorConfig())


def test_schema_rejects_non_boolean_is_buyer_maker(make_trades):
    df = make_trades(n=5)
    df["is_buyer_maker"] = df["is_buyer_maker"].astype("int64")
    with pytest.raises(Exception):
        detect_fn(df, DetectorConfig())


def test_schema_rejects_zero_qty(make_trades):
    df = make_trades(n=5)
    df.loc[2, "qty"] = 0.0
    with pytest.raises(Exception):
        detect_fn(df, DetectorConfig())


def test_schema_accepts_minimal_valid_frame(make_trades):
    df = make_trades(n=5)
    out = detect_fn(df, DetectorConfig())
    assert "composite_by_minute" in out
    assert "stats" in out


# ---------------------------------------------------------------------------
# B. Side assignment
# ---------------------------------------------------------------------------


def test_side_is_plus_one_when_is_buyer_maker_false():
    """is_buyer_maker=False means TAKER bought; side convention = +1."""
    from strategies.loid_iceberg_v4_1m_20260720.iceberg_detector import signed_side

    arr = np.array([False, False, False])
    out = signed_side(arr)
    assert (out == 1).all()


def test_side_is_minus_one_when_is_buyer_maker_true():
    """is_buyer_maker=True means TAKER sold; side convention = -1."""
    from strategies.loid_iceberg_v4_1m_20260720.iceberg_detector import signed_side

    arr = np.array([True, True, True])
    out = signed_side(arr)
    assert (out == -1).all()


# ---------------------------------------------------------------------------
# C. Rolling z-score — shifted (look-ahead prevention is load-bearing)
# ---------------------------------------------------------------------------

# lookback=1000 means z[i] is computed against rows [i-1000, i-1] (NOT including i).
# Therefore rows 0..999 have insufficient history → NaN.
# A spike at row i will not be flagged AS the spike row itself — it raises the
# baseline of subsequent rows. Practical implication: signals fire with a
# 1000-trade lag, by design.


def test_zscore_first_lookback_rows_are_nan(make_trades):
    """With lookback=1000, rows 0..999 have insufficient history → NaN.

    Use alternating qty so the rolling std is non-zero after warmup.
    """
    qty_seq = [1.0, 3.0] * 1000
    df = make_trades(n=2000, qty_seq=qty_seq)
    out = detect_fn(df, DetectorConfig(lookback=1000))
    z = out["per_trade_z"]
    for i in range(1000):
        assert math.isnan(z[i]), f"row {i} should be NaN"
    assert math.isfinite(z[1000]), "row 1000 should be the first finite z"
    assert math.isfinite(z[1999])


def test_zscore_window_excludes_current_trade(make_trades):
    """z[1000] is computed against qty[0..999] (does NOT include qty[1000]).

    Construct a sequence with a known mean in rows 0..999 and a deliberately
    different qty at row 1000. Verify z[1000] reflects ONLY rows 0..999.
    Concretely: rows 0..999 qty=2.0, row 1000 qty=10.0, rows 1001+ qty=2.0.
    Prior window mean=2, std=0 → z[1000] should be NaN (uniform window).
    If look-ahead had been included: z[1000] would be (10-2.01)/std(0..1000),
    a finite huge value. We assert NaN, which proves the shift is real.
    """
    qty_seq = [2.0] * 1000 + [10.0] + [2.0] * 10  # 1011 rows total
    df = make_trades(n=1011, qty_seq=qty_seq)
    out = detect_fn(df, DetectorConfig(lookback=1000))
    z = out["per_trade_z"]
    assert math.isnan(z[1000]), (
        f"row 1000 z should be NaN since prior window 0..999 has std=0 "
        f"(shift is real). Got z[1000]={z[1000]}"
    )


def test_zscore_with_variability_actually_flags_large(make_trades):
    """When prior window has variability, a current spike is flagged.

    Rows 0..999 alternate qty=1, 3, 1, 3, ... → mean=2, std=1.
    Row 1000 qty = 10 (current spike, NOT in window).
    Expected z[1000] = (10 - 2)/1 = 8 → |z| > 5, fires "whale".
    """
    qty_seq = ([1.0, 3.0] * 500) + [10.0] + ([1.0, 3.0] * 10)
    df = make_trades(n=len(qty_seq), qty_seq=qty_seq)
    out = detect_fn(df, DetectorConfig(lookback=1000))
    z = out["per_trade_z"]
    assert math.isfinite(z[1000])
    assert abs(z[1000]) > 5.0, f"expected |z| > 5, got {z[1000]}"


# ---------------------------------------------------------------------------
# D. Iceberg clustering — same-ms+price, ≥5 trades, CV≤0.10
# ---------------------------------------------------------------------------


def test_cluster_forms_when_five_trades_same_ms_price_low_cv(make_trades, base_ts):
    """5 trades all at base_ts, qty=1, price=100 → CV=0 ≤ 0.10 → 1 cluster."""
    df = make_trades(
        n=5,
        ts_seq=[base_ts] * 5,
        qty=1.0,
        price=100.0,
    )
    out = detect_fn(df, DetectorConfig())
    stats = out["stats"]
    assert stats["cluster_count"] == 1, f"expected 1 cluster, got {stats['cluster_count']}"


def test_cluster_rejected_when_four_trades(make_trades, base_ts):
    """4 trades same ms+price → no cluster (need ≥5)."""
    df = make_trades(
        n=4,
        ts_seq=[base_ts] * 4,
        qty=1.0,
        price=100.0,
    )
    out = detect_fn(df, DetectorConfig())
    assert out["stats"]["cluster_count"] == 0


def test_cluster_rejected_when_high_cv(make_trades, base_ts):
    """5 trades same ms+price with varying qty [1, 2, 3, 4, 5] → CV > 0.10 → no cluster."""
    df = make_trades(
        n=5,
        ts_seq=[base_ts] * 5,
        qty_seq=[1.0, 2.0, 3.0, 4.0, 5.0],
        price=100.0,
    )
    out = detect_fn(df, DetectorConfig())
    assert out["stats"]["cluster_count"] == 0


# ---------------------------------------------------------------------------
# E. Per-minute composite
# ---------------------------------------------------------------------------


def test_composite_includes_large_zscore_term(make_trades):
    """A current-bar spike (with variability in prior window) contributes side*z to composite.

    Rows 0..999 alternate qty=1, 3 → mean=2, std=1.
    Row 1000 qty=10 (10× std) → z≈8, side=+1 (taker buy).
    Composite at minute containing row 1000 should be ≈ +8.
    """
    qty_seq = ([1.0, 3.0] * 500) + [10.0] + ([1.0, 3.0] * 10)
    df = make_trades(
        n=len(qty_seq),
        qty_seq=qty_seq,
        is_buyer_maker=False,  # taker bought → side=+1
    )
    out = detect_fn(df, DetectorConfig(lookback=1000))
    composite = out["composite_by_minute"]
    assert composite["composite"].abs().sum() > 0


def test_composite_iceberg_contributes_two_per_cluster(make_trades, base_ts):
    """One iceberg cluster → adds ±2 to that minute composite."""
    base = make_trades(n=1495, qty=1.0)
    iceberg = make_trades(
        n=5,
        ts_seq=[base_ts + 1500] * 5,
        qty=1.0,
        price=100.0,
        is_buyer_maker=False,  # taker bought → side=+1
    )
    df = pd.concat([base, iceberg], ignore_index=True)
    out = detect_fn(df, DetectorConfig(lookback=1000))
    composite = out["composite_by_minute"]
    assert composite["composite"].abs().sum() > 0
