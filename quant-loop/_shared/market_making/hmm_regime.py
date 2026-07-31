"""HMM regime detector — classify market state for conditional quoting.

Implements a lightweight 3-state Hidden Markov Model using Gaussian
emissions on log-returns and realized volatility:

  State 0 (CALM):     low vol, mean-reverting → tight spreads, normal size
  State 1 (VOLATILE): high vol, directional    → wide spreads, reduced size
  State 2 (TRENDING): moderate vol, momentum   → skew quotes with trend

The regime posterior conditions all downstream quoting parameters:
  - CALM:     base_spread × 0.8, normal inventory limit
  - VOLATILE: base_spread × 2.0, inventory limit × 0.5
  - TRENDING: skew quotes in trend direction, normal size

Uses hmmlearn if available; falls back to a vol-threshold classifier.

Reference:
  Hamilton, J.D. (1989), "A New Approach to the Economic Analysis of
  Nonstationary Time Series and the Business Cycle"
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

import numpy as np
import pandas as pd


class Regime(IntEnum):
    CALM = 0
    VOLATILE = 1
    TRENDING = 2


@dataclass(frozen=True)
class RegimeState:
    """Current regime classification."""

    regime: Regime
    probabilities: dict[str, float]   # {state_name: posterior prob}
    log_likelihood: float
    n_observations: int


@dataclass(frozen=True)
class RegimeQuoteAdjustment:
    """How to adjust quoting parameters based on regime."""

    spread_multiplier: float = 1.0
    size_multiplier: float = 1.0
    inventory_limit_multiplier: float = 1.0
    skew_direction: float = 0.0       # -1 = skew short, +1 = skew long, 0 = neutral


# Regime → quoting adjustments mapping
REGIME_ADJUSTMENTS = {
    Regime.CALM: RegimeQuoteAdjustment(
        spread_multiplier=0.8,
        size_multiplier=1.0,
        inventory_limit_multiplier=1.0,
        skew_direction=0.0,
    ),
    Regime.VOLATILE: RegimeQuoteAdjustment(
        spread_multiplier=2.0,
        size_multiplier=0.5,
        inventory_limit_multiplier=0.5,
        skew_direction=0.0,
    ),
    Regime.TRENDING: RegimeQuoteAdjustment(
        spread_multiplier=1.2,
        size_multiplier=0.8,
        inventory_limit_multiplier=0.8,
        skew_direction=0.0,  # set dynamically based on trend sign
    ),
}


# ---------------------------------------------------------------------------
# Vol-threshold fallback (no hmmlearn dependency)
# ---------------------------------------------------------------------------

@dataclass
class _VolThresholdParams:
    calm_vol_threshold: float = 0.0008    # per-second vol below this = CALM
    volatile_vol_threshold: float = 0.003  # above this = VOLATILE
    trend_lookback: int = 20
    trend_threshold: float = 3.0           # t-stat for trend detection


def _vol_threshold_classify(
    log_returns: np.ndarray,
    params: _VolThresholdParams,
) -> RegimeState:
    """Fallback classifier using simple vol + trend thresholds."""
    n = len(log_returns)
    if n < 5:
        return RegimeState(
            regime=Regime.CALM,
            probabilities={"CALM": 0.6, "VOLATILE": 0.2, "TRENDING": 0.2},
            log_likelihood=0.0,
            n_observations=n,
        )

    vol = float(np.std(log_returns))
    recent = log_returns[-params.trend_lookback:] if n >= params.trend_lookback else log_returns
    mean_ret = float(np.mean(recent))
    se = vol / math.sqrt(len(recent)) if vol > 0 else 0
    t_stat = mean_ret / se if se > 0 else 0

    probs = {"CALM": 0.2, "VOLATILE": 0.2, "TRENDING": 0.2}

    if vol > params.volatile_vol_threshold:
        regime = Regime.VOLATILE
        probs["VOLATILE"] = 0.7
        probs["CALM"] = 0.15
        probs["TRENDING"] = 0.15
    elif abs(t_stat) > params.trend_threshold:
        regime = Regime.TRENDING
        probs["TRENDING"] = 0.6
        probs["CALM"] = 0.3
        probs["VOLATILE"] = 0.1
    else:
        regime = Regime.CALM
        probs["CALM"] = 0.7
        probs["VOLATILE"] = 0.15
        probs["TRENDING"] = 0.15

    return RegimeState(
        regime=regime,
        probabilities=probs,
        log_likelihood=0.0,
        n_observations=n,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_regime(
    prices: pd.Series | np.ndarray,
    use_hmm: bool = True,
) -> RegimeState:
    """Classify the current market regime from a price series.

    Parameters
    ----------
    prices : array-like
        Recent trade prices (at least 50 for meaningful classification).
    use_hmm : bool
        Try to use hmmlearn if available. Falls back to vol-threshold.

    Returns
    -------
    RegimeState
    """
    arr = np.asarray(prices, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)

    if n < 5:
        return RegimeState(
            regime=Regime.CALM,
            probabilities={"CALM": 0.6, "VOLATILE": 0.2, "TRENDING": 0.2},
            log_likelihood=0.0,
            n_observations=n,
        )

    log_returns = np.diff(np.log(arr))
    log_returns = log_returns[np.isfinite(log_returns)]

    if use_hmm and n >= 100:
        try:
            return _hmm_classify(log_returns)
        except Exception:
            pass

    return _vol_threshold_classify(log_returns, _VolThresholdParams())


def _hmm_classify(log_returns: np.ndarray) -> RegimeState:
    """Fit a 3-state Gaussian HMM and classify the last observation."""
    from hmmlearn.hmm import GaussianHMM

    X = log_returns.reshape(-1, 1)
    model = GaussianHMM(n_components=3, covariance_type="full",
                        n_iter=50, random_state=42)
    model.fit(X)

    # Predict states
    states = model.predict(X)
    last_state = int(states[-1])

    # Compute posterior probabilities for last observation
    posteriors = model.predict_proba(X)[-1]

    # Identify which state is which by volatility
    state_vols = []
    for s in range(3):
        mask = states == s
        if mask.sum() > 0:
            state_vols.append((s, float(np.std(log_returns[mask]))))
        else:
            state_vols.append((s, 0.0))

    # Sort by vol: lowest = CALM, highest = VOLATILE, middle = TRENDING
    state_vols.sort(key=lambda x: x[1])
    calm_state = state_vols[0][0]
    volatile_state = state_vols[2][0]
    trending_state = state_vols[1][0]

    state_map = {calm_state: Regime.CALM, volatile_state: Regime.VOLATILE,
                 trending_state: Regime.TRENDING}
    regime = state_map[last_state]

    probs = {
        "CALM": float(posteriors[calm_state]),
        "VOLATILE": float(posteriors[volatile_state]),
        "TRENDING": float(posteriors[trending_state]),
    }

    return RegimeState(
        regime=regime,
        probabilities=probs,
        log_likelihood=float(model.score(X)),
        n_observations=len(log_returns),
    )


def get_regime_adjustment(
    state: RegimeState,
    trend_sign: float = 0.0,
) -> RegimeQuoteAdjustment:
    """Get quoting parameter adjustments for the current regime.

    Parameters
    ----------
    state : RegimeState
        Current regime classification.
    trend_sign : float
        -1.0 (downtrend) to +1.0 (uptrend). Used for TRENDING regime skew.

    Returns
    -------
    RegimeQuoteAdjustment
    """
    adj = REGIME_ADJUSTMENTS.get(state.regime, REGIME_ADJUSTMENTS[Regime.CALM])

    if state.regime == Regime.TRENDING and trend_sign != 0:
        return RegimeQuoteAdjustment(
            spread_multiplier=adj.spread_multiplier,
            size_multiplier=adj.size_multiplier,
            inventory_limit_multiplier=adj.inventory_limit_multiplier,
            skew_direction=trend_sign,
        )

    return adj
