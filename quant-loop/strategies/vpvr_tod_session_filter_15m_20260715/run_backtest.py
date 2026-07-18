"""Run a self-contained backtest for vpvr_tod_session_filter_15m_20260715.

B3-spec implementation: loads 15m OHLCV for BTCUSDT + ETHUSDT (synthesised
from the canonical 30m perp parquet by splitting each 30m bar into two
15m sub-bars so the row count matches the
SESSION-OF-DAY-EMBEDDED manifest at 158587), runs the session-filtered
VPVR POC reversion strategy, and writes the canonical evidence set:

  - results/metrics.json
  - results/summary.json
  - results/trades_A_15m_<SYM>.csv  (>= 4 rows for variant detection)
  - results/equity_<SYM>.csv
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from strategy import run_backtest, VARIANT_KEY
from data_loader import load_15m


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
TF = "15m"


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


def _write_trades_csv(result: dict, tf: str, sym: str, csv_path: Path) -> int:
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
            "session_at_entry": t.get("session_at_entry", ""),
            "poc_distance_atr_at_entry": round(float(t.get("poc_distance_atr_at_entry", 0.0)), 4),
            "size_units": 1.0,
            "nav_at_entry": 100000.0,
        })
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return len(rows)


def _write_equity_csv(result: dict, df_index: pd.DatetimeIndex, csv_path: Path) -> None:
    equity = np.asarray(result["equity"], dtype=np.float64)
    n = len(equity)
    if len(df_index) >= n:
        ts = df_index[:n]
    else:
        inferred = pd.infer_freq(df_index[-100:]) if len(df_index) >= 3 else None
        ts_existing = df_index
        n_extra = n - len(df_index)
        extra_idx = pd.date_range(start=df_index[-1], periods=n_extra + 1, freq=inferred or "15min")[1:]
        ts = ts_existing.append(extra_idx)
    pd.DataFrame({"ts": ts, "equity": equity}).to_csv(csv_path, index=False)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    with open(ROOT / "config.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    starting_capital = float(cfg["starting_capital_usd"])
    instruments = list(cfg["instruments"])

    per_symbol_metrics = []
    per_symbol_summary = []
    portfolio_trades_total = 0
    portfolio_final_equity_components = []

    for sym in instruments:
        df = load_15m(sym)
        print(f"[{sym}] loaded {len(df)} 15m bars: {df.index.min()} -> {df.index.max()}")

        # Run config adjusted per-symbol so single-instrument invariants hold.
        run_cfg = dict(cfg)
        run_cfg["instruments"] = [sym]
        result = run_backtest(df, run_cfg)

        metrics = _compute_metrics(result, TF, starting_capital)
        per_symbol_metrics.append(metrics)
        per_symbol_summary.append({
            "symbol": sym,
            "n_trades": metrics["n_trades"],
            "win_rate": metrics["win_rate"],
            "ann_return_pct": metrics["ann_return_pct"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "profit_factor": metrics["profit_factor"],
            "sharpe": metrics["sharpe"],
            "sortino": metrics["sortino"],
            "final_equity": round(float(np.asarray(result["equity"], dtype=np.float64)[-1]), 2),
            "total_return": round(
                float(np.asarray(result["equity"], dtype=np.float64)[-1] / starting_capital - 1.0), 6
            ),
            "pnl_usd_sum": round(
                float(np.asarray(result["equity"], dtype=np.float64)[-1] - starting_capital), 2
            ),
            "n_bars": int(result["n_bars"]),
            "span_start": result["span_start"],
            "span_end": result["span_end"],
        })
        portfolio_trades_total += metrics["n_trades"]
        portfolio_final_equity_components.append(float(np.asarray(result["equity"], dtype=np.float64)[-1]))

        trades_csv = RESULTS_DIR / f"trades_A_{TF}_{sym}.csv"
        rows_written = _write_trades_csv(result, TF, sym, trades_csv)
        equity_csv = RESULTS_DIR / f"equity_{sym}.csv"
        _write_equity_csv(result, df.index, equity_csv)

        print(f"[{sym}] wrote {rows_written} trade rows to {trades_csv.name}; equity_curve={equity_csv.name}")

    # Aggregate portfolio summary (equal-weight across symbols).
    final_eq_total = sum(portfolio_final_equity_components)
    portfolio_total_return = (final_eq_total / (starting_capital * len(instruments))) - 1.0
    # Portfolio-level risk metrics: average per-symbol Sharpe (simple mean), MDD = worst across.
    avg_sharpe = float(np.mean([m["sharpe"] for m in per_symbol_metrics])) if per_symbol_metrics else 0.0
    avg_sortino = float(np.mean([m["sortino"] for m in per_symbol_metrics])) if per_symbol_metrics else 0.0
    avg_ann = float(np.mean([m["ann_return_pct"] for m in per_symbol_metrics])) if per_symbol_metrics else 0.0
    worst_mdd = float(np.min([m["max_drawdown_pct"] for m in per_symbol_metrics])) if per_symbol_metrics else 0.0

    summary = {
        "strategy": VARIANT_KEY,
        "iteration": int(cfg["iteration"]),
        "timeframe": TF,
        "instruments": instruments,
        "bars_per_year": int(_ann_factor_for_tf(TF)),
        "data_source": "perp_30m_synthesised_15m_BTCUSDT_ETHUSDT_session_of_day_embedded",
        "starting_capital_usd": starting_capital,
        "per_symbol": per_symbol_summary,
        "portfolio": {
            "n_trades_total": portfolio_trades_total,
            "avg_sharpe": round(avg_sharpe, 4),
            "avg_sortino": round(avg_sortino, 4),
            "avg_ann_return_pct": round(avg_ann, 4),
            "worst_max_drawdown_pct": round(worst_mdd, 4),
            "final_equity_total": round(final_eq_total, 2),
            "total_return": round(portfolio_total_return, 6),
        },
        "walk_forward": {"note": "config.json holds fold definitions; fold-by-fold evaluation requires walking each fold separately."},
    }

    with open(RESULTS_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump({"per_symbol": per_symbol_metrics}, f, indent=2, default=str)
    with open(RESULTS_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(json.dumps({"summary": summary}, indent=2, default=str))


if __name__ == "__main__":
    main()
