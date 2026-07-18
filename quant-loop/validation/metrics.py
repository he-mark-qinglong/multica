"""Uniform metric computation for the OOS validation harness.

All frameworks (native engine, backtrader replay, freqtrade replay) funnel
through these functions so gate comparisons are apples-to-apples.

Conventions
-----------
- Returns are simple (not log) returns.
- Sharpe is annualized from DAILY returns with sqrt(365) (crypto trades 24/7).
- max_drawdown is a POSITIVE fraction (0.12 == 12% peak-to-trough loss).
- profit_factor is gross_profit / abs(gross_loss) over per-trade pnl fractions.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 365


def daily_returns(equity: pd.Series) -> pd.Series:
    """Resample a bar-level equity curve to daily simple returns."""
    if equity.empty:
        return pd.Series(dtype=float)
    eq = equity.sort_index()
    if not isinstance(eq.index, pd.DatetimeIndex):
        eq.index = pd.to_datetime(eq.index, utc=True)
    daily = eq.resample("1D").last().dropna()
    return daily.pct_change().dropna()


def annualized_sharpe(daily_ret: pd.Series) -> float:
    if len(daily_ret) < 2:
        return 0.0
    std = daily_ret.std(ddof=1)
    if std <= 0 or not np.isfinite(std):
        return 0.0
    return float(daily_ret.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))


def annualized_return(daily_ret: pd.Series) -> float:
    """Geometric annualized return from daily simple returns."""
    if len(daily_ret) < 1:
        return 0.0
    total = float((1.0 + daily_ret).prod())
    if total <= 0:
        return -1.0
    years = len(daily_ret) / TRADING_DAYS_PER_YEAR
    return float(total ** (1.0 / years) - 1.0) if years > 0 else 0.0


def max_drawdown(equity: pd.Series) -> float:
    """Positive-fraction max drawdown of a bar-level equity curve."""
    if equity.empty:
        return 0.0
    eq = equity.sort_index().astype(float)
    peak = eq.cummax()
    dd = (eq - peak) / peak.replace(0.0, np.nan)
    mdd = float(dd.min())
    return abs(mdd) if np.isfinite(mdd) else 0.0


def profit_factor(trade_pnls: Sequence[float]) -> float:
    pnls = np.asarray(list(trade_pnls), dtype=float)
    gains = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def win_rate(trade_pnls: Sequence[float]) -> float:
    pnls = np.asarray(list(trade_pnls), dtype=float)
    if pnls.size == 0:
        return 0.0
    return float((pnls > 0).mean())


def metrics_from_run(equity: pd.Series, trade_pnls: Sequence[float]) -> dict:
    """One metrics dict for one framework run on one window/symbol."""
    dr = daily_returns(equity)
    pnls = list(trade_pnls)
    return {
        "sharpe": annualized_sharpe(dr),
        "annualized_return": annualized_return(dr),
        "max_drawdown": max_drawdown(equity),
        "profit_factor": profit_factor(pnls),
        "win_rate": win_rate(pnls),
        "n_trades": len(pnls),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0)
        if len(equity) >= 2 and equity.iloc[0]
        else 0.0,
        "daily_returns": dr,  # kept for bootstrap; stripped before JSON export
    }


def public_metrics(m: dict) -> dict:
    """Metrics dict minus heavy internals, JSON-safe."""
    return {k: v for k, v in m.items() if k != "daily_returns"}
