"""Tests for hmm_regime.py and online_adverse.py."""
import sys
sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.market_making.hmm_regime import (
    Regime, RegimeState, detect_regime, get_regime_adjustment,
)
from _shared.market_making.online_adverse import (
    OnlineASParams, OnlineASState, init_online_as,
    observe_fill, get_effective_cost, adaptive_belief_update,
)


# ---- HMM regime ----

def test_detect_calm():
    np.random.seed(42)
    # Low volatility, no trend
    prices = pd.Series(50000 + np.random.randn(200) * 5)
    state = detect_regime(prices, use_hmm=False)
    assert state.regime in (Regime.CALM, Regime.TRENDING)

def test_detect_volatile():
    np.random.seed(42)
    # High volatility
    prices = pd.Series(50000 + np.random.randn(200) * 200)
    state = detect_regime(prices, use_hmm=False)
    assert state.regime == Regime.VOLATILE

def test_regime_too_short():
    prices = pd.Series([50000, 50001])
    state = detect_regime(prices, use_hmm=False)
    assert state.n_observations < 5
    assert state.regime == Regime.CALM

def test_regime_adjustment_calm():
    state = RegimeState(
        regime=Regime.CALM,
        probabilities={"CALM": 0.8, "VOLATILE": 0.1, "TRENDING": 0.1},
        log_likelihood=0, n_observations=100,
    )
    adj = get_regime_adjustment(state)
    assert adj.spread_multiplier < 1.0  # tight spreads in calm

def test_regime_adjustment_volatile():
    state = RegimeState(
        regime=Regime.VOLATILE,
        probabilities={"CALM": 0.1, "VOLATILE": 0.8, "TRENDING": 0.1},
        log_likelihood=0, n_observations=100,
    )
    adj = get_regime_adjustment(state)
    assert adj.spread_multiplier > 1.0  # wide spreads in volatile
    assert adj.size_multiplier < 1.0    # reduced size


# ---- online adverse selection ----

def test_init_uses_prior():
    state = init_online_as()
    assert state.learned_cost_bp == pytest.approx(1.74)

def test_effective_cost_before_min_obs():
    state = init_online_as()
    cost = get_effective_cost(state)
    assert cost == pytest.approx(1.74)  # prior

def test_observe_updates_cost():
    state = init_online_as()
    ts = pd.Timestamp("2026-04-20", tz="UTC")
    state = observe_fill(state, -3.0, ts)  # observed 3bp adverse cost
    assert state.n_observations == 1
    # First obs replaces prior
    assert state.learned_cost_bp == pytest.approx(3.0)

def test_ewma_convergence():
    state = init_online_as()
    ts = pd.Timestamp("2026-04-20", tz="UTC")
    # Feed 100 observations of -5bp
    for i in range(100):
        state = observe_fill(state, -5.0, ts + pd.Timedelta(seconds=i))
    assert state.learned_cost_bp == pytest.approx(5.0, abs=0.1)

def test_adaptive_belief_update():
    state = init_online_as()
    ts = pd.Timestamp("2026-04-20", tz="UTC")
    for i in range(30):
        state = observe_fill(state, -3.0, ts + pd.Timedelta(seconds=i))
    from _shared.market_making.adverse_selection import BID_HIT
    updated = adaptive_belief_update(100.0, BID_HIT, state)
    assert updated < 100.0
