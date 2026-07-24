"""VPVR VAH/VAL band computation for vpvr_funding_carry_asym_v2 (SMA-34990).

Computes a per-snapshot VAH/VAL band on a given timeframe via the
shared ``vpvr_levels.compute_vpvr_levels`` (SMA-34790). Returns a
per-bar DataFrame with ``vah``, ``val``, ``midpoint``, and a ``half``
column (``"lower"`` / ``"upper"``) describing which half of the band
the bar's close sits in.

Public API
----------
``build_vpvr_band(df, *, window_bars, snapshot_every_bars, num_bins, value_area_fraction)``
    Returns a DataFrame indexed like ``df`` with columns:
        ``vah``, ``val``, ``midpoint``, ``half``.

No-look-ahead
-------------
- Each snapshot is computed on bars ``[t - window + 1, t]`` inclusive
  of bar ``t``.
- The snapshot series is then ``shift(1)``-ed and ffill-ed onto the
  bar index so the level used at bar ``t`` reflects data strictly
  before ``t``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

QUANT_LOOP = Path("/home/smark/multica/quant-loop")
_INDICATORS_DIR = QUANT_LOOP / "strategies" / "_indicators"
if str(_INDICATORS_DIR) not in sys.path:
    sys.path.insert(0, str(_INDICATORS_DIR))

from vpvr_levels import compute_vpvr_levels  # noqa: E402


def _vpvr_snapshot_band(
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    snapshot_idx: pd.DatetimeIndex,
    *,
    window: int,
    num_bins: int,
    value_area_fraction: float,
) -> pd.DataFrame:
    """Per-snapshot VAH/VAL on each rolling window."""
    pos = {ts: i for i, ts in enumerate(high.index)}
    out = {
        "vah": np.full(len(snapshot_idx), np.nan),
        "val": np.full(len(snapshot_idx), np.nan),
    }
    for k, ts in enumerate(snapshot_idx):
        end = pos[ts]
        start = max(0, end - window + 1)
        if end - start + 1 < max(20, window // 4):
            continue
        try:
            profile = compute_vpvr_levels(
                high.iloc[start: end + 1],
                low.iloc[start: end + 1],
                volume.iloc[start: end + 1],
                num_bins=num_bins,
                value_area_fraction=value_area_fraction,
                hvn_quantile=0.85,
                lvn_quantile=0.15,
                num_hvn=3,
                num_lvn=3,
            )
        except (ValueError, ZeroDivisionError):
            continue
        out["vah"][k] = float(profile.vah_price)
        out["val"][k] = float(profile.val_price)
    return pd.DataFrame(out, index=snapshot_idx)


def build_vpvr_band(
    df: pd.DataFrame,
    *,
    window_bars: int = 180,
    snapshot_every_bars: int = 16,
    num_bins: int = 24,
    value_area_fraction: float = 0.70,
) -> pd.DataFrame:
    """Per-bar VAH/VAL band and ``half`` classification.

    Args:
        df: OHLCV frame with DatetimeIndex. Must include ``high``,
            ``low``, ``volume`` (volume required for the profile).
        window_bars: rolling window length in bars.
        snapshot_every_bars: cadence for snapshotting the profile
            (1 = every bar; 16 = every 16 bars ≈ 4h at 15m).
        num_bins: price bins for the profile.
        value_area_fraction: fraction of volume captured by VAH..VAL.

    Returns:
        pd.DataFrame indexed identically to ``df`` with columns:
          - ``vah``      (float, NaN until warm-up)
          - ``val``      (float, NaN until warm-up)
          - ``midpoint`` (float, midpoint of vah/val)
          - ``half``     (object, "lower" / "upper" / "")
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df must have a DatetimeIndex")
    df = df.sort_index()
    high = df["high"].astype(np.float64)
    low = df["low"].astype(np.float64)
    volume = df["volume"].astype(np.float64)
    close = df["close"].astype(np.float64)

    stride = max(1, int(snapshot_every_bars))
    snapshot_idx = df.index[::stride]
    if len(df.index) and df.index[-1] not in snapshot_idx:
        snapshot_idx = snapshot_idx.append(pd.DatetimeIndex([df.index[-1]]))

    snap = _vpvr_snapshot_band(
        high, low, volume, snapshot_idx,
        window=int(window_bars),
        num_bins=int(num_bins),
        value_area_fraction=float(value_area_fraction),
    )
    snap_shifted = snap.shift(1).reindex(df.index).ffill()

    vah = snap_shifted["vah"]
    val = snap_shifted["val"]
    midpoint = 0.5 * (vah + val)
    half = pd.Series("", index=df.index, dtype=object)
    half[close <= midpoint] = "lower"
    half[close >= midpoint] = "upper"

    return pd.DataFrame(
        {
            "vah": vah,
            "val": val,
            "midpoint": midpoint,
            "half": half,
        },
        index=df.index,
    )


__all__ = ["build_vpvr_band"]