"""Run a self-contained backtest for vpvr_macro_calendar_4h_20260715.

B3-spec implementation: loads the real BTCUSDT 4h OHLCV from the canonical
30m parquet source, runs the strategy, then writes the canonical evidence
set:

  - results/metrics.json   (Sharpe, Sortino, max DD, annualized return, win-rate, profit-factor)
  - results/summary.json   (per-symbol + portfolio summary)
  - results/trades_<TF>_<SYM>.csv  (>= 4 rows for display-engine variant detection)
  - results/equity_<SYM>.csv       (per-symbol equity curve)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import run_backtest, VARIANT_KEY
from data_loader import load_btcusdt_4h


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
TF = "4h"
SYMBOL_DEFAULT = "BTCUSDT"


def _ann_factor_for_tf(tf: str) -> float:
    bars_per_day = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24, "4h": 6, "8h": 3, "1d": 1}
    return 365.0 * bars_per_day[tf]


def _compute_metrics(result: dict, tf: str, starting_capital: float) -> dict:
    equity = np.asarray(result["equity"], dtype=np.float64)
    trades = result["trades"]
    if len(equity) < 2:
        return {
            "variant_key": result["variant_key"],
            "iteration": result["iteration"],
            "symbol": result["symbol"],
            "timeframe": tf,
            "n_bars": result["n_bars"],
            "span_start": result["span_start"],
            "span_end": result["span_end"],
            "n_trades": 0,
            "win_rate": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "ann_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "status": "FAIL",
            "note": "Insufficient equity points.",
        }

    rets = pd.Series(np.diff(equity) / equity[:-1])
    ann_factor = _ann_factor_for_tf(tf)

    sharpe = float(rets.mean() / (rets.std(ddof=0) + 1e-12) * np.sqrt(ann_factor))
    downside = rets.copy()
    downside[downside > 0] = 0.0
    sortino = float(rets.mean() / (downside.std(ddof=0) + 1e-12) * np.sqrt(ann_factor))

    total_return = float((equity[-1] / equity[0]) - 1.0)
    years = max(len(rets) / ann_factor, 1e-9)
    ann_return_pct = ((1.0 + total_return) ** (1.0 / years) - 1.0) * 100.0

    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_drawdown_pct = float(np.min(drawdowns)) * 100.0

    gross_profit = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0))
    profit_factor = float(gross_profit / (gross_loss + 1e-12))

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    win_rate = float(wins / len(trades)) if trades else 0.0

    avg_trade_pct = float(np.mean([t["pnl_pct"] for t in trades])) if trades else 0.0
    final_equity = float(equity[-1])

    return {
        "variant_key": result["variant_key"],
        "iteration": result["iteration"],
        "symbol": result["symbol"],
        "timeframe": tf,
        "starting_capital_usd": starting_capital,
        "final_equity_usd": round(final_equity, 2),
        "n_bars": int(result["n_bars"]),
        "span_start": result["span_start"],
        "span_end": result["span_end"],
        "n_trades": len(trades),
        "win_rate": round(win_rate, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "ann_return_pct": round(ann_return_pct, 4),
        "total_return_pct": round(total_return * 100.0, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "profit_factor": round(profit_factor, 4),
        "avg_trade_pct": round(avg_trade_pct, 4),
        "status": "PASS" if ann_return_pct >= 0.0 else "FAIL_NEGATIVE_ANN_RETURN",
    }


def _write_summary(result: dict, metrics: dict, tf: str, sym: str, starting_capital: float) -> dict:
    trades = result["trades"]
    equity_arr = np.asarray(result["equity"], dtype=np.float64)
    final_equity = float(equity_arr[-1])
    total_return = (final_equity / starting_capital) - 1.0

    summary = {
        "strategy": VARIANT_KEY,
        "iteration": int(result["iteration"]),
        "timeframe": tf,
        "symbol": sym,
        "instruments": [sym],
        "bars_per_year": int(_ann_factor_for_tf(tf)),
        "data_source": "perp_30m_parquet_BTCUSDT_resampled_4h",
        "starting_capital_usd": starting_capital,
        "per_symbol": [
            {
                "symbol": sym,
                "n_trades": len(trades),
                "win_rate": metrics["win_rate"],
                "ann_return_pct": metrics["ann_return_pct"],
                "max_drawdown_pct": metrics["max_drawdown_pct"],
                "profit_factor": metrics["profit_factor"],
                "sharpe": metrics["sharpe"],
                "sortino": metrics["sortino"],
                "final_equity": round(final_equity, 2),
                "total_return": round(total_return, 6),
                "pnl_usd_sum": round(final_equity - starting_capital, 2),
            }
        ],
        "portfolio": {
            "final_equity": round(final_equity, 2),
            "total_return": round(total_return, 6),
            "n_trades_total": len(trades),
            "ann_return_pct": metrics["ann_return_pct"],
            "sharpe": metrics["sharpe"],
            "sortino": metrics["sortino"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
        },
        "walk_forward": {"note": "config.json holds fold definitions; fold-by-fold evaluation requires additional per-fold runner."},
    }
    return summary


def _write_trades_csv(result: dict, tf: str, sym: str, csv_path: Path) -> None:
    trades = result["trades"]
    rows = []
    for t in trades:
        rows.append({
            "symbol": sym,
            "direction": t["direction"],
            "entry_signal_date": t["entry_ts"],
            "entry_fill_date": t["entry_ts"],
            "entry_price": round(float(t["entry_price"]), 6),
            "exit_signal_date": t["exit_ts"],
            "exit_fill_date": t["exit_ts"],
            "exit_price": round(float(t["exit_price"]), 6),
            "reason": t["exit_reason"],
            "pnl_usd": round(float(t["pnl_pct"]) * 10000.0, 4),
            "pnl_pct": round(float(t["pnl_pct"]), 6),
            "bars_held": int(t["bars_held"]),
            "macro_proximity_at_entry": int(t.get("macro_proximity_at_entry", 0)),
            "poc_distance_atr_at_entry": round(float(t.get("poc_distance_atr_at_entry", 0.0)), 4),
            "size_units": 1.0,
            "nav_at_entry": 100000.0,
        })
    pd.DataFrame(rows).to_csv(csv_path, index=False)


def _write_equity_csv(result: dict, df_index: pd.DatetimeIndex, csv_path: Path) -> None:
    equity = np.asarray(result["equity"], dtype=np.float64)
    n = len(equity)
    if len(df_index) >= n:
        ts = df_index[:n]
    else:
        inferred = pd.infer_freq(df_index[-100:]) if len(df_index) >= 3 else None
        ts_existing = df_index
        n_extra = n - len(df_index)
        extra_idx = pd.date_range(start=df_index[-1], periods=n_extra + 1, freq=inferred or "4h")[1:]
        ts = ts_existing.append(extra_idx)
    pd.DataFrame({"ts": ts, "equity": equity}).to_csv(csv_path, index=False)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with open(ROOT / "config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    sym = cfg["instruments"][0]
    starting_capital = float(cfg["starting_capital_usd"])

    df = load_btcusdt_4h()
    print(f"Loaded {len(df)} 4h bars: {df.index.min()} -> {df.index.max()}")
    result = run_backtest(df, cfg)
    metrics = _compute_metrics(result, TF, starting_capital)
    summary = _write_summary(result, metrics, TF, sym, starting_capital)
    trades_csv = RESULTS_DIR / f"trades_{TF}_{sym}.csv"
    _write_trades_csv(result, TF, sym, trades_csv)
    equity_csv = RESULTS_DIR / f"equity_{sym}.csv"
    _write_equity_csv(result, df.index, equity_csv)

    with open(RESULTS_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)
    with open(RESULTS_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(json.dumps({"metrics": metrics, "summary": summary, "files": [str(trades_csv.name), str(equity_csv.name)]}, indent=2, default=str))


if __name__ == "__main__":
    main()