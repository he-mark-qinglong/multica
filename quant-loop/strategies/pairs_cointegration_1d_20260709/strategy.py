"""Pairs-cointegration 1D strategy (B1+B2 scaffold).

B1 owns this file only insofar as it provides a thin harness around
`cointegration.py` (rolling hedge ratio + z-score on the spread). B2 will add
the entry/exit logic and the backtest engine. The function signatures are
fixed so the catalog entry works end-to-end:

    - `cointegration`  : re-export from `cointegration.py` for the catalog surface
    - `build_signals` : OLS beta + z-score on log-spreads (B1 partial; entry_signal
                         always False until B2 adds the threshold logic)
    - `run_backtest`  : scaffold — returns an empty `BacktestResult` (B2/B3 territory)
    - `walk_forward_splits` : copied verbatim from vpvr_reversion_1d_20260621

NOTE on data shape: B1's tests use single-(y,x) column numpy arrays. The real
strategy takes a multi-symbol OHLCV panel. The B2 owner should adapt
`build_signals` to accept the long-form panel and call into `cointegration.py`
per pair. We leave that as a B2 TODO; the B1 path (synthetic single pair) is
fully exercised by `tests/test_cointegration.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

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
# Build signals (B1 partial; B2 must add the entry_signal column)
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
        daily bars. (B2 will adapt the wrapper to the multi-symbol panel format.)
    cfg : dict
        Strategy config; reads `cointegration.hedge_window_days`,
        `cointegration.adf_maxlag`, `signal.zscore_window_days`,
        `signal.entry_threshold`, `signal.exit_threshold`.

    Returns
    -------
    pd.DataFrame indexed the same as `prices_a` with columns:
        `alpha`, `beta`, `r_squared`       -- rolling OLS on log(A) vs log(B)
        `spread`                           -- log(A) - alpha - beta * log(B)
        `zscore`, `spread_mean`, `spread_std`
        `entry_signal` (always False at B1; B2 fills the threshold logic)
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

    # Phase 1: rolling hedge ratio.
    hedge_df = rolling_hedge_ratio(
        pd.Series(log_a, index=common),
        pd.Series(log_b, index=common),
        window=hedge_window,
    )
    hedge_df = hedge_df.reindex(common)

    # Phase 2: spread + z-score. We compute them row-by-row using the currently
    # active hedge estimate (no look-ahead) — equivalent to a vectorized
    # assignment but easier to reason about for B1.
    spread = np.full(len(common), np.nan)
    for i in range(len(common)):
        beta = hedge_df["beta"].iloc[i]
        alpha = hedge_df["alpha"].iloc[i]
        if np.isfinite(beta) and np.isfinite(alpha):
            spread[i] = log_a[i] - alpha - beta * log_b[i]

    spread_s = pd.Series(spread, index=common)
    zscore_df = rolling_zscore(spread_s, window=z_window)

    result = pd.DataFrame(
        {
            "alpha": hedge_df["alpha"],
            "beta": hedge_df["beta"],
            "r_squared": hedge_df["r_squared"],
            "spread": spread_s,
            "spread_mean": zscore_df["mean"],
            "spread_std": zscore_df["std"],
            "zscore": zscore_df["zscore"],
            # B1 emits no entry signal — the B2 owner sets this from
            # `entry_threshold` / `exit_threshold` plus the cointegration-break
            # guard (`stop_sigma_threshold`).
            "entry_signal": np.zeros(len(common), dtype=bool),
        },
        index=common,
    )
    return result


# ---------------------------------------------------------------------------
# Backtest (B2/B3 scaffold — placeholder that returns an empty result so the
# catalog entry resolves cleanly).
# ---------------------------------------------------------------------------
def run_backtest(
    prices_a: pd.DataFrame,
    prices_b: pd.DataFrame,
    cfg: dict,
    **kwargs,
) -> BacktestResult:
    """B2/B3 scaffold. Real mean-reversion backtest lands in B2.

    Returns a zero-trade result so the catalog surface resolves. The
    `BacktestResult` shape matches `vpvr_reversion_1d_20260621` so B2 can swap
    in the real engine without touching the dataclass.
    """
    ticker = cfg.get("_ticker", "PAIR")
    starting_cap = cfg["starting_capital_usd"]
    return BacktestResult(
        ticker=ticker,
        n_trades=0,
        win_rate=0.0,
        profit_factor=0.0,
        avg_holding_bars=0.0,
        total_return=0.0,
        annualized_sharpe=0.0,
        annualized_sortino=0.0,
        max_drawdown=0.0,
        turnover_per_year=0.0,
        equity_curve=pd.Series([starting_cap]),
        trades=[],
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
    "Trade",
    "build_signals",
    "compute_spread",
    "engle_granger_test",
    "half_life",
    "ols_hedge_ratio",
    "rolling_hedge_ratio",
    "rolling_zscore",
    "run_backtest",
    "walk_forward_splits",
]