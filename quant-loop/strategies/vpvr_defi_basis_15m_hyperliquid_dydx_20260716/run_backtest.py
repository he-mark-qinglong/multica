"""Run a self-contained backtest for vpvr_defi_basis_15m_hyperliquid_dydx_20260716.

Generates synthetic 15m OHLCV + DeFi basis data, runs the strategy, and writes
results/metrics.json.  This is a B1 indicator-spec sanity run; real data and
full B3 validation should follow.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import run_backtest, VARIANT_KEY


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"


def _make_synthetic_data(n_bars: int = 10000, seed: int = 42) -> pd.DataFrame:
    """Create synthetic 15m BTCUSDT data with embedded VPVR + basis mean-reversion."""
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2023-01-01", periods=n_bars, freq="15min")

    # Random walk price with occasional mean-reversion around a slow POC.
    returns = rng.normal(0.0, 0.0005, size=n_bars)
    # Inject mean-reverting shocks so VPVR reversion has edge.
    for _ in range(int(n_bars / 200)):
        t = rng.integers(200, n_bars - 100)
        returns[t : t + 5] += rng.choice([-1, 1]) * rng.uniform(0.003, 0.006)
        returns[t + 5 : t + 25] -= rng.choice([-1, 1]) * rng.uniform(0.002, 0.004)

    close = 30000.0 * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0002, 0.0015, size=n_bars))
    low = close * (1 - rng.uniform(0.0002, 0.0015, size=n_bars))
    open_ = close * (1 + rng.normal(0.0, 0.0003, size=n_bars))
    volume = rng.lognormal(0.0, 0.5, size=n_bars) * 100.0

    # Synthetic DeFi-CEX basis: mean-reverting around 0 with occasional extremes.
    basis = rng.normal(0.0, 0.0003, size=n_bars)
    for _ in range(int(n_bars / 300)):
        t = rng.integers(300, n_bars - 50)
        basis[t : t + 10] += rng.choice([-1, 1]) * rng.uniform(0.0015, 0.0030)

    df = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "basis": basis,
    }, index=dt)
    df.index.name = "ts"
    return df


def _annual_factor_from_dt(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1.0
    delta = (index[-1] - index[0]).total_seconds() / (len(index) - 1)
    return (365.0 * 24.0 * 3600.0) / delta


def _compute_metrics(result: dict) -> dict:
    equity = result["equity"]
    trades = result["trades"]
    if len(equity) < 2:
        return {"sharpe": 0.0, "ann_return": 0.0, "max_drawdown": 0.0, "profit_factor": 0.0, "n_trades": 0}

    returns = pd.Series(np.diff(equity) / equity[:-1])
    ann_factor = _annual_factor_from_dt(pd.to_datetime([result["span_start"], result["span_end"]]))
    # Use observed bar frequency if more than 2 points.
    ann_factor = 365.0 * 24.0 * 4.0  # 15m bars

    sharpe = float(returns.mean() / (returns.std(ddof=0) + 1e-12) * np.sqrt(ann_factor))
    ann_return = float((equity[-1] / equity[0]) ** (ann_factor / len(returns)) - 1.0) * 100.0

    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_drawdown = float(np.min(drawdowns)) * 100.0

    gross_pnl = sum(t["pnl_pct"] for t in trades)
    gross_profit = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0))
    profit_factor = float(gross_profit / (gross_loss + 1e-12))

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    win_rate = float(wins / len(trades)) if trades else 0.0

    return {
        "variant_key": result["variant_key"],
        "iteration": result["iteration"],
        "symbol": result["symbol"],
        "n_bars": result["n_bars"],
        "span_start": result["span_start"],
        "span_end": result["span_end"],
        "n_trades": len(trades),
        "win_rate": round(win_rate, 4),
        "sharpe": round(sharpe, 4),
        "ann_return_pct": round(ann_return, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "profit_factor": round(profit_factor, 4),
        "gross_pnl_pct": round(gross_pnl, 4),
        "status": "PROFITABLE" if sharpe >= 1.0 and ann_return >= 15.0 and max_drawdown > -25.0 and profit_factor > 1.5 else "NOT-PROFITABLE",
        "note": "B1 synthetic sanity run — real validation pending B3 on live data.",
    }


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with open(ROOT / "config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    df = _make_synthetic_data(n_bars=12000, seed=42)
    result = run_backtest(df, cfg)
    metrics = _compute_metrics(result)

    with open(RESULTS_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)

    print(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
