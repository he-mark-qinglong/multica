"""Portfolio performance & drawdown attribution (I10, I15).

Three complementary decompositions:

  1. :func:`brinson_decomposition` — classic Brinson-Hood-Beebower
     active-return decomposition per segment (strategy or symbol):
     allocation + selection + interaction (I10).

  2. :func:`pnl_contribution` — simple PnL-share attribution: which
     strategies/symbols made or lost the money (I10).

  3. :func:`drawdown_attribution` — drawdown-period attribution: find the
     portfolio's max-drawdown window and decompose the loss over that
     window by contributor; also :func:`time_contribution` for
     period-sliced contributions (I15).

Return-based convention: contributors' compounded returns over the
drawdown window are converted to PnL via the portfolio equity at the
window peak, so contributions sum (approximately, to cross-term order)
to the portfolio drawdown.

Pure functions, no I/O.

References:
  - Brinson, Hood & Beebower (1986), "Determinants of Portfolio
    Performance", Financial Analysts Journal.
  - Grinold & Kahn (2000), "Active Portfolio Management", Ch. 17
    (performance attribution).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Brinson decomposition (I10)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrinsonResult:
    """Per-segment Brinson active-return decomposition."""

    segments: tuple
    allocation: Dict[str, float]     # (wp - wb) * rb
    selection: Dict[str, float]      # wb * (rp - rb)
    interaction: Dict[str, float]    # (wp - wb) * (rp - rb)
    total_active_return: float       # sum of all three effects


def brinson_decomposition(
    portfolio_weights: Mapping[str, float],
    portfolio_returns: Mapping[str, float],
    benchmark_weights: Mapping[str, float],
    benchmark_returns: Mapping[str, float],
) -> BrinsonResult:
    """Brinson-Hood-Beebower decomposition over matching segments.

    All four mappings must share the same segment keys (union is used;
    missing keys count as zero weight/return).
    """
    segments = tuple(sorted(
        set(portfolio_weights) | set(benchmark_weights)
        | set(portfolio_returns) | set(benchmark_returns)
    ))
    alloc, select, interact = {}, {}, {}
    for seg in segments:
        wp = portfolio_weights.get(seg, 0.0)
        wb = benchmark_weights.get(seg, 0.0)
        rp = portfolio_returns.get(seg, 0.0)
        rb = benchmark_returns.get(seg, 0.0)
        alloc[seg] = (wp - wb) * rb
        select[seg] = wb * (rp - rb)
        interact[seg] = (wp - wb) * (rp - rb)
    total = sum(alloc.values()) + sum(select.values()) + sum(interact.values())
    return BrinsonResult(segments, alloc, select, interact, total)


# ---------------------------------------------------------------------------
# PnL contribution (I10)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContributionResult:
    """PnL-share attribution across contributors."""

    pnl: Dict[str, float]
    share_of_total: Dict[str, float]      # pnl / sum(pnl); 0 if total == 0
    share_of_gross: Dict[str, float]      # pnl / sum(|pnl|)
    total_pnl: float
    top_contributor: str
    worst_contributor: str


def pnl_contribution(pnl: Mapping[str, float]) -> ContributionResult:
    """Attribute total PnL across strategies or symbols."""
    if not pnl:
        raise ValueError("pnl mapping is empty")
    total = sum(pnl.values())
    gross = sum(abs(v) for v in pnl.values())
    share_total = (
        {k: (v / total if total != 0.0 else 0.0) for k, v in pnl.items()}
    )
    share_gross = (
        {k: (v / gross if gross != 0.0 else 0.0) for k, v in pnl.items()}
    )
    top = max(pnl, key=lambda k: pnl[k])
    worst = min(pnl, key=lambda k: pnl[k])
    return ContributionResult(
        dict(pnl), share_total, share_gross, total, top, worst
    )


# ---------------------------------------------------------------------------
# Drawdown attribution (I15)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DrawdownAttribution:
    """Decomposition of the max-drawdown window by contributor."""

    peak: pd.Timestamp
    trough: pd.Timestamp
    max_drawdown: float                    # portfolio return peak->trough (<= 0)
    contributions: Dict[str, float]        # PnL units, signed (negative = loss)
    contribution_shares: Dict[str, float]  # of total drawdown loss (losers only)
    top_detractor: str


def drawdown_attribution(
    returns: pd.DataFrame,
    weights: Mapping[str, float] | None = None,
    initial_equity: float = 1.0,
) -> DrawdownAttribution:
    """Find the portfolio's max-drawdown window and attribute the loss.

    Parameters
    ----------
    returns : pd.DataFrame
        Per-bar returns, columns = contributors (strategies or symbols).
    weights : optional static weights; defaults to equal weight.
    initial_equity : starting portfolio equity (PnL scaling).
    """
    if returns.empty or len(returns.columns) == 0:
        raise ValueError("returns frame is empty")
    cols = list(returns.columns)
    if weights is None:
        w = pd.Series(1.0 / len(cols), index=cols)
    else:
        w = pd.Series({c: weights.get(c, 0.0) for c in cols})

    port_ret = returns.mul(w, axis=1).sum(axis=1)
    equity = initial_equity * (1.0 + port_ret).cumprod()
    running_max = equity.cummax()
    dd = equity / running_max - 1.0

    trough = dd.idxmin()
    max_dd = float(dd.loc[trough])
    peak = equity.loc[:trough].idxmax()

    window = returns.loc[peak:trough]
    equity_at_peak = float(equity.loc[peak])
    contributions: Dict[str, float] = {}
    for c in cols:
        compounded = float((1.0 + window[c]).prod() - 1.0)
        contributions[c] = equity_at_peak * w[c] * compounded

    loss_total = sum(v for v in contributions.values() if v < 0)
    shares = {
        c: (v / loss_total if loss_total < 0 and v < 0 else 0.0)
        for c, v in contributions.items()
    }
    top_detractor = min(contributions, key=lambda k: contributions[k])
    return DrawdownAttribution(
        peak=peak, trough=trough, max_drawdown=max_dd,
        contributions=contributions, contribution_shares=shares,
        top_detractor=top_detractor,
    )


def time_contribution(
    returns: pd.DataFrame,
    freq: str = "ME",
) -> pd.DataFrame:
    """Period-sliced compounded contribution per contributor.

    Returns a DataFrame indexed by period start, columns = contributors,
    values = compounded return within the period. The ``__total__`` column
    is the equal-weight portfolio period return.
    """
    if returns.empty:
        raise ValueError("returns frame is empty")
    grouped = (1.0 + returns).groupby(pd.Grouper(freq=freq)).prod() - 1.0
    grouped["__total__"] = grouped.mean(axis=1)
    return grouped
