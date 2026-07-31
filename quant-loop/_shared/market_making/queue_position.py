"""Queue position fill probability model.

Without L2 data, we can't know our exact queue position. But we can
model the *probability* of getting filled as a function of:

  1. Order arrival rate (from aggTrades frequency)
  2. Our quote aggressiveness (how close to mid)
  3. Time in queue

This lets the quoting engine estimate expected fill probability before
posting, and adjust size accordingly.

Reference:
  Moallemi, C.C. (2014), "The Value of Queue Position in a Limit Order Book"
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class QueueParams:
    """Queue position model parameters."""

    base_fill_rate: float = 0.13      # observed fill rate from simulation
    decay_per_second: float = 0.02    # probability decay as order ages
    aggressiveness_bonus: float = 2.0 # multiplier per tick inside the spread


def fill_probability(
    seconds_in_queue: float,
    ticks_from_best: int,
    market_fill_rate: float,
    params: QueueParams = QueueParams(),
) -> float:
    """Estimate probability of being filled.

    Parameters
    ----------
    seconds_in_queue : float
        How long the order has been active.
    ticks_from_best : int
        0 = at best bid/ask, 1 = one tick behind, etc.
    market_fill_rate : float
        Observed fraction of quotes that get filled (from simulation: ~13%).

    Returns
    -------
    float
        Fill probability in [0, 1].
    """
    # Base rate decays with time
    base = market_fill_rate * math.exp(-params.decay_per_second * seconds_in_queue)

    # Closer to best = higher probability
    aggressiveness = params.aggressiveness_bonus ** (-ticks_from_best)

    return min(1.0, base * aggressiveness)


def expected_fill_value(
    fill_prob: float,
    edge_bp: float,
) -> float:
    """Expected value of posting an order = P(fill) × edge.

    If this is negative or below threshold, don't post.
    """
    return fill_prob * edge_bp


def optimal_quote_aggressiveness(
    spread_bp: float,
    adverse_selection_bp: float,
    market_fill_rate: float = 0.13,
) -> int:
    """Decide how many ticks from best to place our order.

    Returns the optimal ticks_from_best (0 = most aggressive).

    Logic:
      - If edge > adverse_selection_cost → be aggressive (0 ticks)
      - If edge ≈ adverse_selection_cost → be passive (1-2 ticks)
      - If edge < adverse_selection_cost → don't quote (return -1)
    """
    net_edge = spread_bp - adverse_selection_bp
    if net_edge <= 0:
        return -1  # don't quote
    if net_edge > adverse_selection_bp * 2:
        return 0   # aggressive
    if net_edge > adverse_selection_bp:
        return 1   # moderate
    return 2       # passive
