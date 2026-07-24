"""Uniform metric computation for the OOS validation harness.

Thin wrapper around ``_shared/validation/compute_metrics.compute_metrics``
(the single metric schema, SMA-34992). All frameworks (native engine,
backtrader replay, freqtrade replay) funnel through these functions so gate
comparisons are apples-to-apples. Function signatures are unchanged from the
pre-unification module; internals delegate to compute_metrics.

Conventions
-----------
- Returns are simple (not log) returns.
- Sharpe is annualized from DAILY returns with sqrt(365) (crypto trades 24/7).
- max_drawdown is a NEGATIVE fraction (-0.12 == 12% peak-to-trough loss),
  aligned with compute_metrics / _shared.gates.enforce G3. (Changed from the
  legacy positive convention on 2026-07-24, Phase B unification.)
- profit_factor is gross_profit / abs(gross_loss) over per-trade pnl fractions.
- win_rate is the fraction of trades with pnl > 0 (per trade, not per bar).
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from _shared.validation.compute_metrics import compute_metrics

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


def _equity_from_returns(rets: Sequence[float], start: float = 1.0) -> pd.Series:
    """Build an equity curve from simple returns with a leading ``start`` bar.

    The leading bar makes compute_metrics' annualization exponent
    (freq / (n_bars - 1)) line up with len(rets), so annualized_return matches
    the geometric formula total**(365/n) - 1 exactly.
    """
    r = np.asarray(list(rets), dtype=float)
    r = r[np.isfinite(r)]
    eq = np.concatenate([[start], start * np.cumprod(1.0 + r)])
    return pd.Series(eq, dtype=float)


def annualized_sharpe(daily_ret: pd.Series) -> float:
    if len(daily_ret) < 2:
        return 0.0
    m = compute_metrics(_equity_from_returns(daily_ret), n_trades=0,
                        freq_per_year=TRADING_DAYS_PER_YEAR)
    return m["sharpe_daily"]


def annualized_return(daily_ret: pd.Series) -> float:
    """Geometric annualized return from daily simple returns."""
    if len(daily_ret) < 1:
        return 0.0
    total = float((1.0 + daily_ret).prod())
    if total <= 0:
        return -1.0
    m = compute_metrics(_equity_from_returns(daily_ret), n_trades=0,
                        freq_per_year=TRADING_DAYS_PER_YEAR)
    return m["annualized_return"]


def max_drawdown(equity: pd.Series) -> float:
    """Negative-fraction max drawdown of a bar-level equity curve."""
    if equity.empty:
        return 0.0
    m = compute_metrics(equity.sort_index().astype(float), n_trades=0,
                        freq_per_year=TRADING_DAYS_PER_YEAR)
    return m["max_drawdown_pct"]


def profit_factor(trade_pnls: Sequence[float]) -> float:
    """Gross profit / abs(gross loss) over per-trade pnl fractions."""
    pnls = np.asarray(list(trade_pnls), dtype=float)
    gains = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def win_rate(trade_pnls: Sequence[float]) -> float:
    """Fraction of trades with pnl > 0."""
    pnls = np.asarray(list(trade_pnls), dtype=float)
    if pnls.size == 0:
        return 0.0
    return float((pnls > 0).mean())


def metrics_from_run(equity: pd.Series, trade_pnls: Sequence[float]) -> dict:
    """One metrics dict for one framework run on one window/symbol."""
    dr = daily_returns(equity)
    pnls = [float(p) for p in trade_pnls]
    # Sharpe/annualized return are daily-based; max_drawdown is bar-level.
    daily = compute_metrics(_equity_from_returns(dr), n_trades=len(pnls),
                            freq_per_year=TRADING_DAYS_PER_YEAR, trade_pnls=pnls)
    bar = compute_metrics(
        equity.sort_index().astype(float) if len(equity) else pd.Series(dtype=float),
        n_trades=len(pnls), freq_per_year=TRADING_DAYS_PER_YEAR, trade_pnls=pnls)
    total = float((1.0 + dr).prod()) if len(dr) else 1.0
    return {
        "sharpe": daily["sharpe_daily"],
        "annualized_return": -1.0 if len(dr) and total <= 0 else daily["annualized_return"],
        "max_drawdown": bar["max_drawdown_pct"],
        "profit_factor": profit_factor(pnls),
        "win_rate": daily["win_rate"],
        "n_trades": len(pnls),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0)
        if len(equity) >= 2 and equity.iloc[0]
        else 0.0,
        "daily_returns": dr,  # kept for bootstrap; stripped before JSON export
    }


def public_metrics(m: dict) -> dict:
    """Metrics dict minus heavy internals, JSON-safe."""
    return {k: v for k, v in m.items() if k != "daily_returns"}
