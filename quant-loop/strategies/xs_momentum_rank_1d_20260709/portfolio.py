"""Portfolio construction for the cross-sectional momentum rank strategy.

The portfolio is constructed in two layers:

1. **Long/short side allocation** -- per the spec, ``gross_target_pct`` of
   NAV is split equally across the long and short legs.
2. **Per-leg cap** -- each leg is ``min(per_symbol_max_pct_nav,
   (gross_target_pct / 2) / K)`` of NAV, where K is the number of legs on
   that side. This honors both the spec's per-symbol cap (10%) AND the
   "1/6 of gross" per-leg instruction when K=3 (so gross=60% gives each leg
   =10% with K=3).

Risk overlays:
    - daily_loss_flatten_pct: if today's realized PnL <= this fraction of
      prior equity, flatten every position at the next rebalance.
    - monthly_loss_pause_pct: if trailing 30d PnL <= this fraction of
      peak equity in that window, pause the strategy for ``monthly_pause_days``
      rebalances.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Position representation
# ---------------------------------------------------------------------------


@dataclass
class TargetPosition:
    symbol: str
    side: str          # "LONG" or "SHORT"
    weight: float      # signed weight on NAV (LONG > 0, SHORT < 0)


@dataclass
class PortfolioTarget:
    """The target portfolio at one rebalance."""

    asof: pd.Timestamp
    positions: List[TargetPosition] = field(default_factory=list)
    paused: bool = False         # True when the monthly-pause circuit is open
    pause_until_idx: Optional[int] = None
    pause_reason: str = ""


# ---------------------------------------------------------------------------
# Allocation math
# ---------------------------------------------------------------------------


def equal_weight_allocation(
    long_symbols: List[str],
    short_symbols: List[str],
    gross_target_pct: float,
    per_symbol_max_pct_nav: float,
) -> List[TargetPosition]:
    """Equal-weight long / short construction with hard per-symbol cap.

    Each leg is sized as the minimum of:
      - per_symbol_max_pct_nav (spec default 10%)
      - gross_target_pct / 2 / K   (the spec's "1/6 of gross" when K=3,
                                    generalized for arbitrary K)

    The gross_target_pct is enforced: sum(|w_i|) <= gross_target_pct.
    """
    if not long_symbols and not short_symbols:
        return []
    K_long = max(len(long_symbols), 1)
    K_short = max(len(short_symbols), 1)
    per_leg_long = gross_target_pct / 2.0 / K_long
    per_leg_short = gross_target_pct / 2.0 / K_short
    cap = per_symbol_max_pct_nav
    w_long = min(per_leg_long, cap)
    w_short = min(per_leg_short, cap)
    out: List[TargetPosition] = []
    for s in long_symbols:
        out.append(TargetPosition(symbol=s, side="LONG", weight=+w_long))
    for s in short_symbols:
        out.append(TargetPosition(symbol=s, side="SHORT", weight=-w_short))
    return out


def gross_exposure(target: PortfolioTarget) -> float:
    """Sum of |weight| across positions."""
    return float(sum(abs(p.weight) for p in target.positions))


def enforce_gross_cap(target: PortfolioTarget, gross_target_pct: float) -> PortfolioTarget:
    """If ``gross_exposure(target) > gross_target_pct`` (which can happen
    when the per-symbol cap pushes the sum over the gross cap), scale all
    weights by the same factor so the gross cap holds.

    No-op if the target already satisfies the cap.
    """
    g = gross_exposure(target)
    if g <= gross_target_pct or g <= 0:
        return target
    scale = gross_target_pct / g
    new_positions = [
        TargetPosition(symbol=p.symbol, side=p.side, weight=p.weight * scale)
        for p in target.positions
    ]
    return PortfolioTarget(
        asof=target.asof,
        positions=new_positions,
        paused=target.paused,
        pause_until_idx=target.pause_until_idx,
        pause_reason=target.pause_reason,
    )


# ---------------------------------------------------------------------------
# Risk overlays
# ---------------------------------------------------------------------------


def daily_loss_breach(
    prior_equity: float,
    new_equity: float,
    daily_loss_flatten_pct: float,
) -> bool:
    """Return True if today's equity move <= the flatten threshold.

    ``prior_equity`` is the equity at the start of the day, ``new_equity``
    is the current equity. The threshold is signed negative: e.g. -2%
    means "if we lost 2% or more, flatten".
    """
    if prior_equity <= 0:
        return False
    ret = new_equity / prior_equity - 1.0
    return ret <= daily_loss_flatten_pct


def monthly_pause_active(
    equity_series: pd.Series,
    asof: pd.Timestamp,
    monthly_loss_pause_pct: float,
) -> Tuple[bool, float]:
    """Check whether the trailing-30-day equity curve has breached the
    monthly-loss threshold.

    The check is: if equity at ``asof`` is below
    ``(1 + monthly_loss_pause_pct) * peak(equity in the trailing 30d window)``,
    we are in pause. ``monthly_loss_pause_pct`` is negative.

    Returns ``(breach, drawdown)`` where drawdown is the current equity's
    drop from the trailing-30d peak (a negative number).
    """
    if equity_series.empty:
        return False, 0.0
    window = equity_series[equity_series.index <= asof].tail(30)
    if window.empty:
        return False, 0.0
    peak = window.cummax()
    cur = window.iloc[-1]
    if peak.iloc[-1] <= 0:
        return False, 0.0
    dd = float(cur / peak.iloc[-1] - 1.0)
    return dd <= monthly_loss_pause_pct, dd