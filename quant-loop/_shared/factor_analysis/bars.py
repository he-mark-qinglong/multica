"""Alternative bar generators — information-driven sampling.

Time-bars (fixed-clock OHLCV) sample uniformly in *time* but
non-uniformly in *information*: low-activity periods get as many bars as
high-activity ones.  López de Prado (2018, Ch. 2) shows that alternative
bars (volume, dollar, tick, imbalance) are closer to i.i.d. after the
sampling, improving statistical properties of signals computed on them.

All generators take a fine-grained OHLCV DataFrame and return a
resampled OHLCV DataFrame with the same columns (``open, high, low,
close, volume``) plus a ``close_time`` index.  Each output row is the
aggregation of a contiguous run of input rows.

Bar types
---------
- :func:`volume_bars`   — sample when cumulative volume crosses a threshold.
- :func:`dollar_bars`   — sample when cumulative dollar volume crosses a threshold.
- :func:`tick_bars`     — sample every ``N`` input rows.
- :func:`imbalance_bars` — sample when cumulative signed-volume imbalance
  exceeds an adaptive threshold (López de Prado *dollar imbalance bars*).

References
----------
- López de Prado (2018) *Advances in Financial Machine Learning*, Ch. 2
- Easley, López de Prado & O'Hara (2012) "Bulk Volume Classification"
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

__all__ = [
    "volume_bars",
    "dollar_bars",
    "tick_bars",
    "imbalance_bars",
    "resample_to_bars",
]


# ---------------------------------------------------------------------------
# Aggregation helper
# ---------------------------------------------------------------------------

def _aggregate_ohlcv(chunk: pd.DataFrame) -> pd.Series:
    """Aggregate a chunk of fine-grained bars into one OHLCV bar."""
    return pd.Series({
        "open": chunk["open"].iloc[0],
        "high": chunk["high"].max(),
        "low": chunk["low"].min(),
        "close": chunk["close"].iloc[-1],
        "volume": chunk["volume"].sum(),
    })


def resample_to_bars(data: pd.DataFrame, boundaries: Iterable[int]) -> pd.DataFrame:
    """Generic OHLCV resampler given a list of *end* indices.

    Parameters
    ----------
    data : pd.DataFrame
        Fine-grained OHLCV with columns ``open, high, low, close, volume``.
    boundaries : iterable of int
        End indices (exclusive upper bound) of each output bar.

    Returns
    -------
    pd.DataFrame — aggregated OHLCV bars.
    """
    out_rows = []
    start = 0
    for end in boundaries:
        if end <= start:
            continue
        chunk = data.iloc[start:end]
        if len(chunk) == 0:
            continue
        out_rows.append(_aggregate_ohlcv(chunk))
        start = end
    if not out_rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.DataFrame(out_rows, index=data.index[[b - 1 for b in _valid_boundaries(boundaries, len(data))]])


def _valid_boundaries(boundaries: Iterable[int], n: int) -> list[int]:
    """Filter boundary list to valid (1 <= b <= n) values."""
    return [b for b in boundaries if 1 <= b <= n]


# ---------------------------------------------------------------------------
# Threshold-based bar generators
# ---------------------------------------------------------------------------

def _threshold_bars(
    data: pd.DataFrame,
    cumulative_metric: np.ndarray,
    threshold: float,
) -> pd.DataFrame:
    """Core loop for volume / dollar bars."""
    n = len(data)
    boundaries: list[int] = []
    cum = 0.0
    start = 0
    for i in range(n):
        cum += cumulative_metric[i]
        if cum >= threshold:
            boundaries.append(i + 1)
            cum = 0.0
            start = i + 1
    # flush remainder
    if start < n:
        boundaries.append(n)
    return resample_to_bars(data, boundaries)


def volume_bars(data: pd.DataFrame, volume_threshold: float) -> pd.DataFrame:
    """Sample a new bar each time cumulative volume ≥ ``volume_threshold``.

    Parameters
    ----------
    data : pd.DataFrame
        Fine-grained OHLCV bars.
    volume_threshold : float
        Cumulative volume that triggers bar formation.

    Returns
    -------
    pd.DataFrame — resampled OHLCV.
    """
    if volume_threshold <= 0:
        raise ValueError(f"volume_threshold must be > 0, got {volume_threshold}")
    vol = data["volume"].astype(float).to_numpy()
    return _threshold_bars(data, vol, volume_threshold)


def dollar_bars(data: pd.DataFrame, dollar_threshold: float) -> pd.DataFrame:
    """Sample a new bar each time cumulative dollar volume ≥ ``dollar_threshold``.

    Dollar volume = ``close × volume`` per input bar.
    """
    if dollar_threshold <= 0:
        raise ValueError(f"dollar_threshold must be > 0, got {dollar_threshold}")
    dollar_vol = (data["close"].astype(float) * data["volume"].astype(float)).to_numpy()
    return _threshold_bars(data, dollar_vol, dollar_threshold)


def tick_bars(data: pd.DataFrame, tick_size: int) -> pd.DataFrame:
    """Sample one bar every ``tick_size`` input rows (fixed-count sampling).

    Parameters
    ----------
    data : pd.DataFrame
        Fine-grained OHLCV bars (each row = one "tick" in this context).
    tick_size : int
        Number of input rows per output bar.
    """
    if tick_size < 1:
        raise ValueError(f"tick_size must be >= 1, got {tick_size}")
    n = len(data)
    boundaries = list(range(tick_size, n + 1, tick_size))
    if boundaries[-1] < n:
        boundaries.append(n)
    return resample_to_bars(data, boundaries)


# ---------------------------------------------------------------------------
# Imbalance bars (López de Prado dollar-imbalance variant)
# ---------------------------------------------------------------------------

def imbalance_bars(
    data: pd.DataFrame,
    expected_imbalance: float | None = None,
    expected_ticks: int = 10,
    min_bars: int = 10,
    use_ema: bool = True,
    ema_alpha: float = 0.01,
) -> pd.DataFrame:
    """Dollar imbalance bars (Easley, López de Prado & O'Hara 2012).

    Signed dollar volume ``b_t = sign(Δprice) × close × volume`` is
    accumulated; a new bar forms when ``|Σ b_t|`` exceeds the threshold
    ``E[|b|] × expected_ticks``.

    The threshold is a **constant** (not growing with tick count),
    following the standard formulation where ``expected_ticks`` is the
    anticipated number of ticks per bar.

    Parameters
    ----------
    data : pd.DataFrame
        Fine-grained OHLCV.
    expected_imbalance : float or None
        Initial ``E[|b|]``; if ``None`` estimated from data.
    expected_ticks : int
        Expected ticks per bar (controls bar frequency).
    min_bars : int
        Warmup bars used to estimate ``E[|b|]`` when not given.
    use_ema : bool
        Whether to adapt ``E[|b|]`` online via EMA.
    ema_alpha : float
        EMA smoothing factor (0, 1).

    Returns
    -------
    pd.DataFrame — resampled OHLCV bars.
    """
    close = data["close"].astype(float)
    volume = data["volume"].astype(float)
    price_change = close.diff().fillna(0.0)
    sign = np.sign(price_change)
    dollar_vol = close * volume
    signed_flow = (sign * dollar_vol).to_numpy()
    abs_flow = np.abs(signed_flow)

    if expected_imbalance is None:
        warmup = min(len(abs_flow), max(min_bars, 1))
        expected_imbalance = float(np.mean(abs_flow[:warmup])) if warmup > 0 else 1.0
        if expected_imbalance == 0:
            expected_imbalance = 1.0

    ema_e = expected_imbalance
    threshold = ema_e * expected_ticks
    n = len(data)
    boundaries: list[int] = []
    cum = 0.0

    for i in range(n):
        cum += signed_flow[i]
        if use_ema and abs_flow[i] > 0:
            ema_e = ema_alpha * abs_flow[i] + (1 - ema_alpha) * ema_e
            threshold = ema_e * expected_ticks
        if threshold > 0 and abs(cum) >= threshold:
            boundaries.append(i + 1)
            cum = 0.0

    return resample_to_bars(data, boundaries)
