"""Queue-position-aware wrapper around the authoritative backtester (B6).

Naive backtests assume every entry order fills. For LIMIT entries that is
wrong: a passive order joins the queue and fills only when the market
trades through it. This module wraps ``_shared/run_backtest.py`` (which is
NOT modified) and, for every limit-order entry, applies the
``_shared/market_making/queue_position.py:fill_probability`` model
(Moallemi 2014) to decide

  1. WHETHER the entry fills at all (simulated mode), and
  2. the filled PROPORTION of the intended size (both modes),

then runs the standard engine on the adjusted trade schedule. Market
orders pass through untouched.

Two fill modes (``QueueAwareConfig.mode``):

  ``"expected"``   deterministic — every limit entry "fills" with its
                   size scaled by P(fill). Useful for fast what-if
                   sweeps; identical results across runs.
  ``"simulated"``  stochastic — a seeded RNG draws u ~ U(0,1) per entry;
                   u < P(fill) → full fill, otherwise the trade is
                   dropped. Reproducible via ``QueueAwareConfig.seed``.

The per-trade audit trail (probability, filled flag, applied size) is
returned so experiments can diff the queue-aware vs naive fill sequences
via :func:`compare_queue_impact`.

Reference:
  Moallemi, C.C. (2014), "The Value of Queue Position in a Limit Order
  Book" — fill probability decays with queue age and distance from best.

Pure functions + frozen dataclasses; no I/O. The only loop is over trades
(the fill model is per-order, not per-bar).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Sequence

import numpy as np
import pandas as pd

from _shared.market_making.queue_position import QueueParams, fill_probability
from _shared.run_backtest import Trade, run_backtest

__all__ = [
    "LimitTrade",
    "QueueAwareConfig",
    "FillDecision",
    "run_queue_aware_backtest",
    "compare_queue_impact",
]

OrderType = Literal["limit", "market"]
FillMode = Literal["expected", "simulated"]


@dataclass(frozen=True)
class LimitTrade:
    """A Trade plus the order-book context needed by the queue model.

    ``entry_ts``/``exit_ts``/``direction``/``size_fraction`` mirror
    ``run_backtest.Trade``. The extra fields only matter for
    ``order_type="limit"`` entries:

      ``ticks_from_best``   quote placement (0 = at best bid/ask)
      ``seconds_in_queue``  how long the resting order has been live
      ``market_fill_rate``  observed base fill rate of comparable quotes
    """

    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    direction: Literal["long", "short"]
    size_fraction: float = 1.0
    order_type: OrderType = "limit"
    ticks_from_best: int = 1
    seconds_in_queue: float = 30.0
    market_fill_rate: float = 0.13


@dataclass(frozen=True)
class QueueAwareConfig:
    """Configuration for :func:`run_queue_aware_backtest`."""

    mode: FillMode = "expected"
    seed: int = 42
    queue_params: QueueParams = QueueParams()
    min_fill_probability: float = 0.0  # expected mode: drop trades below this P
    initial_capital: float = 100_000.0
    cost_bps_rt: float = 24.0
    freq_per_year: int = 365 * 24


@dataclass(frozen=True)
class FillDecision:
    """Audit record for one entry order."""

    entry_ts: pd.Timestamp
    order_type: OrderType
    fill_probability: float  # 1.0 for market orders
    filled: bool
    fill_ratio: float        # applied size = size_fraction * fill_ratio


def _decide(
    trade: LimitTrade,
    rng: np.random.Generator,
    config: QueueAwareConfig,
) -> FillDecision:
    """Apply the queue model to one entry order."""
    if trade.order_type == "market":
        return FillDecision(trade.entry_ts, "market", 1.0, True, 1.0)

    p = fill_probability(
        seconds_in_queue=trade.seconds_in_queue,
        ticks_from_best=trade.ticks_from_best,
        market_fill_rate=trade.market_fill_rate,
        params=config.queue_params,
    )
    if config.mode == "expected":
        filled = p >= config.min_fill_probability
        return FillDecision(trade.entry_ts, "limit", p, filled, p if filled else 0.0)
    # simulated
    filled = bool(rng.random() < p)
    return FillDecision(trade.entry_ts, "limit", p, filled, 1.0 if filled else 0.0)


def run_queue_aware_backtest(
    bars: pd.DataFrame,
    trades: Sequence[LimitTrade],
    *,
    config: QueueAwareConfig = QueueAwareConfig(),
) -> Dict[str, Any]:
    """Run the authoritative engine on a queue-filtered trade schedule.

    Parameters
    ----------
    bars : pd.DataFrame
        Passed straight through to ``run_backtest`` (needs ``close``).
    trades : sequence of LimitTrade
        Entry schedule with order-book context. Limit entries are
        filtered/scaled by the queue model; market entries pass through.
    config : QueueAwareConfig

    Returns
    -------
    dict — everything ``run_backtest`` returns (``equity``, ``metrics``,
    ``n_trades``, ``n_skipped``) plus:
      - ``decisions`` : list[FillDecision], one per input trade
      - ``n_entries_submitted`` : int
      - ``n_entries_filled`` : int
      - ``fill_rate`` : filled / submitted (market orders included as fills)
    """
    rng = np.random.default_rng(config.seed)
    decisions: List[FillDecision] = []
    adjusted: List[Trade] = []
    for t in trades:
        d = _decide(t, rng, config)
        decisions.append(d)
        if not d.filled or d.fill_ratio <= 0.0:
            continue
        adjusted.append(
            Trade(
                entry_ts=t.entry_ts,
                exit_ts=t.exit_ts,
                direction=t.direction,
                size_fraction=float(t.size_fraction) * d.fill_ratio,
            )
        )

    result = run_backtest(
        bars,
        adjusted,
        initial_capital=config.initial_capital,
        cost_bps_rt=config.cost_bps_rt,
        cost_mode="fill",
        freq_per_year=config.freq_per_year,
    )
    n_submitted = len(trades)
    n_filled = sum(1 for d in decisions if d.filled)
    result.update(
        {
            "decisions": decisions,
            "n_entries_submitted": n_submitted,
            "n_entries_filled": n_filled,
            "fill_rate": (n_filled / n_submitted) if n_submitted else 0.0,
        }
    )
    return result


def compare_queue_impact(
    bars: pd.DataFrame,
    trades: Sequence[LimitTrade],
    *,
    config: QueueAwareConfig = QueueAwareConfig(),
) -> Dict[str, Any]:
    """Contrast-experiment report: queue-aware vs naive fill sequences.

    Runs the schedule twice — once naively (every order fills in full,
    i.e. all entries treated as marketable) and once through the queue
    model — and diffs the two fill sequences and equity outcomes.

    Returns
    -------
    dict with:
      - ``naive`` / ``queue_aware`` : the two full backtest results
      - ``fill_report`` : pd.DataFrame, one row per input trade with the
        naive size vs the queue-aware applied size and filled flag
      - ``n_trades_naive`` / ``n_trades_queue_aware`` : int
      - ``total_return_naive_pct`` / ``total_return_queue_aware_pct`` : float
      - ``return_diff_pct`` : queue_aware minus naive total return
      - ``fill_rate`` : fraction of submitted entries actually filled
    """
    naive_trades = [
        Trade(t.entry_ts, t.exit_ts, t.direction, float(t.size_fraction))
        for t in trades
    ]
    naive = run_backtest(
        bars,
        naive_trades,
        initial_capital=config.initial_capital,
        cost_bps_rt=config.cost_bps_rt,
        cost_mode="fill",
        freq_per_year=config.freq_per_year,
    )
    qa = run_queue_aware_backtest(bars, trades, config=config)

    fill_report = pd.DataFrame(
        {
            "entry_ts": [t.entry_ts for t in trades],
            "order_type": [t.order_type for t in trades],
            "naive_size": [float(t.size_fraction) for t in trades],
            "fill_probability": [d.fill_probability for d in qa["decisions"]],
            "filled": [d.filled for d in qa["decisions"]],
            "queue_aware_size": [
                float(t.size_fraction) * d.fill_ratio
                for t, d in zip(trades, qa["decisions"])
            ],
        }
    )
    ret_naive = naive["metrics"]["total_return_pct"]
    ret_qa = qa["metrics"]["total_return_pct"]
    return {
        "naive": naive,
        "queue_aware": qa,
        "fill_report": fill_report,
        "n_trades_naive": naive["n_trades"],
        "n_trades_queue_aware": qa["n_trades"],
        "total_return_naive_pct": ret_naive,
        "total_return_queue_aware_pct": ret_qa,
        "return_diff_pct": ret_qa - ret_naive,
        "fill_rate": qa["fill_rate"],
    }
