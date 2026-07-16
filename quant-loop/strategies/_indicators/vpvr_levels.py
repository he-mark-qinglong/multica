"""Volume Profile Visible Range (VPVR) level detector.

Pure functions for computing the volume profile of OHLCV bars and
extracting the structural levels a strategy needs:

  - **POC**  — Point of Control (price with the highest volume).
  - **VAH / VAL** — Value Area High / Low (price band holding ~70%
    of total volume, expanded outward from the POC).
  - **HVN / LVN** — High / Low Volume Nodes (local maxima / minima
    in the volume profile).

Convention follows ``iter94_20260714.py``: pure numpy/pandas in,
``pd.Series`` / ``dict`` out, no I/O, no globals. The price-bin
width is auto-scaled to the price range so a $60k BTC and a
$0.05 alt produce comparable resolution.

Bar-volume distribution: each bar's volume is split evenly across
the price bins it spans (``[low, high]``). This is the textbook
"equal-distribution" approximation used when only OHLCV is
available — when tick data is present, callers should swap in
``build_volume_profile_from_trades``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults — single source of truth so callers don't disagree.
# ---------------------------------------------------------------------------
DEFAULT_VALUE_AREA_FRACTION: float = 0.70
DEFAULT_NUM_HVN: int = 5
DEFAULT_NUM_LVN: int = 5
DEFAULT_HVN_QUANTILE: float = 0.85   # bins at >= this quantile count as HVN
DEFAULT_LVN_QUANTILE: float = 0.15   # bins at <= this quantile count as LVN
DEFAULT_PRICE_BINS: int = 200        # target number of price bins


@dataclass(frozen=True)
class VolumeProfile:
    """A volume profile result.

    Attributes
    ----------
    price_centers
        ``np.ndarray`` of shape ``(B,)`` — center price of each bin.
    volume
        ``np.ndarray`` of shape ``(B,)`` — total base-asset volume
        distributed into each bin.
    bin_width
        Width of each price bin in quote-currency units (USD).
    poc_price
        Center price of the highest-volume bin.
    vah_price, val_price
        Upper / lower bounds of the value area (containing
        ``value_area_fraction`` of total volume, default 70%).
    hvn_zones
        List of ``(price_low, price_high, volume)`` tuples for the
        high-volume nodes identified.
    lvn_zones
        List of ``(price_low, price_high, volume)`` tuples for the
        low-volume nodes identified.
    total_volume
        Sum of ``volume`` (sanity check; matches sum of bar volumes
        modulo distribution rounding).
    value_area_fraction
        Fraction of total volume captured between VAL and VAH.
    """

    price_centers: np.ndarray
    volume: np.ndarray
    bin_width: float
    poc_price: float
    vah_price: float
    val_price: float
    hvn_zones: List[Tuple[float, float, float]]
    lvn_zones: List[Tuple[float, float, float]]
    total_volume: float
    value_area_fraction: float


# ---------------------------------------------------------------------------
# Volume profile construction.
# ---------------------------------------------------------------------------
def build_volume_profile(
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    num_bins: int = DEFAULT_PRICE_BINS,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Compute the volume profile by distributing each bar's volume
    uniformly across the price bins it spans.

    Parameters
    ----------
    high, low, volume
        Bar series (same length, same index). Use ``close`` for any
        of the OHLC columns you don't want to span.
    num_bins
        Target number of price bins. The actual number of bins may
        differ slightly if ``high.max() - low.min()`` doesn't divide
        evenly; ``np.linspace`` keeps the bin count fixed regardless.

    Returns
    -------
    price_centers
        ``np.ndarray`` of bin center prices.
    profile
        ``np.ndarray`` of cumulative volume per bin.
    bin_width
        Width of each price bin in quote-currency units.
    """
    h = high.to_numpy(dtype=float)
    lo = low.to_numpy(dtype=float)
    v = volume.to_numpy(dtype=float)

    if h.size == 0 or lo.size == 0 or v.size == 0:
        raise ValueError("empty input series")

    price_min = float(np.nanmin(lo))
    price_max = float(np.nanmax(h))
    if not np.isfinite(price_min) or not np.isfinite(price_max) or price_min >= price_max:
        raise ValueError(f"invalid price range: {price_min} .. {price_max}")

    edges = np.linspace(price_min, price_max, num_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    bin_width = float(edges[1] - edges[0])
    profile = np.zeros(num_bins, dtype=float)

    # Vectorize the per-bar distribution. We approximate each bar's
    # contribution by the fraction of its [low, high] range that
    # overlaps each bin, multiplied by bar volume.
    for hi_bar, lo_bar, vol_bar in zip(h, lo, v):
        if not np.isfinite(hi_bar) or not np.isfinite(lo_bar) or not np.isfinite(vol_bar):
            continue
        if vol_bar <= 0 or hi_bar <= lo_bar:
            continue
        # Clip bar range to overall profile range (in case extremes
        # drifted; edges already cover the union, so this is a no-op
        # in normal cases).
        bar_lo = max(lo_bar, price_min)
        bar_hi = min(hi_bar, price_max)
        # Bins overlapping [bar_lo, bar_hi]
        idx_lo = int(np.searchsorted(edges, bar_lo, side="right") - 1)
        idx_hi = int(np.searchsorted(edges, bar_hi, side="left"))
        idx_lo = max(0, idx_lo)
        idx_hi = min(num_bins, idx_hi)
        if idx_lo >= idx_hi:
            continue
        # Overlap length per bin: ``edges`` has one more element than
        # the number of bins, so the upper-edge slice needs the +1.
        edges_lo = edges[idx_lo:idx_hi]
        edges_hi = edges[idx_lo + 1:idx_hi + 1]
        overlap_lo = np.maximum(edges_lo, bar_lo)
        overlap_hi = np.minimum(edges_hi, bar_hi)
        overlap_len = np.maximum(overlap_hi - overlap_lo, 0.0)
        bar_range = bar_hi - bar_lo
        if bar_range <= 0:
            continue
        profile[idx_lo:idx_hi] += vol_bar * (overlap_len / bar_range)

    return centers, profile, bin_width


# ---------------------------------------------------------------------------
# POC + Value Area.
# ---------------------------------------------------------------------------
def find_poc(price_centers: np.ndarray, profile: np.ndarray) -> float:
    """Point of Control = bin center with the highest volume."""
    if profile.size == 0:
        raise ValueError("empty profile")
    return float(price_centers[int(np.argmax(profile))])


def find_value_area(
    price_centers: np.ndarray,
    profile: np.ndarray,
    bin_width: float,
    poc_index: int,
    value_area_fraction: float = DEFAULT_VALUE_AREA_FRACTION,
) -> Tuple[float, float]:
    """Value Area High / Low.

    Starting from the POC bin, expand outward to the next-highest
    adjacent bin until the cumulative volume covers
    ``value_area_fraction`` (default 70%). The resulting bin range
    is reported as ``(val_price, vah_price)``.
    """
    if not 0.0 < value_area_fraction <= 1.0:
        raise ValueError("value_area_fraction must be in (0, 1]")

    total_volume = float(profile.sum())
    if total_volume <= 0:
        raise ValueError("zero total volume")

    target = value_area_fraction * total_volume

    # Snapshot the profile as mutable ints (Python lists are easier
    # to extend outward from a center than numpy slices).
    weights = profile.tolist()
    n = len(weights)

    lo = poc_index
    hi = poc_index
    cumulative = weights[poc_index]

    while cumulative < target:
        # Compare the candidate volumes just outside the current
        # window and expand into whichever is larger. Tie → expand
        # both, but only when strictly needed.
        can_lo = weights[lo - 1] if lo > 0 else -1.0
        can_hi = weights[hi + 1] if hi < n - 1 else -1.0
        if can_lo < 0 and can_hi < 0:
            break  # at the boundary; stop early
        if can_hi >= can_lo and hi < n - 1:
            hi += 1
            cumulative += weights[hi]
        elif can_lo >= can_hi and lo > 0:
            lo -= 1
            cumulative += weights[lo]
        else:
            # one side hit the boundary; expand the other
            if hi < n - 1:
                hi += 1
                cumulative += weights[hi]
            elif lo > 0:
                lo -= 1
                cumulative += weights[lo]
            else:
                break

    # Edges of the value area bin range.
    val_price = float(price_centers[lo] - 0.5 * bin_width)
    vah_price = float(price_centers[hi] + 0.5 * bin_width)
    return val_price, vah_price


# ---------------------------------------------------------------------------
# HVN / LVN — peaks / troughs above / below quantile thresholds.
# ---------------------------------------------------------------------------
def _merge_adjacent(
    mask: np.ndarray,
    price_centers: np.ndarray,
    profile: np.ndarray,
    bin_width: float,
) -> List[Tuple[float, float, float]]:
    """Merge contiguous bins where ``mask`` is True into zones.

    Returns a list of ``(price_low, price_high, total_volume)``
    tuples, one per merged run.
    """
    zones: List[Tuple[float, float, float]] = []
    if mask.size == 0 or not mask.any():
        return zones
    runs = np.where(np.diff(np.concatenate([[0], mask.astype(int), [0]])) == 1)[0]
    ends = np.where(np.diff(np.concatenate([[0], mask.astype(int), [0]])) == -1)[0]
    for start, end in zip(runs, ends):
        price_low = float(price_centers[start] - 0.5 * bin_width)
        price_high = float(price_centers[end - 1] + 0.5 * bin_width)
        vol = float(profile[start:end].sum())
        zones.append((price_low, price_high, vol))
    return zones


def find_hvn_lvn(
    price_centers: np.ndarray,
    profile: np.ndarray,
    bin_width: float,
    hvn_quantile: float = DEFAULT_HVN_QUANTILE,
    lvn_quantile: float = DEFAULT_LVN_QUANTILE,
    num_hvn: int = DEFAULT_NUM_HVN,
    num_lvn: int = DEFAULT_NUM_LVN,
) -> Tuple[List[Tuple[float, float, float]], List[Tuple[float, float, float]]]:
    """Identify High and Low Volume Nodes.

    Bins whose volume is at or above ``hvn_quantile`` are tagged as
    HVN candidates, those at or below ``lvn_quantile`` are tagged as
    LVN candidates. Contiguous candidate bins are merged into zones
    and the top-``num_hvn`` / bottom-``num_lvn`` zones by volume are
    returned (so the caller always gets a stable count regardless of
    threshold settings).
    """
    if profile.size == 0:
        return [], []

    hvn_threshold = float(np.quantile(profile, hvn_quantile))
    lvn_threshold = float(np.quantile(profile, lvn_quantile))

    hvn_mask = profile >= hvn_threshold
    lvn_mask = profile <= lvn_threshold

    hvn_zones = _merge_adjacent(hvn_mask, price_centers, profile, bin_width)
    lvn_zones = _merge_adjacent(lvn_mask, price_centers, profile, bin_width)

    # Rank by volume: HVN descending, LVN ascending.
    hvn_zones.sort(key=lambda z: z[2], reverse=True)
    lvn_zones.sort(key=lambda z: z[2])
    return hvn_zones[:num_hvn], lvn_zones[:num_lvn]


# ---------------------------------------------------------------------------
# One-shot API.
# ---------------------------------------------------------------------------
def compute_vpvr_levels(
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    num_bins: int = DEFAULT_PRICE_BINS,
    value_area_fraction: float = DEFAULT_VALUE_AREA_FRACTION,
    hvn_quantile: float = DEFAULT_HVN_QUANTILE,
    lvn_quantile: float = DEFAULT_LVN_QUANTILE,
    num_hvn: int = DEFAULT_NUM_HVN,
    num_lvn: int = DEFAULT_NUM_LVN,
) -> VolumeProfile:
    """Run the full pipeline and return a :class:`VolumeProfile`."""
    centers, profile, bin_width = build_volume_profile(high, low, volume, num_bins)
    poc_price = find_poc(centers, profile)
    poc_index = int(np.argmin(np.abs(centers - poc_price)))
    val_price, vah_price = find_value_area(
        centers, profile, bin_width, poc_index, value_area_fraction,
    )
    hvn_zones, lvn_zones = find_hvn_lvn(
        centers, profile, bin_width,
        hvn_quantile, lvn_quantile, num_hvn, num_lvn,
    )
    return VolumeProfile(
        price_centers=centers,
        volume=profile,
        bin_width=bin_width,
        poc_price=poc_price,
        vah_price=vah_price,
        val_price=val_price,
        hvn_zones=hvn_zones,
        lvn_zones=lvn_zones,
        total_volume=float(profile.sum()),
        value_area_fraction=value_area_fraction,
    )


__all__ = [
    "DEFAULT_VALUE_AREA_FRACTION",
    "DEFAULT_NUM_HVN",
    "DEFAULT_NUM_LVN",
    "DEFAULT_HVN_QUANTILE",
    "DEFAULT_LVN_QUANTILE",
    "DEFAULT_PRICE_BINS",
    "VolumeProfile",
    "build_volume_profile",
    "find_poc",
    "find_value_area",
    "find_hvn_lvn",
    "compute_vpvr_levels",
]