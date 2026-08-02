"""Tests for _shared/data/quality_check.py (F15)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.data.quality_check import (
    QualityCheckResult,
    QualityPipeline,
    add_ingest_timestamp,
    check_completeness,
    check_continuity,
    check_monotonic_timestamp,
    check_outliers,
    check_volume_sanity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_frame(n: int = 100, freq: str = "1h") -> pd.DataFrame:
    """Build a clean OHLCV frame with no issues."""
    ts = pd.date_range("2026-01-01", periods=n, freq=freq)
    close = 100.0 + np.cumsum(np.random.randn(n) * 0.1)
    volume = np.random.uniform(100, 1000, n)
    return pd.DataFrame({
        "timestamp": ts,
        "open": close - 0.5,
        "high": close + 0.5,
        "low": close - 1.0,
        "close": close,
        "volume": volume,
    })


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def test_check_completeness_pass():
    """Clean frame → passes."""
    df = _clean_frame()
    name, passed, msg = check_completeness(df)
    assert passed
    assert name == "completeness"


def test_check_completeness_nan_fail():
    """NaN in required column → fails."""
    df = _clean_frame()
    df.loc[5, "close"] = np.nan
    _, passed, msg = check_completeness(df)
    assert not passed
    assert "NaN" in msg


def test_check_completeness_missing_column():
    """Missing column → fails."""
    df = _clean_frame().drop(columns=["volume"])
    _, passed, msg = check_completeness(df)
    assert not passed
    assert "missing" in msg.lower()


def test_check_continuity_pass():
    """Regular timestamps → passes."""
    df = _clean_frame()
    _, passed, _ = check_continuity(df)
    assert passed


def test_check_continuity_gap_fail():
    """Large gap in timestamps → fails."""
    df = _clean_frame()
    # Shift a chunk of timestamps to create a gap
    df.loc[50:, "timestamp"] = df.loc[50:, "timestamp"] + pd.Timedelta(hours=10)
    _, passed, msg = check_continuity(df)
    assert not passed
    assert "gap" in msg.lower()


def test_check_outliers_pass():
    """Normal returns → passes."""
    df = _clean_frame(n=200)
    _, passed, _ = check_outliers(df)
    assert passed


def test_check_outliers_fail():
    """Extreme outlier → fails."""
    df = _clean_frame(n=200)
    df.loc[100, "close"] = df.loc[100, "close"] * 3  # huge jump
    _, passed, msg = check_outliers(df)
    assert not passed
    assert "beyond" in msg.lower() or "σ" in msg


def test_check_monotonic_pass():
    """Increasing timestamps → passes."""
    df = _clean_frame()
    _, passed, _ = check_monotonic_timestamp(df)
    assert passed


def test_check_monotonic_fail():
    """Unsorted timestamps → fails."""
    df = _clean_frame()
    df = df.sample(frac=1, random_state=42).reset_index(drop=True)
    _, passed, msg = check_monotonic_timestamp(df)
    assert not passed


def test_check_volume_sanity_pass():
    """Non-negative volumes → passes."""
    df = _clean_frame()
    _, passed, _ = check_volume_sanity(df)
    assert passed


def test_check_volume_sanity_fail():
    """Negative volumes → fails."""
    df = _clean_frame()
    df.loc[10, "volume"] = -500
    _, passed, msg = check_volume_sanity(df)
    assert not passed
    assert "negative" in msg.lower()


# ---------------------------------------------------------------------------
# QualityPipeline
# ---------------------------------------------------------------------------

def test_pipeline_clean_frame_passes():
    """A clean frame should pass all checks."""
    pipeline = QualityPipeline()
    df = _clean_frame(n=100)
    result = pipeline.run(df, name="BTCUSDT_1h")
    assert result.passed
    assert result.score == pytest.approx(1.0)
    assert result.total_rows == 100
    assert result.name == "BTCUSDT_1h"


def test_pipeline_dirty_frame_fails():
    """A frame with issues should fail."""
    pipeline = QualityPipeline()
    df = _clean_frame()
    df.loc[5, "close"] = np.nan
    df.loc[10, "volume"] = -100
    result = pipeline.run(df, name="test")
    assert not result.passed
    assert result.score < 1.0
    assert len(result.checks) > 0


def test_pipeline_empty_frame():
    """Empty frame → fails."""
    pipeline = QualityPipeline()
    result = pipeline.run(pd.DataFrame(), name="empty")
    assert not result.passed
    assert result.total_rows == 0
    assert result.score == 0.0


def test_pipeline_custom_checks():
    """Custom checks should be used."""
    custom = [
        lambda df: ("custom1", True, "always pass"),
        lambda df: ("custom2", False, "always fail"),
    ]
    pipeline = QualityPipeline(checks=custom)
    result = pipeline.run(_clean_frame(), name="custom")
    assert not result.passed
    assert len(result.checks) == 2
    assert result.score == pytest.approx(0.5)


def test_pipeline_check_exception_handled():
    """A check that raises should be caught and recorded as failure."""
    def bad_check(df):
        raise ValueError("boom")

    pipeline = QualityPipeline(checks=[bad_check])
    result = pipeline.run(_clean_frame(), name="test")
    assert not result.passed
    assert "boom" in result.checks[0][2]


def test_quality_check_result_is_frozen():
    """QualityCheckResult should be immutable."""
    result = QualityCheckResult(
        name="test", total_rows=10, passed=True,
        checks=(("a", True, "ok"),), score=1.0,
    )
    with pytest.raises(Exception):
        result.passed = False


def test_pipeline_checks_count():
    """Default pipeline should have the standard 6 checks."""
    pipeline = QualityPipeline()
    df = _clean_frame(n=100)
    result = pipeline.run(df, name="test")
    assert len(result.checks) == 6


# ---------------------------------------------------------------------------
# add_ingest_timestamp
# ---------------------------------------------------------------------------

def test_add_ingest_timestamp_adds_column():
    """Should add an ingest_ts column."""
    df = _clean_frame(n=10)
    result = add_ingest_timestamp(df)
    assert "ingest_ts" in result.columns


def test_add_ingest_timestamp_preserves_original():
    """Original df should not be modified."""
    df = _clean_frame(n=10)
    original_cols = set(df.columns)
    _ = add_ingest_timestamp(df)
    assert set(df.columns) == original_cols


def test_add_ingest_timestamp_values_are_recent():
    """ingest_ts values should be close to now."""
    import time as _time
    before = _time.time()
    df = _clean_frame(n=10)
    result = add_ingest_timestamp(df)
    after = _time.time()
    for val in result["ingest_ts"]:
        assert before - 1 <= val <= after + 1
