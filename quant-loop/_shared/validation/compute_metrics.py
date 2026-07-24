"""Shared helper to compute the 9-key metrics dict expected by metrics_validator.

Per strategy-worker-2 gap #1 (SMA-34992 / 2026-07-20): previously each strategy
hand-rolled this dict, which led to per-strategy metric drift (cf. SMA-34922
max_dd sentinel bug). Single source of truth here.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    if den == 0 or math.isnan(den) or math.isinf(den):
        return default
    return float(num) / float(den)


def compute_metrics(
    equity: pd.Series,
    n_trades: int,
    freq_per_year: int = 365,
) -> dict[str, Any]:
    """Compute sharpe/ann_ret/max_dd/pf/n_trades/n_bars/win_rate/calmar/sortino.

    Args:
        equity: per-bar equity curve (index = timestamp), starting value > 0.
        n_trades: trade count supplied by the backtest (positions opened+closed).
        freq_per_year: bars per year (365 for daily, 365*24*60 for 1m, etc.).

    Returns:
        Dict with the 9 metric keys. Values are floats (or int for n_trades).
        Sentinels (max_dd=-4e-6, etc.) cannot appear by construction.
    """
    if len(equity) == 0:
        return {
            "sharpe_daily": 0.0,
            "annualized_return": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "n_trades": int(n_trades),
            "n_bars": 0,
            "win_rate": 0.0,
            "calmar": 0.0,
            "sortino": 0.0,
        }

    equity = equity.astype(float)
    n_bars = len(equity)
    start = float(equity.iloc[0])
    end = float(equity.iloc[-1])
    rets = equity.pct_change().fillna(0.0)

    # Annualized return (geometric): (end/start)^(freq/start_bars) - 1
    if n_bars > 1 and start > 0:
        annualized_return = (end / start) ** (freq_per_year / (n_bars - 1)) - 1.0
    else:
        annualized_return = 0.0

    # Sharpe (annualized, ddof=1)
    if len(rets) >= 2 and float(rets.std(ddof=1)) > 1e-12:
        sharpe = float(rets.mean() / rets.std(ddof=1) * np.sqrt(freq_per_year))
    else:
        sharpe = 0.0

    # Max drawdown (decimal)
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    # Profit factor + win rate (on bar returns; losers define negative)
    pos = rets[rets > 0].sum()
    neg = -rets[rets < 0].sum()
    pf = _safe_div(pos, neg, default=0.0)
    n_pos = int((rets > 0).sum())
    win_rate = n_pos / max(n_bars, 1)

    # Calmar = annualized return / |max_dd|
    calmar = _safe_div(annualized_return, abs(max_dd), default=0.0)

    # Sortino: downside deviation (returns < 0 only)
    downside = rets[rets < 0]
    if len(downside) >= 2 and float(downside.std(ddof=1)) > 1e-12:
        sortino = float(rets.mean() / downside.std(ddof=1) * np.sqrt(freq_per_year))
    else:
        sortino = 0.0

    return {
        "sharpe_daily": float(sharpe),
        "annualized_return": float(annualized_return),
        "max_drawdown_pct": float(max_dd),
        "profit_factor": float(pf),
        "n_trades": int(n_trades),
        "n_bars": int(n_bars),
        "win_rate": float(win_rate),
        "calmar": float(calmar),
        "sortino": float(sortino),
    }
