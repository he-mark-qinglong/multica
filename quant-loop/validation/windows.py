"""Walk-forward out-of-sample window computation.

The harness evaluates every variant on N contiguous, non-overlapping OOS
folds covering the available data span. Variant parameters are already fixed
in config.json (calibration happened upstream in the quant loop), so the
folds are pure evaluation segments — no re-fitting inside the harness.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class OOSWindow:
    index: int  # 1-based
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def label(self) -> str:
        return f"W{self.index}[{self.start.date()}..{self.end.date()}]"


def compute_oos_windows(
    span_start: pd.Timestamp,
    span_end: pd.Timestamp,
    n_windows: int = 3,
) -> list[OOSWindow]:
    """Split [span_start, span_end] into n_windows contiguous equal folds."""
    if n_windows < 1:
        raise ValueError("n_windows must be >= 1")
    span_start = pd.Timestamp(span_start)
    span_end = pd.Timestamp(span_end)
    if span_end <= span_start:
        raise ValueError(f"empty span: {span_start}..{span_end}")
    edges = [
        span_start + (span_end - span_start) * i / n_windows
        for i in range(n_windows + 1)
    ]
    windows = []
    for i in range(n_windows):
        start = edges[i].floor("1min")
        # window ends are exclusive of the next window's first bar
        end = edges[i + 1].floor("1min") - pd.Timedelta(minutes=1)
        windows.append(OOSWindow(index=i + 1, start=start, end=end))
    return windows
