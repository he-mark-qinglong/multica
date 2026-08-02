"""Stress testing engine — predefined crisis scenarios.

Applies historical-style shocks to a portfolio of strategies and
estimates PnL impact, drawdown, and recovery time. This is the
quantitative answer to Jane Street's "how bad can it get?"

Scenarios:
  - Flash crash:      -10% in 1 hour, recovery in 4 hours
  - Funding spike:    +0.5% funding rate (8h annualized)
  - Liquidation cascade: -20% in 30 min, slow recovery
  - Correlation breakdown: all strategies → correlation 0.9
  - Spread blowout:  10× normal spread for 1 hour
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StressScenario:
    """A predefined stress scenario."""

    name: str
    description: str
    price_shock_pct: float = 0.0     # e.g. -0.10 = -10%
    funding_shock_pct: float = 0.0   # e.g. 0.005 = +0.5% per 8h
    spread_multiplier: float = 1.0   # e.g. 10.0 = 10× normal
    correlation_override: float | None = None  # e.g. 0.9 = all correlated
    duration_hours: float = 1.0


@dataclass(frozen=True)
class StressResult:
    """Impact estimate for one scenario."""

    scenario: str
    pnl_impact_usd: float
    pnl_impact_pct: float
    max_drawdown_pct: float
    var_before_bp: float
    var_after_bp: float
    survival: bool                   # True if portfolio survives (DD < threshold)


# ---------------------------------------------------------------------------
# Predefined scenarios
# ---------------------------------------------------------------------------

SCENARIOS = {
    "flash_crash": StressScenario(
        name="Flash Crash",
        description="BTC -10% in 1h, recovery in 4h (cf. 2020-03-12)",
        price_shock_pct=-0.10,
        spread_multiplier=5.0,
        duration_hours=1.0,
    ),
    "funding_spike": StressScenario(
        name="Funding Spike",
        description="Funding rate +0.5% per 8h (extreme bull)",
        funding_shock_pct=0.005,
        spread_multiplier=2.0,
        duration_hours=8.0,
    ),
    "liquidation_cascade": StressScenario(
        name="Liquidation Cascade",
        description="BTC -20% in 30min, cascading liquidations",
        price_shock_pct=-0.20,
        spread_multiplier=10.0,
        duration_hours=0.5,
    ),
    "corr_breakdown": StressScenario(
        name="Correlation Breakdown",
        description="All strategies → correlation 0.9 (crisis mode)",
        correlation_override=0.9,
        spread_multiplier=3.0,
        duration_hours=4.0,
    ),
    "spread_blowout": StressScenario(
        name="Spread Blowout",
        description="Order book thins, 10× normal spread for 1h",
        spread_multiplier=10.0,
        duration_hours=1.0,
    ),
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def run_stress_test(
    scenario: StressScenario,
    capital_usd: float,
    net_inventory_usd: float,
    historical_pnl_bp: list[float],
    historical_spread_bp: float = 4.0,
    survival_threshold_pct: float = -0.25,
    n_strategies: int = 1,
) -> StressResult:
    """Estimate portfolio impact under a stress scenario.

    Parameters
    ----------
    scenario : StressScenario
        The scenario to apply.
    capital_usd : float
        Total deployed capital.
    net_inventory_usd : float
        Net position notional (signed: positive = long).
    historical_pnl_bp : list[float]
        Historical per-trade PnL in bp (for VaR estimation).
    historical_spread_bp : float
        Normal market spread (bp).
    survival_threshold_pct : float
        Max acceptable drawdown (e.g. -0.25 = -25%).
    n_strategies : int
        Number of strategies/assets in the portfolio — used to scale
        VaR when *correlation_override* is active (diversification
        benefit collapses as correlations → 1).

    Returns
    -------
    StressResult
    """
    pnl_impacts: list[float] = []

    # 1. Price shock impact (on net inventory)
    if scenario.price_shock_pct != 0 and net_inventory_usd != 0:
        price_impact = net_inventory_usd * scenario.price_shock_pct
        pnl_impacts.append(price_impact)

    # 2. Funding cost (on inventory held during scenario)
    if scenario.funding_shock_pct != 0 and net_inventory_usd > 0:
        # Long pays positive funding
        funding_cost = net_inventory_usd * scenario.funding_shock_pct * (scenario.duration_hours / 8.0)
        pnl_impacts.append(-funding_cost)

    # 3. Spread widening cost (increased adverse selection)
    if scenario.spread_multiplier > 1.0:
        from _shared.market_making.tail_risk import historical_var
        base_var = historical_var(historical_pnl_bp, 0.95) if historical_pnl_bp else 4.0
        stressed_var = base_var * scenario.spread_multiplier
    else:
        base_var = 4.0
        stressed_var = base_var

    # 3b. Correlation breakdown: diversification benefit collapses.
    # Portfolio vol scales by √(1+(n-1)ρ) relative to the uncorrelated
    # baseline, so VaR is amplified by the same factor.
    if scenario.correlation_override is not None and n_strategies > 1:
        vol_amplification = float(
            np.sqrt(1.0 + (n_strategies - 1) * scenario.correlation_override)
        )
        stressed_var *= vol_amplification

    total_impact = sum(pnl_impacts)
    pnl_pct = total_impact / capital_usd if capital_usd > 0 else 0.0

    # Drawdown estimate: max of price shock + spread cost
    max_dd = min(pnl_pct, -stressed_var / 10_000.0 * 3)  # 3× VaR as DD proxy

    survival = max_dd > survival_threshold_pct

    return StressResult(
        scenario=scenario.name,
        pnl_impact_usd=total_impact,
        pnl_impact_pct=pnl_pct,
        max_drawdown_pct=max_dd,
        var_before_bp=base_var,
        var_after_bp=stressed_var,
        survival=survival,
    )


def run_all_stress_tests(
    capital_usd: float,
    net_inventory_usd: float,
    historical_pnl_bp: list[float],
    historical_spread_bp: float = 4.0,
    n_strategies: int = 1,
) -> list[StressResult]:
    """Run all predefined stress scenarios."""
    results = []
    for scenario in SCENARIOS.values():
        result = run_stress_test(
            scenario=scenario,
            capital_usd=capital_usd,
            net_inventory_usd=net_inventory_usd,
            historical_pnl_bp=historical_pnl_bp,
            historical_spread_bp=historical_spread_bp,
            n_strategies=n_strategies,
        )
        results.append(result)
    return results
