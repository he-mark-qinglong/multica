"""Dynamic ERC rebalancing with correlation regime detection.

Static ERC computes weights once from a full-sample covariance matrix.
Dynamic ERC rolls the window, detects correlation regime shifts, and
adjusts weights accordingly — reducing exposure when correlations spike.

Jane Street: "It is bad to lose a lot of money."
In a crisis, correlations → 1, diversification disappears, and you
need to cut overall exposure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from _shared.market_making.portfolio_risk import erc_weights, CorrelationResult, compute_correlation


@dataclass(frozen=True)
class DynamicERCParams:
    """Dynamic ERC configuration."""

    lookback: int = 504              # rolling window (e.g., 504 hourly bars = 21 days)
    min_lookback: int = 60           # minimum bars before producing weights
    rebalance_freq: int = 24         # recompute every N bars
    crisis_corr_threshold: float = 0.7   # mean correlation above this → crisis
    crisis_shrinkage: float = 0.5    # multiply all weights by this in crisis
    crisis_vol_threshold: float = 2.0 # portfolio vol / historical avg → crisis


@dataclass(frozen=True)
class DynamicERCResult:
    """Output of one dynamic ERC step."""

    weights: dict[str, float]
    raw_weights: dict[str, float]    # before crisis adjustment
    is_crisis: bool
    mean_correlation: float
    diversification_ratio: float
    n_observations: int


class DynamicERC:
    """Rolling-window ERC with crisis detection.

    Usage:
        derc = DynamicERC(params)
        for timestamp, returns_row in returns_df.iterrows():
            result = derc.update(returns_df.loc[:timestamp])
            weights = result.weights
    """

    def __init__(self, params: DynamicERCParams = DynamicERCParams()):
        self.params = params
        self._last_rebalance = 0
        self._cached_weights: dict[str, float] | None = None
        self._historical_port_vol: list[float] = []

    def update(self, returns: pd.DataFrame) -> DynamicERCResult | None:
        """Compute dynamic ERC weights from a returns DataFrame.

        Parameters
        ----------
        returns : pd.DataFrame
            Strategy/asset returns, columns = names, rows = time.

        Returns
        -------
        DynamicERCResult or None
            None if insufficient data.
        """
        n = len(returns)
        if n < self.params.min_lookback:
            return None

        # Only rebalance at specified frequency
        if self._cached_weights is not None and (n - self._last_rebalance) < self.params.rebalance_freq:
            return DynamicERCResult(
                weights=self._cached_weights,
                raw_weights=self._cached_weights,
                is_crisis=False,
                mean_correlation=0.0,
                diversification_ratio=1.0,
                n_observations=n,
            )

        self._last_rebalance = n

        # Rolling window
        window = returns.tail(self.params.lookback)

        # Correlation analysis
        corr_result = compute_correlation(window)

        # Compute ERC weights from rolling covariance
        cov = window.cov()
        erc = erc_weights(cov)
        raw_weights = erc.weights

        # Crisis detection
        is_crisis = self._detect_crisis(window, corr_result)

        # Apply crisis shrinkage
        if is_crisis:
            adjusted = {k: v * self.params.crisis_shrinkage for k, v in raw_weights.items()}
        else:
            adjusted = dict(raw_weights)

        self._cached_weights = adjusted

        return DynamicERCResult(
            weights=adjusted,
            raw_weights=raw_weights,
            is_crisis=is_crisis,
            mean_correlation=corr_result.mean_correlation,
            diversification_ratio=corr_result.diversification_ratio,
            n_observations=n,
        )

    def _detect_crisis(self, window: pd.DataFrame, corr: CorrelationResult) -> bool:
        """Detect correlation/vol crisis regime."""
        # Signal 1: mean correlation spike (only positive — negative
        # correlation is excellent diversification, not a crisis)
        if corr.mean_correlation > self.params.crisis_corr_threshold:
            return True

        # Signal 2: portfolio vol spike vs history
        port_vol = float(window.sum(axis=1).std())
        self._historical_port_vol.append(port_vol)
        if len(self._historical_port_vol) > 10:
            recent_avg = np.mean(self._historical_port_vol[-10:])
            long_avg = np.mean(self._historical_port_vol)
            if long_avg > 0 and recent_avg / long_avg > self.params.crisis_vol_threshold:
                return True

        return False
