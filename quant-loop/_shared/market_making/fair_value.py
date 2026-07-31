"""Fair value estimation for market making.

Provides multiple estimators of a security's true value, composable into
a single ``FairValue`` snapshot used as the anchor for quote generation.

References:
  - Glosten & Harris (1988), "Estimating the Components of the Bid/Ask Spread"
  - Jane Street, "Probability & Markets Guide" — Expected Value
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from _shared.indicators.vpvr import compute_vpvr_levels


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketSnapshot:
    """Minimal market state needed for fair-value computation."""

    timestamp: pd.Timestamp
    bid_price: float
    ask_price: float
    bid_volume: float           # L2 best-bid volume (fallback: last trade qty)
    ask_volume: float
    last_price: float
    recent_trades: pd.DataFrame  # columns: ts, price, qty, is_buyer_maker
    bars: pd.DataFrame           # OHLCV: high, low, close, volume


@dataclass(frozen=True)
class FairValue:
    """Composite fair-value estimate."""

    mid: float
    microprice: float
    vwap: float
    vpvr_poc: float
    composite: float
    timestamp: pd.Timestamp


# ---------------------------------------------------------------------------
# Component estimators
# ---------------------------------------------------------------------------

def microprice(
    bid_price: float,
    ask_price: float,
    bid_volume: float,
    ask_volume: float,
) -> float:
    """Glosten-Harris (1988) microprice.

    Weighted average偏向 volume 薄弱的一侧:
        microprice = (ask * bid_vol + bid * ask_vol) / (bid_vol + ask_vol)

    When bid volume dominates (buying pressure), microprice shifts toward ask.
    """
    total_vol = bid_volume + ask_volume
    if total_vol <= 0:
        return 0.5 * (bid_price + ask_price)
    return (ask_price * bid_volume + bid_price * ask_volume) / total_vol


def rolling_vwap(trades: pd.DataFrame, lookback: int = 20) -> float:
    """Volume-weighted average price over the most recent *lookback* trades.

    ``trades`` must have ``price`` and ``qty`` columns.
    """
    if trades.empty or lookback <= 0:
        return float("nan")
    tail = trades.tail(lookback)
    total_qty = tail["qty"].sum()
    if total_qty <= 0:
        return float("nan")
    return float((tail["price"] * tail["qty"]).sum() / total_qty)


def vpvr_fair_value(
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    num_bins: int = 200,
) -> float:
    """VPVR Point-of-Control as fair value.

    Delegates to ``_shared.indicators.vpvr.compute_vpvr_levels`` and returns
    the POC price (highest-volume price node).
    """
    if len(high) == 0:
        return float("nan")
    vp = compute_vpvr_levels(high, low, volume, num_bins=num_bins)
    return float(vp.poc_price)


# ---------------------------------------------------------------------------
# Composite estimator
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS = {"microprice": 0.4, "vwap": 0.3, "vpvr_poc": 0.3}


def compute_fair_value(
    snapshot: MarketSnapshot,
    vwap_lookback: int = 20,
    vpvr_bars: int = 200,
    weights: Optional[dict[str, float]] = None,
) -> FairValue:
    """Fuse three estimators into a single composite fair value.

    Missing components are gracefully dropped and remaining weights
    re-normalised so that the composite is always a valid weighted average.
    """
    w = weights if weights is not None else dict(_DEFAULT_WEIGHTS)

    mid = 0.5 * (snapshot.bid_price + snapshot.ask_price)
    mp = microprice(
        snapshot.bid_price, snapshot.ask_price,
        snapshot.bid_volume, snapshot.ask_volume,
    )

    vwap_val = rolling_vwap(snapshot.recent_trades, vwap_lookback)
    vpvr_val = float("nan")
    if len(snapshot.bars) > 0:
        tail = snapshot.bars.tail(vpvr_bars)
        vpvr_val = vpvr_fair_value(
            tail["high"], tail["low"], tail["volume"],
        )

    # --- re-normalise weights over available components ---
    active: dict[str, float] = {}
    candidates = {"microprice": mp, "vwap": vwap_val, "vpvr_poc": vpvr_val}
    for key, val in candidates.items():
        if key in w and np.isfinite(val):
            active[key] = w[key]

    if not active:
        composite = mid
    else:
        total_w = sum(active.values())
        composite = sum(active[k] * candidates[k] for k in active) / total_w

    return FairValue(
        mid=mid,
        microprice=mp,
        vwap=vwap_val if np.isfinite(vwap_val) else mid,
        vpvr_poc=vpvr_val if np.isfinite(vpvr_val) else mid,
        composite=composite,
        timestamp=snapshot.timestamp,
    )
