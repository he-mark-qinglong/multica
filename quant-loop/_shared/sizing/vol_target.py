"""Volatility-targeted position sizing layer.

Replaces fixed risk_target_pct=0.005 across strategies. Scales position
size by inverse realized vol so the strategy targets a constant annualized
vol (default 15%). This is NOT a return-enhancer — it's a risk normalizer
that makes strategies comparable and capital deployment regime-aware.

References:
- Moreira & Muir (2017) "Volatility-Managed Portfolios" JF
- Harvey, Hoyle, Rattray, Sargaison, Taylor, Van Hemert (2019) "The Best of
  Strategies for the Worst of Times: Portfolio Protections"
"""
import numpy as np
import pandas as pd


def rolling_realized_vol(
    returns: pd.Series,
    lookback: int = 20,
    periods_per_year: int = 365,
) -> pd.Series:
    """Annualized realized vol from rolling std of returns."""
    return returns.rolling(lookback, min_periods=max(2, lookback // 2)).std() * np.sqrt(periods_per_year)


def vol_target_weights(
    returns: pd.Series,
    target_vol: float = 0.15,
    lookback: int = 20,
    floor: float = 0.1,
    cap: float = 3.0,
    periods_per_year: int = 365,
) -> pd.Series:
    """Daily position-size multiplier to target `target_vol` annualized vol.

    Args:
        returns: per-bar strategy returns (the un-sized version, e.g., signal * fixed_risk)
        target_vol: desired annualized vol (0.15 = 15%)
        lookback: rolling window (in bars) for realized vol estimate
        floor: minimum weight (0.1 = never go below 10% of base size)
        cap: maximum weight (3.0 = never go above 3x base size)
        periods_per_year: annualization (365 for daily, 365*24 for 1h, etc.)

    Returns:
        pd.Series of weights aligned to `returns` index, in range [floor, cap].
        Early bars (before lookback) get weight=1.0 (no information yet).
    """
    realized = rolling_realized_vol(returns, lookback, periods_per_year)
    raw_weight = target_vol / realized.replace(0, np.nan)
    weights = raw_weight.clip(lower=floor, upper=cap).fillna(1.0)
    # Warm-up: weight=1.0 before lookback
    weights.iloc[:lookback] = 1.0
    return weights


def apply_vol_target(
    equity: pd.Series,
    target_vol: float = 0.15,
    lookback: int = 20,
    floor: float = 0.1,
    cap: float = 3.0,
    periods_per_year: int = 365,
) -> pd.Series:
    """Convenience: take an equity curve, return vol-targeted equity curve.

    Args:
        equity: baseline equity curve (from fixed-sizing backtest)
        target_vol, lookback, floor, cap, periods_per_year: see vol_target_weights
    
    Returns:
        New equity curve with per-bar weights applied.
    """
    returns = equity.pct_change().fillna(0.0)
    weights = vol_target_weights(returns, target_vol, lookback, floor, cap, periods_per_year)
    sized_returns = returns * weights
    new_equity = (1 + sized_returns).cumprod()
    # Match starting equity
    if len(equity) > 0:
        new_equity *= equity.iloc[0] / new_equity.iloc[0]
    return new_equity


def sharpe_lift(equity_baseline: pd.Series, equity_sized: pd.Series, periods_per_year: int = 365) -> float:
    """Sharpe(sized) - Sharpe(baseline). Positive means vol-targeting helped."""
    r_b = equity_baseline.pct_change().dropna()
    r_s = equity_sized.pct_change().dropna()
    sb = r_b.mean() / r_b.std() * np.sqrt(periods_per_year) if r_b.std() > 0 else 0.0
    ss = r_s.mean() / r_s.std() * np.sqrt(periods_per_year) if r_s.std() > 0 else 0.0
    return float(ss - sb)
