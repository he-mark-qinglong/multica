"""Avellaneda-Stoikov market-making parameter calibration.

Calibrates the two key parameters of the Avellaneda-Stoikov (A-S) model
from historical trade/tick data:

  * **κ (kappa)** — order-arrival intensity. Estimated from the rate of
    market-order arrivals (exponential inter-arrival model).
  * **γ (gamma)** — inventory risk-aversion coefficient. Estimated from
    observed PnL variance relative to a target risk-adjusted return.
  * **σ (sigma)** — mid-price volatility. Estimated from the standard
    deviation of mid-price returns.

The A-S reservation price adjusts the mid-price for inventory risk::

    r = mid − q · γ · σ² · (T − t)

where *q* is the current inventory (positive = long). The optimal
spread around the reservation price depends on κ and σ.

This module provides pure estimation functions and a combined
:func:`calibrate` entry point. No I/O — the caller supplies historical
DataFrames/Series.

References:
  - Avellaneda & Stoikov (2008), "High-frequency trading in a limit
    order book", Quantitative Finance 8(3), pp. 217–224.
  - Guéant, Lehalle, & Fernandez-Tapia (2013), "Dealing with the
    inventory risk", Mathematics and Financial Economics 7(4).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CalibratedParams:
    """Output of :func:`calibrate` — A-S model parameters fitted to data.

    Attributes
    ----------
    gamma : float
        Inventory risk-aversion coefficient (higher = more conservative).
    kappa : float
        Order arrival intensity (trades per unit time).
    sigma : float
        Mid-price return volatility (per period).
    n_trades : int
        Number of trades used to estimate κ.
    method : str
        Calibration method identifier (always ``"historical"``).
    """

    gamma: float
    kappa: float
    sigma: float
    n_trades: int
    method: str = "historical"


# ---------------------------------------------------------------------------
# Individual estimators
# ---------------------------------------------------------------------------


def estimate_kappa(trades_df: pd.DataFrame, spread_col: str = "spread") -> float:
    """Estimate order-arrival intensity κ from inter-arrival times.

    Assumes market-order arrivals follow a Poisson process, so
    inter-arrival times are exponentially distributed with rate κ.

    The maximum-likelihood estimate is::

        κ = n / Σ(inter_arrival_times) = 1 / mean(inter_arrival_times)

    Parameters
    ----------
    trades_df : pd.DataFrame
        Must contain a ``timestamp`` column (epoch seconds or
        pandas Timestamp). Rows should be ordered by time.
    spread_col : str
        Column name for the bid-ask spread (not used in the ML estimate
        but accepted for API symmetry with future spread-based methods).

    Returns
    -------
    float
        Estimated κ (arrivals per second). Returns 0.0 if fewer than
        2 trades are provided.
    """
    if "timestamp" not in trades_df.columns:
        raise ValueError("trades_df must contain a 'timestamp' column")
    if len(trades_df) < 2:
        return 0.0

    ts = pd.to_datetime(trades_df["timestamp"])
    ts_sorted = ts.sort_values()
    inter_arrivals = ts_sorted.diff().dropna().dt.total_seconds()

    if len(inter_arrivals) == 0:
        return 0.0
    mean_interval = float(inter_arrivals.mean())
    if mean_interval <= 0:
        return 0.0
    return 1.0 / mean_interval


def estimate_sigma(returns: pd.Series, periods_per_year: int = 365 * 24) -> float:
    """Estimate mid-price return volatility σ.

    Parameters
    ----------
    returns : pd.Series
        Mid-price returns (simple or log). NaNs are dropped.
    periods_per_year : int
        Annualisation factor (default = hourly bars × 365 days).
        The returned σ is **per-period** (not annualised) — the
        annualisation factor is used only to provide context in
        :func:`calibrate`.

    Returns
    -------
    float
        Per-period standard deviation of returns.
    """
    clean = returns.dropna()
    if len(clean) < 2:
        return 0.0
    return float(clean.std(ddof=1))


def estimate_gamma_from_pnl(
    positions: pd.Series,
    pnl: pd.Series,
    target_sharpe: float = 1.5,
) -> float:
    """Estimate inventory risk-aversion γ from observed PnL.

    The A-S model links γ to the desired risk-reward trade-off.
    A practical calibration: choose γ so that the inventory penalty
    term ``q · γ · σ² · T`` is commensurate with the PnL volatility
    needed to achieve the target Sharpe ratio.

    We estimate::

        γ = target_sharpe × σ_pnl / (mean(|q|) × σ²_ret × T)

    where σ_pnl is the per-period PnL std, q is the average absolute
    position, and T is the normalised horizon (1.0).

    Parameters
    ----------
    positions : pd.Series
        Signed inventory (quantity held) per period.
    pnl : pd.Series
        Realised+unrealised PnL per period, aligned to *positions*.
    target_sharpe : float
        Desired annualised Sharpe ratio (default 1.5).

    Returns
    -------
    float
        Estimated γ. Higher γ → more aggressive inventory reduction.
    """
    aligned = pd.concat(
        [positions.rename("pos"), pnl.rename("pnl")], axis=1
    ).dropna()
    if len(aligned) < 2:
        return 1.0  # neutral default

    sigma_pnl = float(aligned["pnl"].std(ddof=1))
    mean_abs_q = float(aligned["pos"].abs().mean())
    sigma_ret = float(aligned["pnl"].std(ddof=1))  # proxy for return vol

    if mean_abs_q < 1e-12 or sigma_ret < 1e-12:
        return 1.0

    gamma = target_sharpe * sigma_pnl / (mean_abs_q * sigma_ret ** 2)
    return max(gamma, 1e-6)  # guard against zero/negative


# ---------------------------------------------------------------------------
# Reservation price
# ---------------------------------------------------------------------------


def reservation_price(
    mid: float,
    q: float,
    sigma: float,
    gamma: float,
    T: float = 1.0,
) -> float:
    """Avellaneda-Stoikov reservation price.

    Adjusts the mid-price for inventory risk::

        r = mid − q · γ · σ² · T

    A long position (*q* > 0) shifts the reservation price *down*
    (encouraging sell orders to reduce inventory), and vice versa.

    Parameters
    ----------
    mid : float
        Current mid-price.
    q : float
        Signed inventory quantity (positive = long).
    sigma : float
        Per-period return volatility.
    gamma : float
        Risk-aversion coefficient.
    T : float
        Time remaining to the trading horizon (normalised to 1.0).

    Returns
    -------
    float
        Reservation price.

    References
    ----------
    Avellaneda & Stoikov (2008), eq. (5).
    """
    return mid - q * gamma * sigma ** 2 * T


# ---------------------------------------------------------------------------
# Combined calibration
# ---------------------------------------------------------------------------


def calibrate(
    trades_df: pd.DataFrame,
    returns: pd.Series,
    positions: Optional[pd.Series] = None,
    pnl: Optional[pd.Series] = None,
    target_sharpe: float = 1.5,
    periods_per_year: int = 365 * 24,
) -> CalibratedParams:
    """Calibrate all A-S parameters from historical data.

    Parameters
    ----------
    trades_df : pd.DataFrame
        Historical trades with a ``timestamp`` column.
    returns : pd.Series
        Mid-price returns series.
    positions : pd.Series, optional
        Signed inventory per period. If None, γ defaults to 1.0.
    pnl : pd.Series, optional
        PnL per period. If None, γ defaults to 1.0.
    target_sharpe : float
        Target Sharpe for γ estimation.
    periods_per_year : int
        Annualisation factor for context.

    Returns
    -------
    CalibratedParams
    """
    kappa = estimate_kappa(trades_df)
    sigma = estimate_sigma(returns, periods_per_year)

    if positions is not None and pnl is not None:
        gamma = estimate_gamma_from_pnl(positions, pnl, target_sharpe)
    else:
        gamma = 1.0

    return CalibratedParams(
        gamma=gamma,
        kappa=kappa,
        sigma=sigma,
        n_trades=len(trades_df),
        method="historical",
    )


__all__ = [
    "CalibratedParams",
    "estimate_kappa",
    "estimate_sigma",
    "estimate_gamma_from_pnl",
    "reservation_price",
    "calibrate",
]
