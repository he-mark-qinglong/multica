"""Data quality pipeline: multi-check orchestration + dual-timestamp (F15).

Wraps the existing :mod:`_shared.data.quality` checks (gap detection, price
anomalies) into a composable pipeline that also adds:

- **Completeness** — no NaN in required columns.
- **Outlier detection** — z-score on returns.
- **Monotonic timestamp** — timestamps must be strictly increasing.
- **Volume sanity** — non-negative, non-pathological volumes.

The pipeline produces a :class:`QualityCheckResult` with a ``score`` field
(fraction of checks passed) for quick pass/fail gating.

Additionally, :func:`add_ingest_timestamp` implements the dual-timestamp
pattern: it adds a wall-clock ``ingest_ts`` column alongside the exchange
``timestamp``, so downstream consumers can distinguish *when data arrived*
from *when the exchange generated it* — critical for detecting feed lag.

References:
  - :mod:`_shared.data.quality` — the underlying single-frame detectors.
  - Google SRE Book, Ch. 11 "Being On-Call" — data-quality SLOs.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Tuple

import numpy as np
import pandas as pd

from _shared.data.quality import find_gaps, find_price_anomalies


@dataclass(frozen=True)
class QualityCheckResult:
    """Outcome of running the quality pipeline on one frame.

    Attributes
    ----------
    name:
        Frame identifier (e.g. symbol or file name).
    total_rows:
        Row count of the input frame.
    passed:
        ``True`` if all checks passed.
    checks:
        Tuple of ``(check_name, passed, message)`` tuples.
    score:
        Fraction of checks that passed (0.0 – 1.0).
    """

    name: str
    total_rows: int
    passed: bool
    checks: Tuple[Tuple[str, bool, str], ...]
    score: float


# ---------------------------------------------------------------------------
# Standard checks (pure functions returning (name, passed, message))
# ---------------------------------------------------------------------------

def check_completeness(
    df: pd.DataFrame, required_cols: Sequence[str] = ("timestamp", "close", "volume"),
) -> Tuple[str, bool, str]:
    """Check that required columns have no NaN values."""
    name = "completeness"
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        return (name, False, f"missing columns: {missing_cols}")

    nan_counts = {c: int(df[c].isna().sum()) for c in required_cols}
    total_nan = sum(nan_counts.values())
    if total_nan == 0:
        return (name, True, "no NaN in required columns")
    return (name, False, f"{total_nan} NaN values: {nan_counts}")


def check_continuity(
    df: pd.DataFrame, ts_col: str = "timestamp", gap_mult: float = 2.0,
) -> Tuple[str, bool, str]:
    """Check for time-series gaps using find_gaps."""
    name = "continuity"
    if ts_col not in df.columns:
        return (name, False, f"column {ts_col!r} not found")
    work = df.sort_values(ts_col).reset_index(drop=True)
    ts = work[ts_col]
    if pd.api.types.is_datetime64_any_dtype(ts):
        ts_ms = (ts.astype("int64") // 1e6).astype(np.int64)
    else:
        ts_ms = ts.astype(np.int64)

    gaps = find_gaps(ts_ms.to_numpy(), median_mult=gap_mult)
    if not gaps:
        return (name, True, "no gaps detected")
    return (name, False, f"{len(gaps)} gaps detected (largest: {max(g.size_ms for g in gaps)} ms)")


def check_outliers(
    df: pd.DataFrame, price_col: str = "close", z_threshold: float = 5.0,
) -> Tuple[str, bool, str]:
    """Check for z-score outliers in returns."""
    name = "outliers"
    if price_col not in df.columns:
        return (name, False, f"column {price_col!r} not found")
    rets = df[price_col].pct_change().dropna()
    if len(rets) < 10:
        return (name, True, "insufficient data for outlier check")
    z = (rets - rets.mean()) / (rets.std() + 1e-12)
    n_outliers = int((z.abs() > z_threshold).sum())
    if n_outliers == 0:
        return (name, True, f"no returns beyond {z_threshold}σ")
    return (name, False, f"{n_outliers} returns beyond {z_threshold}σ")


def check_monotonic_timestamp(
    df: pd.DataFrame, ts_col: str = "timestamp",
) -> Tuple[str, bool, str]:
    """Check that timestamps are strictly increasing."""
    name = "monotonic_timestamp"
    if ts_col not in df.columns:
        return (name, False, f"column {ts_col!r} not found")
    ts = df[ts_col]
    is_mono = bool(ts.is_monotonic_increasing)
    n_dup = int(ts.duplicated().sum())
    if is_mono and n_dup == 0:
        return (name, True, "timestamps strictly increasing")
    return (name, False, f"not monotonic ({n_dup} duplicates)")


def check_volume_sanity(
    df: pd.DataFrame, volume_col: str = "volume",
) -> Tuple[str, bool, str]:
    """Check that volumes are non-negative."""
    name = "volume_sanity"
    if volume_col not in df.columns:
        return (name, False, f"column {volume_col!r} not found")
    neg_count = int((df[volume_col] < 0).sum())
    if neg_count == 0:
        return (name, True, "all volumes non-negative")
    return (name, False, f"{neg_count} negative volume values")


def check_price_anomalies_rolling(
    df: pd.DataFrame, price_col: str = "close", ts_col: str = "timestamp",
    ret_mult: float = 5.0,
) -> Tuple[str, bool, str]:
    """Check for price anomalies using rolling-std outlier detection."""
    name = "price_anomalies"
    if price_col not in df.columns or ts_col not in df.columns:
        return (name, False, f"required columns missing")
    work = df.sort_values(ts_col).reset_index(drop=True)
    ts = work[ts_col]
    if pd.api.types.is_datetime64_any_dtype(ts):
        ts_ms = (ts.astype("int64") // 1e6).astype(np.int64)
    else:
        ts_ms = ts.astype(np.int64)
    anomalies = find_price_anomalies(
        work[price_col].astype(float), ts_ms, ret_mult=ret_mult,
    )
    if not anomalies:
        return (name, True, "no price anomalies")
    return (name, False, f"{len(anomalies)} price anomalies detected")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _default_checks() -> Sequence[Callable[[pd.DataFrame], Tuple[str, bool, str]]]:
    """Return the standard check sequence."""
    return (
        check_completeness,
        check_continuity,
        check_outliers,
        check_monotonic_timestamp,
        check_volume_sanity,
        check_price_anomalies_rolling,
    )


class QualityPipeline:
    """Runs a configurable sequence of quality checks on a DataFrame.

    Parameters
    ----------
    checks:
        Sequence of callables, each taking a DataFrame and returning
        ``(name, passed, message)``. Defaults to the standard 6 checks.

    Usage::

        pipeline = QualityPipeline()
        result = pipeline.run(df, name="BTCUSDT_1h")
        if not result.passed:
            print(f"Quality check failed: {result.score:.0%}")
    """

    def __init__(
        self,
        checks: Sequence[Callable[[pd.DataFrame], Tuple[str, bool, str]]] | None = None,
    ) -> None:
        self.checks: Tuple[Callable, ...] = tuple(checks) if checks else _default_checks()

    def run(self, df: pd.DataFrame, name: str = "") -> QualityCheckResult:
        """Run all checks and return a :class:`QualityCheckResult`.

        Parameters
        ----------
        df:
            Input DataFrame with at least a timestamp column.
        name:
            Identifier for this frame (symbol, file name, etc.).

        Returns
        -------
        QualityCheckResult
        """
        if len(df) == 0:
            return QualityCheckResult(
                name=name, total_rows=0, passed=False,
                checks=(("pipeline", False, "empty frame"),),
                score=0.0,
            )

        results = []
        for check_fn in self.checks:
            try:
                result = check_fn(df)
            except Exception as e:
                result = (check_fn.__name__, False, f"check raised: {e}")
            results.append(result)

        passed_count = sum(1 for _, p, _ in results if p)
        total = len(results)
        score = float(passed_count / total) if total > 0 else 0.0
        passed = all(p for _, p, _ in results)

        return QualityCheckResult(
            name=name,
            total_rows=len(df),
            passed=passed,
            checks=tuple(results),
            score=score,
        )


# ---------------------------------------------------------------------------
# Dual-timestamp
# ---------------------------------------------------------------------------

def add_ingest_timestamp(df: pd.DataFrame, ts_col: str = "timestamp") -> pd.DataFrame:
    """Add a wall-clock ``ingest_ts`` column alongside the exchange timestamp.

    Implements the dual-timestamp pattern: ``timestamp`` = when the exchange
    generated the bar/tick; ``ingest_ts`` = when our system received it. The
    difference reveals feed lag.

    Parameters
    ----------
    df:
        Input DataFrame.
    ts_col:
        Name of the existing exchange timestamp column (for validation only;
        the ingest timestamp is always wall-clock ``now``).

    Returns
    -------
    pd.DataFrame
        Copy of *df* with an added ``ingest_ts`` column (POSIX timestamp).
    """
    result = df.copy()
    result["ingest_ts"] = time.time()
    return result


__all__ = [
    "QualityCheckResult",
    "QualityPipeline",
    "check_completeness",
    "check_continuity",
    "check_outliers",
    "check_monotonic_timestamp",
    "check_volume_sanity",
    "check_price_anomalies_rolling",
    "add_ingest_timestamp",
]
