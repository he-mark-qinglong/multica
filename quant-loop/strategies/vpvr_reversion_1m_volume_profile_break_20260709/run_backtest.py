"""B3 backtest for vpvr_reversion_1m_volume_profile_break_20260709.

Loads the real BTCUSDT 1m parquet shipped with the strategy, runs the
self-contained backtest engine in ``strategy.py``, and writes:

  - results/metrics.json     — campaign summary (Sharpe, ann_return, ...)
  - results/summary.json     — issue-handoff summary (criteria, blockers)
  - results/trades_*.csv     — per-trade ledger (long + short ledgers)
  - results/equity_curve.csv — bar-by-bar equity for plotting

Spec ref: SPEC.md / Iter#69 V5.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import run_backtest, VARIANT_KEY

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
DATA_DIR = ROOT / "data"


# ---------------------------------------------------------------------------
# Data loader.
# ---------------------------------------------------------------------------
def load_data() -> pd.DataFrame:
    """Load BTCUSDT 1m parquet shipped with the strategy."""
    cfg_path = ROOT / "config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    parquet_name = cfg.get("data", {}).get("parquet", "data/fapi_BTCUSDT__1m.parquet")
    path = ROOT / parquet_name
    if not path.exists():
        sys.exit(f"missing data file: {path}")
    df = pd.read_parquet(path)
    if df.index.name != "ts" and "openTime" in df.columns:
        df = df.set_index("openTime")
    df.index.name = "ts"
    needed = {"open", "high", "low", "close", "volume"}
    missing = needed - set(df.columns)
    if missing:
        sys.exit(f"parquet missing required columns: {sorted(missing)}")
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


# ---------------------------------------------------------------------------
# Metrics.
# ---------------------------------------------------------------------------
def compute_metrics(result: dict, cfg: dict) -> dict:
    equity = np.asarray(result["equity"], dtype=np.float64)
    trades = result["trades"]
    if len(equity) < 2:
        return _empty_metrics(result, cfg)

    returns = pd.Series(np.diff(equity) / equity[:-1])
    # 1m bars, 525,600 bars per non-leap year. Use actual sample count.
    ann_factor = 365.0 * 24.0 * 60.0

    sharpe = float(
        returns.mean() / (returns.std(ddof=0) + 1e-12) * np.sqrt(ann_factor)
    )
    # Geometric annualized return over the actual span length.
    elapsed_min = len(returns)
    if elapsed_min <= 0:
        ann_return = 0.0
    else:
        ann_return = (
            float((equity[-1] / equity[0]) ** (ann_factor / elapsed_min) - 1.0)
            * 100.0
        )

    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_drawdown = float(np.min(drawdowns)) * 100.0

    gross_profit = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0))
    profit_factor = float(gross_profit / (gross_loss + 1e-12))

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    losses = sum(1 for t in trades if t["pnl_pct"] <= 0)
    win_rate = float(wins / len(trades)) if trades else 0.0

    # Campaign pass/fail.
    criteria = {
        "sharpe": {"min": 1.0, "actual": round(sharpe, 4),
                    "pass": sharpe >= 1.0},
        "ann_return_pct": {"min": 15.0, "actual": round(ann_return, 4),
                            "pass": ann_return >= 15.0},
        "max_drawdown_pct": {"min": -25.0, "actual": round(max_drawdown, 4),
                              "pass": max_drawdown > -25.0},
        "profit_factor": {"min": 1.5, "actual": round(profit_factor, 4),
                           "pass": profit_factor > 1.5},
        "n_trades": {"min": 200, "actual": len(trades),
                     "pass": len(trades) >= 200},
    }
    criteria_pass = all(c["pass"] for c in criteria.values())

    return {
        "variant_key": result["variant_key"],
        "iteration": result["iteration"],
        "version": cfg.get("version", "V?"),
        "symbol": result["symbol"],
        "n_bars": result["n_bars"],
        "span_start": result["span_start"],
        "span_end": result["span_end"],
        "starting_capital_usd": cfg["starting_capital_usd"],
        "ending_capital_usd": float(equity[-1]),
        "n_trades": len(trades),
        "n_wins": wins,
        "n_losses": losses,
        "win_rate": round(win_rate, 4),
        "sharpe": round(sharpe, 4),
        "ann_return_pct": round(ann_return, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "profit_factor": round(profit_factor, 4),
        "criteria": criteria,
        "status": "PROFITABLE" if criteria_pass else "NOT-PROFITABLE",
        "data_source": cfg.get("data", {}).get("parquet", "data/fapi_BTCUSDT__1m.parquet"),
        "note": "B3 run on real BTCUSDT 1m Binance USD-M data.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _empty_metrics(result: dict, cfg: dict) -> dict:
    return {
        "variant_key": result["variant_key"],
        "iteration": result["iteration"],
        "version": cfg.get("version", "V?"),
        "symbol": result["symbol"],
        "n_bars": result["n_bars"],
        "n_trades": 0,
        "win_rate": 0.0,
        "sharpe": 0.0,
        "ann_return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "profit_factor": 0.0,
        "criteria": {
            "sharpe": {"min": 1.0, "actual": 0.0, "pass": False},
            "ann_return_pct": {"min": 15.0, "actual": 0.0, "pass": False},
            "max_drawdown_pct": {"min": -25.0, "actual": 0.0, "pass": False},
            "profit_factor": {"min": 1.5, "actual": 0.0, "pass": False},
            "n_trades": {"min": 200, "actual": 0, "pass": False},
        },
        "status": "NO-TRADES",
        "note": "Backtest produced zero trades — check data, params, or signal logic.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Trade ledger and equity curve writers.
# ---------------------------------------------------------------------------
def write_trades_csv(trades: list, path: Path) -> int:
    if not trades:
        path.write_text("entry_ts,exit_ts,direction,entry_price,exit_price,"
                         "pnl_pct,bars_held,exit_reason,break_state_at_entry,"
                         "vah_at_entry,val_at_entry,poc_distance_atr_at_entry\n")
        return 0
    fieldnames = list(trades[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in trades:
            writer.writerow(t)
    return len(trades)


def write_equity_curve(equity: np.ndarray, index: pd.DatetimeIndex, path: Path) -> int:
    rows = len(equity)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "equity_usd"])
        for ts, eq in zip(index, equity):
            writer.writerow([str(ts), float(eq)])
    return rows


def build_summary(metrics: dict, cfg: dict) -> dict:
    """Compact handoff summary (issue-friendly, not full metrics)."""
    return {
        "variant_key": metrics["variant_key"],
        "iteration": metrics["iteration"],
        "version": metrics.get("version"),
        "status": metrics["status"],
        "criteria_pass": all(c["pass"] for c in metrics.get("criteria", {}).values()),
        "criteria": metrics.get("criteria", {}),
        "headline": {
            "sharpe": metrics["sharpe"],
            "ann_return_pct": metrics["ann_return_pct"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "profit_factor": metrics["profit_factor"],
            "n_trades": metrics["n_trades"],
            "win_rate": metrics.get("win_rate", 0.0),
        },
        "data": {
            "source": metrics.get("data_source", "data/fapi_BTCUSDT__1m.parquet"),
            "n_bars": metrics["n_bars"],
            "span": [metrics["span_start"], metrics["span_end"]],
        },
        "campaign_align": "VPVR Campaign — Sharpe≥1.0, ann≥15%",
        "spec_ref": "SMA-31410 V5 (iter#69)",
    }


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ROOT / "config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    df = load_data()
    print(f"loaded {len(df):,} bars; range {df.index[0]} → {df.index[-1]}")

    result = run_backtest(df, cfg)
    metrics = compute_metrics(result, cfg)

    # Issue-handoff summary.
    summary = build_summary(metrics, cfg)

    with open(RESULTS_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)
    with open(RESULTS_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    # Trade ledgers — split by direction per the issue contract.
    long_trades = [t for t in result["trades"] if t["direction"] == "long"]
    short_trades = [t for t in result["trades"] if t["direction"] == "short"]
    write_trades_csv(long_trades, RESULTS_DIR / "trades_long.csv")
    write_trades_csv(short_trades, RESULTS_DIR / "trades_short.csv")

    # Equity curve (bar-by-bar).
    write_equity_curve(result["equity"], df.index, RESULTS_DIR / "equity_curve.csv")

    print(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
