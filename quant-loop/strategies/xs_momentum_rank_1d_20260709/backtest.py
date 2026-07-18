"""Cross-sectional momentum rank backtest.

A daily-rebalance long/short backtest on a panel of daily OHLCV frames.

Mechanics
---------
1. For each daily bar ``t`` in the union of dates across all symbols:
    a. Compute the universe filter (liquidity gate).
    b. Compute per-symbol momentum scores using ``strategy.compute_momentum_score``.
    c. Rank, pick top-K longs and bottom-K shorts (``strategy.select_long_short``).
    d. Construct equal-weight targets (``portfolio.equal_weight_allocation``).
    e. Apply risk overlays:
        - daily loss flatten: if today's mark-to-market PnL breaches the
          threshold, force the next bar's target to be flat (no positions).
        - monthly pause: if trailing 30d peak drawdown breaches the
          monthly threshold, force the next 30 bars to be flat.
2. Track each symbol's exposure day-over-day; realized PnL is computed from
   the prior bar's positions and today's close, net of round-trip fees on
   the *delta* (the change in position size -- either opening, closing or
   rebalancing) so per-rebalance turnover is charged but holding a position
   overnight is not double-charged.

The output is a ``BacktestResult`` dataclass with the equity curve, gross
exposure schedule, turnover, Sharpe, Sortino, max drawdown and the trade
log.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from portfolio import (
    PortfolioTarget,
    TargetPosition,
    daily_loss_breach,
    enforce_gross_cap,
    equal_weight_allocation,
    gross_exposure,
    monthly_pause_active,
)
from strategy import (
    build_signals,
    rank_symbols_on,
    select_long_short,
)
from universe import (
    UniverseConfig,
    eligible_symbols_on,
    load_universe_config,
)

CONFIG_PATH = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class RebalanceEvent:
    date: pd.Timestamp
    target_positions: List[TargetPosition]
    gross: float
    turnover: float
    notes: str = ""


@dataclass
class BacktestResult:
    strategy: str
    n_rebalances: int
    total_return: float
    annualized_return: float
    annualized_sharpe: float
    annualized_sortino: float
    max_drawdown: float
    avg_gross: float
    total_turnover: float
    avg_per_bar_turnover: float
    equity_curve: pd.Series = field(default_factory=pd.Series)
    gross_series: pd.Series = field(default_factory=pd.Series)
    turnover_series: pd.Series = field(default_factory=pd.Series)
    events: List[RebalanceEvent] = field(default_factory=list)
    paused_days: int = 0
    daily_loss_flatten_days: int = 0


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------


def _cost_per_side(cfg: dict) -> float:
    fee = cfg.get("fees_bps_per_side", 0.0) / 10000.0
    slip = cfg.get("slippage_bps_per_side", 0.0) / 10000.0
    return fee + slip


def _prior_positions(positions: Dict[str, float]) -> Dict[str, float]:
    return dict(positions)


def _new_positions_from_target(
    target: PortfolioTarget, equity: float
) -> Dict[str, float]:
    """Translate signed weights into dollar exposures.

    SHORT weight is interpreted as *selling* ``weight * equity`` of the
    notional -- the PnL for the day on a SHORT leg is then ``- weight * equity * ret``
    (since the leg gains when the underlying drops). This keeps the daily
    PnL formula uniform across long/short.
    """
    out: Dict[str, float] = {}
    for p in target.positions:
        out[p.symbol] = float(p.weight) * equity
    return out


def _realized_pnl(
    prior_positions: Dict[str, float],
    prior_closes: Dict[str, float],
    cur_closes: Dict[str, float],
) -> float:
    """Mark-to-market PnL for one bar given prior dollar exposures and
    today's closes (using yesterday's closes as entry levels for SHORT
    PnL -- the backtest charges cost on the *delta* separately).

    For each symbol the daily PnL is:
        long  : pos_dollar * (cur_close / prior_close - 1)
        short : pos_dollar * (1 - cur_close / prior_close)
              = pos_dollar * -(cur_close / prior_close - 1)

    so we can express both cases uniformly by signing the position: a
    negative ``pos_dollar`` (short) flips the sign of the return.
    """
    pnl = 0.0
    for sym, pos in prior_positions.items():
        p0 = prior_closes.get(sym)
        p1 = cur_closes.get(sym)
        if p0 is None or p1 is None or p0 == 0:
            continue
        ret = p1 / p0 - 1.0
        pnl += pos * ret
    return pnl


def _delta_turnover_cost(
    prior_positions: Dict[str, float],
    new_positions: Dict[str, float],
    cost_per_side_frac: float,
) -> float:
    """Sum of |delta| across symbols, applied as two-sided cost.

    cost = sum_sym |pos_new - pos_old| * cost_per_side_frac * 2
    """
    syms = set(prior_positions) | set(new_positions)
    gross_delta = 0.0
    for s in syms:
        gross_delta += abs(new_positions.get(s, 0.0) - prior_positions.get(s, 0.0))
    return gross_delta * cost_per_side_frac * 2.0


def run_backtest(
    per_symbol_dfs: Dict[str, pd.DataFrame],
    cfg: Optional[dict] = None,
    universe_cfg: Optional[UniverseConfig] = None,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
) -> BacktestResult:
    """Run the cross-sectional momentum backtest.

    Parameters
    ----------
    per_symbol_dfs : dict of symbol -> 1d OHLCV frame.
    cfg : strategy config (defaults to ``config.json``).
    universe_cfg : universe config (defaults to ``config.json``).
    start, end : optional date bounds (inclusive) on the daily index.
    """
    cfg = cfg or json.loads(CONFIG_PATH.read_text())
    universe_cfg = universe_cfg or load_universe_config()
    cost = _cost_per_side(cfg)
    gross_target = cfg["portfolio"]["gross_target_pct"]
    per_sym_cap = cfg["portfolio"]["per_symbol_max_pct_nav"]
    top_k = cfg["portfolio"]["top_k_default"]
    bot_k = cfg["portfolio"]["bottom_k_default"]
    daily_loss_pct = cfg["risk"]["daily_loss_flatten_pct"]
    monthly_loss_pct = cfg["risk"]["monthly_loss_pause_pct"]
    monthly_pause_days = int(cfg["risk"]["monthly_pause_days"])
    starting_capital = float(cfg["starting_capital_usd"])

    panel = build_signals(per_symbol_dfs, cfg)
    if panel.empty:
        raise ValueError("empty momentum panel -- no usable symbols provided")
    # union of all dates
    dates = sorted(panel.index.tolist())
    if start is not None:
        dates = [d for d in dates if d >= pd.Timestamp(start).tz_localize("UTC")]
    if end is not None:
        dates = [d for d in dates if d <= pd.Timestamp(end).tz_localize("UTC")]
    if not dates:
        raise ValueError("no dates in panel after applying start/end bounds")

    # State carried across bars.
    positions: Dict[str, float] = {}   # current dollar exposure per symbol
    last_closes: Dict[str, float] = {}  # closes used as entry levels for SHORT PnL
    equity = starting_capital
    equity_curve: List[Tuple[pd.Timestamp, float]] = []
    gross_series: List[Tuple[pd.Timestamp, float]] = []
    turnover_series: List[Tuple[pd.Timestamp, float]] = []
    events: List[RebalanceEvent] = []
    paused_days_remaining = 0
    paused_days_count = 0
    daily_loss_days = 0
    total_turnover = 0.0

    # The first valid rebalance is when the panel has enough history that
    # the 30d return is defined for every symbol.
    min_date = panel.apply(lambda s: s.first_valid_index()).max()
    rebalance_dates = [d for d in dates if d >= min_date]

    for d in rebalance_dates:
        # 1. Mark to market prior positions using yesterday's closes.
        cur_closes = {
            sym: float(per_symbol_dfs[sym].loc[per_symbol_dfs[sym].index == d, "close"].iloc[0])
            for sym in per_symbol_dfs
            if not per_symbol_dfs[sym].loc[per_symbol_dfs[sym].index == d].empty
        }

        # 2. Realized PnL from yesterday's positions into today's close.
        pnl = _realized_pnl(positions, last_closes, cur_closes) if positions else 0.0
        prior_equity = equity
        equity = equity + pnl

        # 3. Daily loss flatten check.
        if daily_loss_breach(prior_equity, equity, daily_loss_pct) and positions:
            positions = {s: 0.0 for s in positions}
            daily_loss_days += 1
            notes = "daily_loss_flatten"
        else:
            notes = ""

        # 4. Monthly pause check.
        # Build a tiny equity series from ``equity_curve`` so far.
        eq_so_far = pd.Series(
            [v for _, v in equity_curve] + [equity],
            index=pd.DatetimeIndex([t for t, _ in equity_curve] + [d], name="openTime"),
        )
        breach, _dd = monthly_pause_active(eq_so_far, d, monthly_loss_pct)
        if breach:
            paused_days_remaining = monthly_pause_days

        if paused_days_remaining > 0:
            paused_days_count += 1
            paused_days_remaining -= 1
            target = PortfolioTarget(asof=d, positions=[], paused=True, pause_reason="monthly_pause")
            notes = (notes + " monthly_pause").strip()
        else:
            # 5. Build new target.
            eligible = eligible_symbols_on(per_symbol_dfs, d, universe_cfg)
            # Restrict ranking to eligible symbols.
            ranking = rank_symbols_on(panel, d)
            ranking = ranking[ranking["symbol"].isin(eligible)]
            ls = select_long_short(ranking, top_k=top_k, bottom_k=bot_k)
            longs = [s for s, side in ls.items() if side == "LONG"]
            shorts = [s for s, side in ls.items() if side == "SHORT"]
            target_positions = equal_weight_allocation(
                longs,
                shorts,
                gross_target_pct=gross_target,
                per_symbol_max_pct_nav=per_sym_cap,
            )
            target = PortfolioTarget(asof=d, positions=target_positions)
            target = enforce_gross_cap(target, gross_target)

        # 6. Translate target to dollar positions at current equity.
        new_positions = _new_positions_from_target(target, equity)
        # Charge round-trip cost on the delta (rebalance turnover).
        delta_cost = _delta_turnover_cost(positions, new_positions, cost)
        equity = equity - delta_cost
        turnover = 0.5 * sum(
            abs(new_positions.get(s, 0.0) - positions.get(s, 0.0))
            for s in set(positions) | set(new_positions)
        ) / max(equity, 1.0)
        total_turnover += turnover

        # 7. Advance state.
        positions = new_positions
        last_closes = cur_closes
        equity_curve.append((d, equity))
        gross_series.append((d, gross_exposure(target)))
        turnover_series.append((d, turnover))
        events.append(
            RebalanceEvent(
                date=d,
                target_positions=list(target.positions),
                gross=gross_exposure(target),
                turnover=turnover,
                notes=notes,
            )
        )

    # Build summary.
    eq_idx = pd.DatetimeIndex([d for d, _ in equity_curve], name="openTime")
    eq = pd.Series([v for _, v in equity_curve], index=eq_idx)
    gr = pd.Series([v for _, v in gross_series], index=eq_idx)
    to = pd.Series([v for _, v in turnover_series], index=eq_idx)

    # Last mark: at the final rebalance date, also realise forward any
    # post-rebalance moves so the equity curve is complete. Since we
    # rebalance every bar in a daily run, the final rebalance's target is
    # in effect at the close of the last bar -- mark-to-market is exact.
    if len(eq) < 2:
        total_return = 0.0
        sharpe = sortino = max_dd = 0.0
    else:
        daily_ret = eq.pct_change().fillna(0.0)
        total_return = float(eq.iloc[-1] / starting_capital - 1.0)
        years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1.0 / 365.25)
        annualized_return = (1.0 + total_return) ** (1.0 / years) - 1.0
        if daily_ret.std() == 0:
            sharpe = 0.0
            sortino = 0.0
        else:
            sharpe = float(daily_ret.mean() / daily_ret.std() * math.sqrt(365))
            downside = daily_ret[daily_ret < 0]
            dstd = downside.std() if len(downside) > 0 else daily_ret.std()
            sortino = float(daily_ret.mean() / dstd * math.sqrt(365)) if dstd and dstd > 0 else 0.0
        rolling_max = eq.cummax()
        drawdown = (eq - rolling_max) / rolling_max
        max_dd = float(drawdown.min())
        # rename local total_return usage above; we want annualized too.
        annualized_return = float(annualized_return)
    n_rebalances = len(equity_curve)
    avg_gross = float(gr.mean()) if not gr.empty else 0.0
    avg_per_bar_turnover = float(to.mean()) if not to.empty else 0.0

    return BacktestResult(
        strategy=cfg.get("strategy", "xs_momentum_rank_1d_20260709"),
        n_rebalances=n_rebalances,
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_sharpe=sharpe,
        annualized_sortino=sortino,
        max_drawdown=max_dd,
        avg_gross=avg_gross,
        total_turnover=total_turnover,
        avg_per_bar_turnover=avg_per_bar_turnover,
        equity_curve=eq,
        gross_series=gr,
        turnover_series=to,
        events=events,
        paused_days=paused_days_count,
        daily_loss_flatten_days=daily_loss_days,
    )


# Expose a JSON-friendly summary builder for the runner.
def result_summary_dict(result: BacktestResult) -> Dict[str, object]:
    return {
        "strategy": result.strategy,
        "n_rebalances": result.n_rebalances,
        "total_return": result.total_return,
        "annualized_return": result.annualized_return,
        "annualized_sharpe": result.annualized_sharpe,
        "annualized_sortino": result.annualized_sortino,
        "max_drawdown": result.max_drawdown,
        "avg_gross": result.avg_gross,
        "total_turnover": result.total_turnover,
        "avg_per_bar_turnover": result.avg_per_bar_turnover,
        "paused_days": result.paused_days,
        "daily_loss_flatten_days": result.daily_loss_flatten_days,
        "equity_curve_start": (
            result.equity_curve.index[0].date().isoformat()
            if not result.equity_curve.empty
            else None
        ),
        "equity_curve_end": (
            result.equity_curve.index[-1].date().isoformat()
            if not result.equity_curve.empty
            else None
        ),
    }