"""Avellaneda-Stoikov reservation price.

Adjusts the fair value by the cost of carrying inventory: when long, the
reservation price drops below fair value (encourage selling); when short,
it rises above (encourage buying).

Reference:
  Avellaneda & Stoikov (2008), "High-frequency trading in a limit order book"
  Jane Street, "Probability & Markets Guide" — Inventory Risk
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ReservationPriceParams:
    """Tunables for the reservation-price formula."""

    gamma: float = 0.1              # risk-aversion coefficient
    sigma_window: int = 60          # vol lookback (seconds)
    horizon_seconds: float = 300.0  # rebalance horizon T


def reservation_price(
    fair_value: float,
    inventory_qty: float,
    sigma: float,
    time_remaining: float,
    gamma: float = 0.1,
) -> float:
    """Avellaneda-Stoikov reservation price ``r = s - q·γ·σ²·(T-t)``.

    Parameters
    ----------
    fair_value : float
        Current composite fair value ``s``.
    inventory_qty : float
        Net inventory ``q`` — positive = long, negative = short.
    sigma : float
        Per-second realised volatility ``σ``.
    time_remaining : float
        Seconds until the rebalance horizon ``(T - t)``.
    gamma : float
        Risk-aversion coefficient ``γ``.

    Returns
    -------
    float
        Reservation price.  Equal to *fair_value* when ``q`` or ``σ``
        or *time_remaining* is zero.
    """
    if time_remaining <= 0 or sigma <= 0 or gamma <= 0:
        return fair_value
    return fair_value - inventory_qty * gamma * sigma ** 2 * time_remaining


def rolling_sigma(trades: pd.DataFrame, window_seconds: int = 60) -> float:
    """Per-second realised volatility from recent aggTrades.

    Computes log-returns of consecutive trade prices within the trailing
    *window_seconds* window, then returns the standard deviation scaled
    to per-second (i.e. raw std of per-trade log-returns multiplied by
    sqrt(trades_per_second)).

    ``trades`` must have ``ts`` (datetime) and ``price`` columns.
    """
    if trades.empty or len(trades) < 2:
        return 0.0
    tail = trades.tail(500)  # cap computation cost
    ts = tail["ts"]
    if ts.iloc[-1] == ts.iloc[0]:
        return 0.0
    elapsed = (ts.iloc[-1] - ts.iloc[0]).total_seconds()
    if elapsed <= 0:
        return 0.0
    prices = tail["price"].astype(float).to_numpy()
    log_rets = np.diff(np.log(prices))
    log_rets = log_rets[np.isfinite(log_rets)]
    if len(log_rets) < 2:
        return 0.0
    per_trade_vol = float(np.std(log_rets, ddof=1))
    trades_per_second = len(log_rets) / elapsed
    return per_trade_vol * math.sqrt(trades_per_second)
