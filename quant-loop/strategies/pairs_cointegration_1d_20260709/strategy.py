"""Pairs-cointegration 1D strategy (B1+B2 production).

B1 owns `cointegration.py` (OLS + EG + rolling hedge + z-score). This module
implements the B2 layer on top:

    - `build_signals(prices_a, prices_b, cfg)`
        Rolling hedge + z-score on log-spreads, plus entry/exit/break columns
        derived from the configured thresholds.

    - `simulate_pair_trades(prices_a, prices_b, cfg, pair_key, portfolio_state)`
        Walk the daily bars, generate fills against the per-pair signal df,
        mutate `PortfolioState` (entry/exit/break/pause), return a
        `PairResult` for persistence.

    - `run_backtest(prices_a, prices_b, cfg)`
        Thin wrapper kept for the catalog surface: returns a single-pair
        `BacktestResult` in the shape `vpvr_reversion_1d_20260621` produces.

    - `walk_forward_splits(dates, cfg)`  — unchanged from B1.

This file is the single source of truth for *signal* logic; the backtest
runner (`run_backtest.py`) only orchestrates the per-pair loop, calls
`simulate_pair_trades`, and persists results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Re-export the B1 primitives so the strategy has a single import surface.
from cointegration import (
    EGTestResult,
    HedgeRatio,
    compute_spread,
    engle_granger_test,
    half_life,
    ols_hedge_ratio,
    rolling_hedge_ratio,
    rolling_zscore,
)

CONFIG_PATH = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------------------
# Result dataclasses — match the parent strategy's surface so variant scripts
# can re-use the same `_summarize` helpers from `vpvr_reversion_1d_20260621`.
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    reason: str
    pnl: float
    pnl_pct: float
    bars_held: int


@dataclass
class BacktestResult:
    ticker: str
    n_trades: int
    win_rate: float
    profit_factor: float
    avg_holding_bars: float
    total_return: float
    annualized_sharpe: float
    annualized_sortino: float
    max_drawdown: float
    turnover_per_year: float
    equity_curve: pd.Series = field(default_factory=pd.Series)
    trades: List[Trade] = field(default_factory=list)


# ---------------------------------------------------------------------------
# B2 trade ledger — also re-used by run_backtest.py for persistence.
# ---------------------------------------------------------------------------
@dataclass
class PairTrade:
    pair_key: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    side: str          # "long_spread" or "short_spread"
    z_entry: float
    z_exit: float
    pnl_pct: float
    pnl_usd: float
    reason: str        # "mean_revert" | "coint_break" | "monthly_kill"


@dataclass
class PairResult:
    pair_key: str
    n_trades: int
    win_rate: float
    total_pnl_usd: float
    total_pnl_pct: float
    avg_bars_held: float
    trades: List[PairTrade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    hedge_ratio_stability: pd.DataFrame = field(default_factory=pd.DataFrame)
    exits: List[Dict] = field(default_factory=list)  # per-fill ledger for portfolio aggregation


# ---------------------------------------------------------------------------
# Build signals (B1: hedge + z-score; B2: entry/exit/break columns)
# ---------------------------------------------------------------------------
def build_signals(
    prices_a: pd.DataFrame,
    prices_b: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """Compute rolling hedge ratio + spread + z-score for a single (A, B) pair.

    Parameters
    ----------
    prices_a, prices_b : pd.DataFrame
        Each must have a `close` column and a DatetimeIndex aligned to the same
        daily bars.
    cfg : dict
        Strategy config; reads `cointegration.hedge_window_days`,
        `cointegration.adf_maxlag`, `signal.zscore_window_days`,
        `signal.entry_threshold`, `signal.exit_threshold`,
        `signal.stop_sigma_threshold`.

    Returns
    -------
    pd.DataFrame indexed the same as `prices_a` with columns:
        `alpha`, `beta`, `r_squared`           -- rolling OLS on log(A) vs log(B)
        `spread`                               -- log(A) - alpha - beta * log(B)
        `zscore`, `spread_mean`, `spread_std`
        `entry_long_spread`  (bool)            -- z < -entry_threshold
        `entry_short_spread` (bool)            -- z > +entry_threshold
        `exit_signal`        (bool)            -- |z| < exit_threshold
        `coint_break`        (bool)            -- |Δspread| > stop_sigma * spread_std
    """
    # Use only the intersection of dates so a misalignment in either series
    # doesn't leak rows with NaN prices into the regression.
    common = prices_a.index.intersection(prices_b.index)
    a = prices_a.loc[common]
    b = prices_b.loc[common]

    log_a = np.log(a["close"].to_numpy(dtype=float))
    log_b = np.log(b["close"].to_numpy(dtype=float))

    hedge_window = cfg["cointegration"]["hedge_window_days"]
    z_window = cfg["signal"]["zscore_window_days"]
    adf_lag = cfg["cointegration"].get("adf_maxlag", 1)
    entry_thr = float(cfg["signal"]["entry_threshold"])
    exit_thr = float(cfg["signal"]["exit_threshold"])
    stop_sig = float(cfg["signal"]["stop_sigma_threshold"])

    # Phase 1: rolling hedge ratio.
    hedge_df = rolling_hedge_ratio(
        pd.Series(log_a, index=common),
        pd.Series(log_b, index=common),
        window=hedge_window,
    )
    hedge_df = hedge_df.reindex(common)

    # Phase 2: spread + z-score. We compute them row-by-row using the currently
    # active hedge estimate (no look-ahead) — equivalent to a vectorized
    # assignment but easier to reason about.
    spread = np.full(len(common), np.nan)
    for i in range(len(common)):
        beta = hedge_df["beta"].iloc[i]
        alpha = hedge_df["alpha"].iloc[i]
        if np.isfinite(beta) and np.isfinite(alpha):
            spread[i] = log_a[i] - alpha - beta * log_b[i]

    spread_s = pd.Series(spread, index=common)
    zscore_df = rolling_zscore(spread_s, window=z_window)

    # Phase 3: per-bar entry / exit / break columns.
    z = zscore_df["zscore"]
    std = zscore_df["std"]
    entry_long = (z < -entry_thr).fillna(False).to_numpy()
    entry_short = (z > entry_thr).fillna(False).to_numpy()
    exit_sig = (z.abs() < exit_thr).fillna(False).to_numpy()

    # Cointegration break: today's |Δspread| > stop_sigma * spread_std.
    # Compare against previous bar's spread (NaN-safe via shift).
    spread_diff = spread_s.diff().abs()
    coint_break = (spread_diff > (stop_sig * std)).fillna(False).to_numpy()

    result = pd.DataFrame(
        {
            "alpha": hedge_df["alpha"],
            "beta": hedge_df["beta"],
            "r_squared": hedge_df["r_squared"],
            "spread": spread_s,
            "spread_mean": zscore_df["mean"],
            "spread_std": zscore_df["std"],
            "zscore": zscore_df["zscore"],
            "entry_long_spread": entry_long,
            "entry_short_spread": entry_short,
            "exit_signal": exit_sig,
            "coint_break": coint_break,
        },
        index=common,
    )
    return result


# ---------------------------------------------------------------------------
# Single-pair trade simulation — B2 owns this.
# ---------------------------------------------------------------------------
def simulate_pair_trades(
    prices_a: pd.DataFrame,
    prices_b: pd.DataFrame,
    cfg: dict,
    pair_key: str,
    portfolio_state: "object",  # PortfolioState; kept loose to avoid import cycle
) -> PairResult:
    """Walk the daily bars and emit PairTrades against the per-pair signals.

    Trade triggers (see SPEC.md "Signal" section):
        z >  +entry_threshold -> short_spread entry
        z <  -entry_threshold -> long_spread  entry
        |z| < exit_threshold  -> close any open position
        |Δspread| > stop_sigma * spread_std  -> close (coint break)

    Position model (per spec):
        long_spread  : long 1 unit of A, short beta units of B
        short_spread : short 1 unit of A, long beta units of B

    PnL model: pnl_pct = (spread_t - spread_{entry}) * sign, where sign=+1
    for long_spread and -1 for short_spread. The 1-unit-of-A normalization
    is then sized at runtime by `portfolio.apply_pair_constraints`.

    The function mutates `portfolio_state` as it goes: each new entry is
    rejected if the pair or portfolio is paused or the active-pair cap is
    hit; each exit (mean-revert or coint-break) records a PairTrade. The
    portfolio also enforces monthly max-loss pause, which is checked in
    `portfolio_state.update_monthly_pnl` after every exit.

    Returns a PairResult with the trade ledger, equity curve (mark-to-
    market at exit dates), and hedge-ratio stability summary.
    """
    starting_cap = float(cfg["starting_capital_usd"])
    leg_pct = float(cfg["position_sizing"]["leg_pct_per_pair"])
    fee = float(cfg["fees_bps_per_side"]) / 10000.0
    slip = float(cfg["slippage_bps_per_side"]) / 10000.0
    cost_per_side = fee + slip
    leg_notional = starting_cap * leg_pct

    sig = build_signals(prices_a, prices_b, cfg)

    # Hedge ratio stability: rolling beta std/mean over the full sample.
    valid_beta = sig["beta"].dropna()
    hedge_summary = pd.DataFrame(
        {
            "beta_mean": [float(valid_beta.mean()) if len(valid_beta) else float("nan")],
            "beta_std": [float(valid_beta.std()) if len(valid_beta) else float("nan")],
            "beta_min": [float(valid_beta.min()) if len(valid_beta) else float("nan")],
            "beta_max": [float(valid_beta.max()) if len(valid_beta) else float("nan")],
            "n_obs": [int(valid_beta.notna().sum())],
        },
        index=[pair_key],
    )

    trades: List[PairTrade] = []
    in_pos = False
    side: str = ""
    entry_date: Optional[pd.Timestamp] = None
    entry_z = 0.0
    entry_spread = 0.0

    for dt, row in sig.iterrows():
        z = row.get("zscore")
        spread = row.get("spread")
        if not (np.isfinite(z) and np.isfinite(spread)):
            continue

        if in_pos:
            # Cointegration-break guard: today vs yesterday's spread, in
            # units of today's spread_std.
            if bool(row.get("coint_break", False)):
                pnl_pct = (spread - entry_spread) * (
                    1.0 if side == "long_spread" else -1.0
                )
                pnl_pct -= 2.0 * cost_per_side
                trades.append(
                    PairTrade(
                        pair_key=pair_key,
                        entry_date=entry_date,
                        exit_date=dt,
                        side=side,
                        z_entry=entry_z,
                        z_exit=float(z),
                        pnl_pct=float(pnl_pct),
                        pnl_usd=float(pnl_pct) * leg_notional,
                        reason="coint_break",
                    )
                )
                # Update portfolio state machine with the exit fill.
                portfolio_state.record_exit(
                    pair_key=pair_key,
                    exit_date=dt,
                    pnl_pct=float(pnl_pct),
                    reason="coint_break",
                )
                in_pos = False
            elif bool(row.get("exit_signal", False)):
                pnl_pct = (spread - entry_spread) * (
                    1.0 if side == "long_spread" else -1.0
                )
                pnl_pct -= 2.0 * cost_per_side
                trades.append(
                    PairTrade(
                        pair_key=pair_key,
                        entry_date=entry_date,
                        exit_date=dt,
                        side=side,
                        z_entry=entry_z,
                        z_exit=float(z),
                        pnl_pct=float(pnl_pct),
                        pnl_usd=float(pnl_pct) * leg_notional,
                        reason="mean_revert",
                    )
                )
                portfolio_state.record_exit(
                    pair_key=pair_key,
                    exit_date=dt,
                    pnl_pct=float(pnl_pct),
                    reason="mean_revert",
                )
                in_pos = False
        else:
            # Try to enter: check pair/portfolio pause + active-pair cap.
            desired_side: Optional[str] = None
            if bool(row.get("entry_short_spread", False)):
                desired_side = "short_spread"
            elif bool(row.get("entry_long_spread", False)):
                desired_side = "long_spread"

            if desired_side is not None and portfolio_state.allow_entry(
                pair_key=pair_key, entry_date=dt
            ):
                in_pos = True
                side = desired_side
                entry_date = dt
                entry_z = float(z)
                entry_spread = float(spread)
                portfolio_state.record_entry(
                    pair_key=pair_key,
                    entry_date=dt,
                    side=side,
                    alpha=float(row.get("alpha", float("nan"))),
                    beta=float(row.get("beta", float("nan"))),
                )

    n = len(trades)
    if n:
        wins = [t for t in trades if t.pnl_usd > 0]
        win_rate = len(wins) / n
        total_pnl_usd = sum(t.pnl_usd for t in trades)
        total_pnl_pct = total_pnl_usd / starting_cap
        avg_bars = float(np.mean([
            (t.exit_date - t.entry_date).days for t in trades
        ]))
    else:
        win_rate = 0.0
        total_pnl_usd = 0.0
        total_pnl_pct = 0.0
        avg_bars = 0.0

    # Pair-level equity curve: mark-to-market at exit dates.
    eq = pd.Series([starting_cap], index=[sig.index[0]])
    if trades:
        running = starting_cap
        for t in trades:
            running = running + t.pnl_usd
            eq = pd.concat([eq, pd.Series([running], index=[t.exit_date])])
    eq = eq[~eq.index.duplicated(keep="last")].sort_index()

    return PairResult(
        pair_key=pair_key,
        n_trades=n,
        win_rate=win_rate,
        total_pnl_usd=total_pnl_usd,
        total_pnl_pct=total_pnl_pct,
        avg_bars_held=avg_bars,
        trades=trades,
        equity_curve=eq,
        hedge_ratio_stability=hedge_summary,
    )


# ---------------------------------------------------------------------------
# Catalog surface: `run_backtest` (single-pair, no portfolio state machine).
# ---------------------------------------------------------------------------
def run_backtest(
    prices_a: pd.DataFrame,
    prices_b: pd.DataFrame,
    cfg: dict,
    **kwargs,
) -> BacktestResult:
    """Catalog-surface backtest: single pair, no portfolio state machine.

    The full multi-pair orchestration (with portfolio-level pause + cap) lives
    in `run_backtest.run_multi_pair_backtest`. This wrapper exists so the
    strategy can be poked at directly from a notebook without the portfolio
    scaffolding getting in the way.
    """
    # Lazy import to avoid pulling portfolio.py when the caller only needs
    # build_signals.
    from portfolio import PortfolioState

    ticker = cfg.get("_ticker", "PAIR")
    starting_cap = float(cfg["starting_capital_usd"])
    state = PortfolioState(starting_capital_usd=starting_cap, cfg=cfg)
    res = simulate_pair_trades(prices_a, prices_b, cfg, ticker, state)

    n = res.n_trades
    pnls = np.array([t.pnl_pct for t in res.trades]) if res.trades else np.array([])
    wins = pnls[pnls > 0] if pnls.size else np.array([])
    losses = pnls[pnls <= 0] if pnls.size else np.array([])
    profit_factor = float(wins.sum() / -losses.sum()) if losses.size and -losses.sum() > 0 else float("inf")
    if not np.isfinite(profit_factor):
        profit_factor = 0.0
    avg_hold = res.avg_bars_held
    total_return = res.total_pnl_pct

    # Sharpe/Sortino on per-trade pct returns. Annualize by sqrt(252) on the
    # assumption that 1 trade/bar -> 252 trades/year; for crypto that's
    # 1 trade/day -> a 1-bar holding period implies a similar rate. Treat
    # as nominal-only — B4 owns the rigorous market-neutrality verification.
    if pnls.size > 1:
        ann_factor = float(np.sqrt(252.0))
        sharpe = float(pnls.mean() / (pnls.std(ddof=1) + 1e-12) * ann_factor)
        downside = pnls[pnls < 0]
        sortino = float(pnls.mean() / (downside.std(ddof=1) + 1e-12) * ann_factor) if downside.size > 1 else 0.0
    else:
        sharpe = 0.0
        sortino = 0.0

    # Max drawdown on the equity curve.
    eq = res.equity_curve.to_numpy()
    if eq.size > 1:
        peaks = np.maximum.accumulate(eq)
        dd = (eq - peaks) / peaks
        max_dd = float(dd.min())
    else:
        max_dd = 0.0

    # Turnover per year: 2 legs per round-trip * n_trades / years_in_sample.
    years = (prices_a.index[-1] - prices_a.index[0]).days / 365.25
    turnover = float(2.0 * n / years) if years > 0 else 0.0

    return BacktestResult(
        ticker=ticker,
        n_trades=n,
        win_rate=res.win_rate,
        profit_factor=profit_factor,
        avg_holding_bars=avg_hold,
        total_return=total_return,
        annualized_sharpe=sharpe,
        annualized_sortino=sortino,
        max_drawdown=max_dd,
        turnover_per_year=turnover,
        equity_curve=res.equity_curve,
        trades=[
            Trade(
                ticker=ticker,
                entry_date=t.entry_date,
                entry_price=float(t.entry_date.value),  # placeholder for catalog
                exit_date=t.exit_date,
                exit_price=float(t.exit_date.value),
                reason=t.reason,
                pnl=t.pnl_usd,
                pnl_pct=t.pnl_pct,
                bars_held=(t.exit_date - t.entry_date).days,
            )
            for t in res.trades
        ],
    )


# ---------------------------------------------------------------------------
# Walk-forward splits (same convention as vpvr_reversion_1d_20260621)
# ---------------------------------------------------------------------------
def walk_forward_splits(
    dates: pd.DatetimeIndex,
    cfg: dict,
) -> List[Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    train = cfg["walk_forward"]["train_days"]
    test = cfg["walk_forward"]["test_days"]
    step = cfg["walk_forward"]["step_days"]
    start = dates[0]
    end = dates[-1]
    splits: List[Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    cursor = start
    while True:
        tr_start = cursor
        tr_end = tr_start + pd.Timedelta(days=train)
        te_start = tr_end
        te_end = te_start + pd.Timedelta(days=test)
        if te_end > end:
            break
        splits.append((tr_start, tr_end, te_start, te_end))
        cursor = cursor + pd.Timedelta(days=step)
    return splits


# ---------------------------------------------------------------------------
# Public catalog surface
# ---------------------------------------------------------------------------
__all__ = [
    "BacktestResult",
    "EGTestResult",
    "HedgeRatio",
    "PairResult",
    "PairTrade",
    "Trade",
    "build_signals",
    "compute_spread",
    "engle_granger_test",
    "half_life",
    "ols_hedge_ratio",
    "rolling_hedge_ratio",
    "rolling_zscore",
    "run_backtest",
    "simulate_pair_trades",
    "walk_forward_splits",
]
