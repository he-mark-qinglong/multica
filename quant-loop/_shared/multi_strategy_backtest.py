"""Multi-strategy portfolio backtest (B15).

Runs N strategies against the SAME bar frame — each strategy contributes
its own closed-trade schedule — then merges the per-strategy equity
curves at the portfolio layer by capital weight:

  * ``weighting="equal"``    — 1/N capital each.
  * ``weighting="explicit"`` — caller-supplied weights (normalised).
  * ``weighting="erc"``      — Equal Risk Contribution weights from the
    strategies' per-bar return covariance, via
    ``_shared/market_making/portfolio_risk.py:erc_weights``
    (Maillard/Roncalli/Teïletche 2010). Falls back to equal weights when
    the covariance is degenerate (e.g. a strategy with zero variance).

Every strategy is evaluated with the authoritative per-bar compounding
engine ``_shared/run_backtest.py`` on its own capital slice
(``weight * initial_capital``); the portfolio equity is the bar-by-bar
sum of the strategy curves. Outputs both the merged portfolio metrics and
the per-strategy decomposition (same metric keys, comparable Sharpe).

On top of the equity merge, the module also converts each strategy's
trade schedule into a tagged fill stream and replays it through
``_shared/portfolio/account_view.py:build_account_views``
(mode="shared"), yielding an independent average-cost accounting view of
the pool (realized/unrealized PnL, fees) — a cross-check on the
equity-based portfolio result.

References:
  - Maillard, S., Roncalli, T., Teïletche, J. (2010), "The Properties of
    Equally Weighted Risk Contribution Portfolios", Journal of Portfolio
    Management 36(4).
  - Grinold & Kahn (2000), "Active Portfolio Management", Ch. 4
    (multi-strategy book accounting).
  - López de Prado (2018), AFML Ch. 10 (concurrent strategies sharing
    one capital pool).

Pure functions, frozen dataclasses, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from _shared.market_making.portfolio_risk import erc_weights
from _shared.portfolio.account_view import POOL_ID, Fill, build_account_views
from _shared.run_backtest import Trade, _bar_index, _metrics, run_backtest

__all__ = [
    "StrategySpec",
    "MultiStrategyConfig",
    "run_multi_strategy_backtest",
]

Weighting = Literal["equal", "explicit", "erc"]


@dataclass(frozen=True)
class StrategySpec:
    """One strategy's trade schedule over the shared bars.

    ``weight`` is only consulted when ``weighting="explicit"``.
    """

    name: str
    trades: Tuple[Trade, ...]
    weight: float = 1.0


@dataclass(frozen=True)
class MultiStrategyConfig:
    """Configuration for :func:`run_multi_strategy_backtest`."""

    initial_capital: float = 100_000.0
    cost_bps_rt: float = 24.0
    freq_per_year: int = 365 * 24
    weighting: Weighting = "equal"
    symbol: str = "SYNTH"  # symbol tag for the account_view fill stream


def _resolve_weights(
    specs: Sequence[StrategySpec],
    strategy_returns: Optional[pd.DataFrame],
    config: MultiStrategyConfig,
) -> Dict[str, float]:
    """Capital weights per strategy for the requested weighting scheme."""
    names = [s.name for s in specs]
    n = len(names)
    if config.weighting == "equal":
        return {name: 1.0 / n for name in names}
    if config.weighting == "explicit":
        total = sum(s.weight for s in specs)
        if total <= 0:
            raise ValueError("explicit weights must sum to a positive value")
        return {s.name: s.weight / total for s in specs}
    if config.weighting == "erc":
        if strategy_returns is None or strategy_returns.shape[1] != n:
            return {name: 1.0 / n for name in names}
        cov = strategy_returns.cov()
        if not np.isfinite(cov.to_numpy()).all() or (np.diag(cov) <= 0).any():
            # Degenerate covariance (flat strategy / constant equity).
            return {name: 1.0 / n for name in names}
        try:
            return dict(erc_weights(cov).weights)
        except Exception:
            return {name: 1.0 / n for name in names}
    raise ValueError(f"unknown weighting {config.weighting!r}")


def _strategy_returns(
    bars: pd.DataFrame,
    specs: Sequence[StrategySpec],
    config: MultiStrategyConfig,
) -> pd.DataFrame:
    """Per-bar simple returns of each strategy (capital-independent)."""
    cols: Dict[str, pd.Series] = {}
    for spec in specs:
        res = run_backtest(
            bars,
            list(spec.trades),
            initial_capital=1.0,
            cost_bps_rt=config.cost_bps_rt,
            cost_mode="fill",
            freq_per_year=config.freq_per_year,
        )
        cols[spec.name] = res["equity"].pct_change().fillna(0.0)
    return pd.DataFrame(cols)


def _trades_to_fills(
    bars: pd.DataFrame,
    specs: Sequence[StrategySpec],
    weights: Dict[str, float],
    config: MultiStrategyConfig,
) -> List[Fill]:
    """Convert trade schedules into a strategy-tagged fill stream.

    Entry fills at the close of the first held bar (``ei+1``, matching the
    engine's next-bar execution); exit fills at the close of the exit bar
    ``xi``. Quantity sized so notional = ``weight * capital * size_fraction``;
    each fill carries the half-round-trip commission. Trades whose
    entry/exit is not on a bar (or have no held bar) are skipped — the
    same validity rule as ``run_backtest``.
    """
    idx = pd.DatetimeIndex(bars.index)
    close = bars["close"].to_numpy(dtype=float)
    n = len(bars)
    cost_rt = config.cost_bps_rt / 10_000.0
    fills: List[Fill] = []
    for spec in specs:
        capital = config.initial_capital * weights[spec.name]
        for t in spec.trades:
            ei = _bar_index(idx, t.entry_ts)
            xi = _bar_index(idx, t.exit_ts)
            if ei is None or xi is None or xi <= ei or ei + 1 >= n or xi >= n:
                continue
            entry_px = float(close[ei + 1])
            exit_px = float(close[xi])
            qty = capital * float(t.size_fraction) / entry_px
            sign = 1.0 if t.direction == "long" else -1.0
            fills.append(
                Fill(
                    ts=idx[ei + 1],
                    strategy_id=spec.name,
                    symbol=config.symbol,
                    qty=sign * qty,
                    price=entry_px,
                    fee=qty * entry_px * cost_rt / 2.0,
                )
            )
            fills.append(
                Fill(
                    ts=idx[xi],
                    strategy_id=spec.name,
                    symbol=config.symbol,
                    qty=-sign * qty,
                    price=exit_px,
                    fee=qty * exit_px * cost_rt / 2.0,
                )
            )
    return fills


def run_multi_strategy_backtest(
    bars: pd.DataFrame,
    strategies: Sequence[StrategySpec],
    *,
    config: MultiStrategyConfig = MultiStrategyConfig(),
) -> Dict[str, Any]:
    """Portfolio backtest over N strategies sharing one bar frame.

    Parameters
    ----------
    bars : pd.DataFrame
        Shared bar frame (needs ``close``; UTC DatetimeIndex).
    strategies : sequence of StrategySpec
        Each strategy's closed-trade schedule on ``bars``. Names must be
        unique.
    config : MultiStrategyConfig

    Returns
    -------
    dict with:
      - ``weights`` : {name: capital weight}
      - ``portfolio_equity`` : pd.Series — bar-by-bar sum of strategy curves
      - ``portfolio_metrics`` : sharpe / annualised_pct / total_return_pct /
        max_drawdown_pct / n_bars (same keys as ``run_backtest``)
      - ``strategies`` : {name: {"equity", "metrics", "n_trades",
        "n_skipped", "weight", "capital"}}
      - ``pool_account`` : summary dict of the account_view ``__pool__``
        view (final_equity / realized / unrealized / fees / n_fills)
    """
    if not strategies:
        raise ValueError("at least one strategy is required")
    names = [s.name for s in strategies]
    if len(set(names)) != len(names):
        raise ValueError(f"strategy names must be unique, got {names}")
    if config.initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    # ERC needs the strategy return covariance first (capital-independent).
    strat_rets = (
        _strategy_returns(bars, strategies, config)
        if config.weighting == "erc"
        else None
    )
    weights = _resolve_weights(strategies, strat_rets, config)

    per_strategy: Dict[str, Dict[str, Any]] = {}
    equity_sum: Optional[pd.Series] = None
    for spec in strategies:
        capital = config.initial_capital * weights[spec.name]
        res = run_backtest(
            bars,
            list(spec.trades),
            initial_capital=capital,
            cost_bps_rt=config.cost_bps_rt,
            cost_mode="fill",
            freq_per_year=config.freq_per_year,
        )
        per_strategy[spec.name] = {
            "equity": res["equity"],
            "metrics": res["metrics"],
            "n_trades": res["n_trades"],
            "n_skipped": res["n_skipped"],
            "weight": weights[spec.name],
            "capital": capital,
        }
        equity_sum = res["equity"] if equity_sum is None else equity_sum + res["equity"]

    assert equity_sum is not None
    portfolio_metrics = _metrics(equity_sum, config.freq_per_year)

    # Independent accounting cross-check via the shared-pool account view.
    fills = _trades_to_fills(bars, strategies, weights, config)
    pool_summary: Dict[str, Any] = {}
    if fills:
        views = build_account_views(
            fills,
            config.initial_capital,
            mode="shared",
            capital_weights=weights,
        )
        pool = views[POOL_ID]
        pool_summary = {
            "final_equity": pool.final_equity,
            "total_return": pool.total_return,
            "realized_pnl": pool.realized_pnl,
            "unrealized_pnl": pool.unrealized_pnl,
            "total_fees": pool.total_fees,
            "n_fills": pool.n_fills,
        }

    return {
        "weights": weights,
        "portfolio_equity": equity_sum,
        "portfolio_metrics": portfolio_metrics,
        "strategies": per_strategy,
        "pool_account": pool_summary,
    }
