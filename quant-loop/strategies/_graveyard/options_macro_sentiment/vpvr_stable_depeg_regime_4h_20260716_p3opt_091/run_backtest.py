"""Run a self-contained backtest for vpvr_stable_depeg_regime_4h_20260716.

Generates synthetic 4h OHLCV + depeg premium data, runs the strategy, and writes
results/metrics.json.  This is a B1 indicator-spec sanity run; real data and
full B3 validation should follow.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import run_backtest, VARIANT_KEY


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"


def _make_synthetic_data(n_bars: int = 8000, seed: int = 44) -> pd.DataFrame:
    """Create synthetic 4h BTCUSDT data with occasional depeg events."""
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2023-01-01", periods=n_bars, freq="4h")

    returns = rng.normal(0.0, 0.0015, size=n_bars)
    # Inject mean-reverting shocks.
    for _ in range(int(n_bars / 300)):
        t = rng.integers(300, n_bars - 80)
        returns[t : t + 4] += rng.choice([-1, 1]) * rng.uniform(0.005, 0.010)
        returns[t + 4 : t + 40] -= rng.choice([-1, 1]) * rng.uniform(0.003, 0.007)

    close = 30000.0 * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0005, 0.0040, size=n_bars))
    low = close * (1 - rng.uniform(0.0005, 0.0040, size=n_bars))
    open_ = close * (1 + rng.normal(0.0, 0.0008, size=n_bars))
    volume = rng.lognormal(0.0, 0.5, size=n_bars) * 100.0

    # Premium: mostly zero, occasional spikes to 30-60 bps.
    premium = np.zeros(n_bars)
    for _ in range(int(n_bars / 800)):
        t = rng.integers(500, n_bars - 100)
        premium[t : t + 20] = rng.uniform(0.0015, 0.0040)

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "premium": premium,
    }, index=dt)


def _compute_metrics(result: dict) -> dict:
    equity = result["equity"]
    trades = result["trades"]
    if len(equity) < 2:
        return {"sharpe": 0.0, "ann_return": 0.0, "max_drawdown": 0.0, "profit_factor": 0.0, "n_trades": 0}

    returns = pd.Series(np.diff(equity) / equity[:-1])
    ann_factor = 365.0 * 24.0 * 6.0  # 4h bars

    sharpe = float(returns.mean() / (returns.std(ddof=0) + 1e-12) * np.sqrt(ann_factor))
    ann_return = float((equity[-1] / equity[0]) ** (ann_factor / len(returns)) - 1.0) * 100.0

    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_drawdown = float(np.min(drawdowns)) * 100.0

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
        "status": "PROFITABLE" if sharpe >= 1.0 and ann_return >= 15.0 and max_drawdown > -25.0 and profit_factor > 1.5 else "NOT-PROFITABLE",
        "note": "B1 synthetic sanity run — real validation pending B3 on live data.",
    }


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with open(ROOT / "config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    df = _make_synthetic_data(n_bars=10000, seed=44)
    result = run_backtest(df, cfg)
    metrics = _compute_metrics(result)

    with open(RESULTS_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)

    print(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
