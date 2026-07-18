"""Walk-forward OOS validation for V10 (iter#74, 5m bars).

Splits the 5m frame into ``n_splits`` non-overlapping test windows. Each
test window has its own train window that ENDS at the test start, so the
strategy never sees the test data.

Output: a dict with per-fold in-sample / out-of-sample metrics and the
aggregated mean / median / positive-fold counts.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from strategy import run_backtest as run_strategy_backtest


def _max_drawdown(equity: np.ndarray) -> float:
    if len(equity) < 2:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    dd = (equity - peaks) / np.where(peaks == 0, 1, peaks)
    return float(dd.min())


def walk_forward(
    df: pd.DataFrame,
    *,
    cfg: Dict[str, Any],
    initial_capital: float = 100000.0,
    fee_bps: float = 1.0,
    slippage_bps: float = 1.0,
    position_size_pct: float = 0.95,
    bars_per_year: int = 105120,
    max_hold_bars: int = 288,
    n_splits: int = 5,
    train_pct: float = 0.6,
    min_bars_per_window: int = 2000,
) -> Dict[str, Any]:
    """Run rolling non-overlapping walk-forward.

    The training window is anchored at the start of the test window and
    extends backward ``train_pct * N_total`` bars. The test window is
    one of ``n_splits`` non-overlapping slices of the post-train portion.
    """
    n = len(df)
    train_size = int(n * train_pct)
    available = n - train_size
    if available <= 0:
        return {"n_folds": 0, "folds": [], "oos_sharpe_mean": 0.0}

    # Cap splits so each test window has at least min_bars_per_window bars.
    # 5m bars are tiny (~12/hr), so min_bars_per_window=2000 ≈ 7 days.
    max_splits = max(1, available // max(1, min_bars_per_window))
    n_splits = min(n_splits, max_splits)
    test_size = available // n_splits

    folds: List[Dict[str, Any]] = []
    for k in range(n_splits):
        test_start = train_size + k * test_size
        test_end = test_start + test_size if k < n_splits - 1 else n
        test_end_idx = min(test_end, n - 1)

        train_start = max(0, train_size - max(train_size, min_bars_per_window * 4))
        train_df = df.iloc[train_start:train_size].copy()
        test_df = df.iloc[test_start:test_end].copy()

        if len(train_df) < min_bars_per_window or len(test_df) < min_bars_per_window // 4:
            continue

        is_res = run_strategy_backtest(
            train_df, cfg=cfg,
            initial_capital=initial_capital,
            fee_bps=fee_bps, slippage_bps=slippage_bps,
            position_size_pct=position_size_pct,
            bars_per_year=bars_per_year,
            max_hold_bars=max_hold_bars,
        )
        oos_res = run_strategy_backtest(
            test_df, cfg=cfg,
            initial_capital=initial_capital,
            fee_bps=fee_bps, slippage_bps=slippage_bps,
            position_size_pct=position_size_pct,
            bars_per_year=bars_per_year,
            max_hold_bars=max_hold_bars,
        )

        is_eq = np.asarray(is_res["equity_curve"], dtype=float)
        oos_eq = np.asarray(oos_res["equity_curve"], dtype=float)
        is_rets = np.diff(is_eq) / is_eq[:-1] if len(is_eq) > 1 else np.array([])
        oos_rets = np.diff(oos_eq) / oos_eq[:-1] if len(oos_eq) > 1 else np.array([])

        is_sharpe = (
            float(np.mean(is_rets) / np.std(is_rets, ddof=0) * math.sqrt(bars_per_year))
            if len(is_rets) > 1 and np.std(is_rets, ddof=0) > 0 else 0.0
        )
        oos_sharpe = (
            float(np.mean(oos_rets) / np.std(oos_rets, ddof=0) * math.sqrt(bars_per_year))
            if len(oos_rets) > 1 and np.std(oos_rets, ddof=0) > 0 else 0.0
        )

        folds.append({
            "fold": k,
            "train_window": [str(df.index[train_start]), str(df.index[train_size])],
            "test_window": [str(df.index[test_start]), str(df.index[test_end_idx])],
            "n_train_bars": len(train_df),
            "n_test_bars": len(test_df),
            "in_sample": {
                "sharpe": is_sharpe,
                "total_return_pct": is_res["total_return_pct"],
                "annualised_pct": is_res["annualised_pct"],
                "max_drawdown_pct": _max_drawdown(is_eq),
                "n_trades": is_res["n_trades"],
                "win_rate": is_res["win_rate"],
                "profit_factor": is_res["profit_factor"],
            },
            "out_of_sample": {
                "sharpe": oos_sharpe,
                "total_return_pct": oos_res["total_return_pct"],
                "annualised_pct": oos_res["annualised_pct"],
                "max_drawdown_pct": _max_drawdown(oos_eq),
                "n_trades": oos_res["n_trades"],
                "win_rate": oos_res["win_rate"],
                "profit_factor": oos_res["profit_factor"],
            },
        })

    if not folds:
        return {"n_folds": 0, "folds": [], "oos_sharpe_mean": 0.0}

    oos_sharpes = np.array([f["out_of_sample"]["sharpe"] for f in folds])
    is_sharpes = np.array([f["in_sample"]["sharpe"] for f in folds])
    oos_returns = np.array([f["out_of_sample"]["total_return_pct"] for f in folds])
    summary = {
        "n_folds": len(folds),
        "n_splits_requested": n_splits,
        "train_pct": train_pct,
        "bars_per_year": bars_per_year,
        "is_sharpe_mean": float(is_sharpes.mean()),
        "oos_sharpe_mean": float(oos_sharpes.mean()),
        "oos_sharpe_median": float(np.median(oos_sharpes)),
        "oos_sharpe_min": float(oos_sharpes.min()),
        "oos_return_mean": float(oos_returns.mean()),
        "oos_positive_folds": int(np.sum(oos_returns > 0)),
        "oos_total_trades": int(sum(f["out_of_sample"]["n_trades"] for f in folds)),
        "folds": folds,
    }
    return summary


__all__ = ["walk_forward"]