"""Run the BB + RSI 1m reversion strategy per symbol, then aggregate.

Outputs (in ``results/``):

    summary.json      — per-symbol strategy + buy-hold metrics (full row)
    metrics.json      — Evidence Gate payload (sharpe / mdd / n_trades + extras)
    equity_<SYM>.csv  — bar-by-bar equity curve
    trades_<SYM>.csv  — flat trade list
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from data_loader import load_all
from strategy import baseline_hold, run_backtest

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _trade_rows(result) -> List[Dict[str, Any]]:
    return [
        {
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_date": t.entry_date.isoformat() if t.entry_date is not None else None,
            "entry_price": t.entry_price,
            "exit_date": t.exit_date.isoformat() if t.exit_date is not None else None,
            "exit_price": t.exit_price,
            "reason": t.reason,
            "pnl_usd": t.pnl,
            "pnl_pct": t.pnl_pct,
            "bars_held": t.bars_held,
            "atr_at_entry": t.atr_at_entry,
        }
        for t in result.trades
    ]


def _bars_per_year(timeframe: str) -> float:
    """Approximate bars per year for turnover / sharpe annualization."""
    tf = timeframe.lower()
    if tf.endswith("m"):
        return float(int(tf[:-1])) * 60 * 24 * 365
    if tf.endswith("h"):
        return float(int(tf[:-1])) * 24 * 365
    if tf.endswith("d"):
        return float(int(tf[:-1])) * 365
    return 252.0


def _strategy_metrics(s) -> Dict[str, Any]:
    return {
        "trades": s.n_trades,
        "win_rate": s.win_rate,
        "profit_factor": s.profit_factor,
        "avg_hold_bars": s.avg_holding_bars,
        "total_return": s.total_return,
        "sharpe": s.annualized_sharpe,
        "sortino": s.annualized_sortino,
        "max_dd": s.max_drawdown,
        "turnover_per_year": s.turnover_per_year,
    }


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    data = load_all(cfg["instruments"])
    summary_rows: List[Dict[str, Any]] = []
    by_symbol: Dict[str, Dict[str, Any]] = {}
    portfolio_equity: Dict[pd.Timestamp, float] = {}

    starting = cfg["starting_capital_usd"]
    timeframe = cfg["timeframe"]
    bars_per_year = _bars_per_year(timeframe)

    for sym, df in data.items():
        if not isinstance(df.index, pd.DatetimeIndex):
            raise SystemExit(f"{sym}: index is not datetime")
        cfg_t = dict(cfg)
        cfg_t["_symbol"] = sym

        strat = run_backtest(df, cfg_t)
        hold = baseline_hold(df, cfg_t)

        # Per-symbol outputs:
        # - ``trades_<SYM>.csv`` is the actionable record (always written).
        # - ``equity_<SYM>.csv`` is downsampled to hourly so a 1m run does not
        #   produce a 100+ MB blob. Set ``equity_full=true`` in config to
        #   override and emit the full per-bar curve.
        pd.DataFrame(_trade_rows(strat)).to_csv(RESULTS_DIR / f"trades_{sym}.csv", index=False)
        eq_df = strat.equity_curve.to_frame("equity")
        if not cfg.get("equity_full", False):
            # Resample to last-of-hour. Cover 1h/5m/1m runs uniformly.
            try:
                eq_df = eq_df.resample("1h").last().dropna()
            except Exception:
                pass
        eq_df.to_csv(RESULTS_DIR / f"equity_{sym}.csv")

        summary_rows.append({
            "symbol": sym,
            "rows": len(df),
            "span_start": df.index[0].isoformat(),
            "span_end": df.index[-1].isoformat(),
            "strategy": _strategy_metrics(strat),
            "buy_hold": {"total_return": hold.total_return},
        })
        by_symbol[sym] = {
            "sharpe": strat.annualized_sharpe,
            "mdd": strat.max_drawdown,
            "n_trades": strat.n_trades,
            "win_rate": strat.win_rate,
            "profit_factor": strat.profit_factor,
            "total_return": strat.total_return,
        }
        # Build an aggregate equity curve as the sum of per-symbol equity
        # series. Each symbol started from the same notional share.
        share = starting / len(data)
        scaled = strat.equity_curve * (share / starting)
        for ts, val in scaled.items():
            portfolio_equity[ts] = portfolio_equity.get(ts, 0.0) + float(val)

    out_path = RESULTS_DIR / "summary.json"
    out_path.write_text(json.dumps(summary_rows, indent=2, default=float))
    print(json.dumps(summary_rows, indent=2, default=float))

    if not summary_rows:
        print("No instruments backtested successfully.", file=sys.stderr)
        return 1

    # ---------------- Evidence Gate payload ----------------
    # Aggregate over the portfolio equity curve (not symbol-level averages)
    # so the headline numbers reflect the *combined* book, not the mean of
    # symbol books. Falls back to per-symbol metrics if the portfolio curve
    # only has ≤1 point.
    total_n_trades = sum(by_symbol[s]["n_trades"] for s in by_symbol)
    only = next(iter(by_symbol.values()), None) if len(by_symbol) == 1 else None
    pe = pd.Series(portfolio_equity).sort_index()
    if len(pe) > 1:
        bar_ret = pe.pct_change().fillna(0.0)
        sigma = float(bar_ret.std(ddof=0))
        agg_sharpe = (
            float(bar_ret.mean() / sigma * math.sqrt(bars_per_year)) if sigma > 0 else 0.0
        )
        dn = bar_ret[bar_ret < 0]
        dstd = float(dn.std(ddof=0)) if len(dn) > 1 else sigma
        agg_sortino = (
            float(bar_ret.mean() / dstd * math.sqrt(bars_per_year)) if dstd > 0 else 0.0
        )
        agg_mdd = float(((pe / pe.cummax()) - 1.0).min())
        days = (pe.index[-1] - pe.index[0]).days
        years = max(days / 365.25, 1.0 / 365.25)
        agg_return = float(pe.iloc[-1] / pe.iloc[0] - 1.0)
        annualized_return = float((pe.iloc[-1] / pe.iloc[0]) ** (1.0 / years) - 1.0)
    elif only is not None:
        agg_sharpe = only["sharpe"]
        agg_mdd = only["mdd"]
        agg_return = only["total_return"]
        agg_sortino = 0.0
        annualized_return = 0.0
    else:
        agg_sharpe = 0.0
        agg_mdd = 0.0
        agg_return = 0.0
        agg_sortino = 0.0
        annualized_return = 0.0

    # annualized return from aggregate equity
    pe = pd.Series(portfolio_equity).sort_index()

    metrics_payload = {
        "strategy": cfg["strategy"],
        "iteration": cfg.get("iteration", 1),
        "timeframe": timeframe,
        "instruments": cfg["instruments"],
        "sharpe": agg_sharpe,
        "sortino": agg_sortino,
        "mdd": agg_mdd,
        "total_return": agg_return,
        "annualized_return": annualized_return,
        "n_trades": total_n_trades,
        "by_symbol": by_symbol,
        "agg_sharpe_mean": agg_sharpe,
        "agg_mdd_worst": agg_mdd,
        "agg_n_trades_total": total_n_trades,
        "equity_curve_start": str(pe.index[0].date()) if len(pe) > 0 else None,
        "equity_curve_end": str(pe.index[-1].date()) if len(pe) > 0 else None,
        "active_universe": list(by_symbol.keys()),
        "target_universe_size": len(cfg["instruments"]),
        "data_source": cfg["data_source"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(metrics_payload, indent=2, default=float))
    print("\n=== metrics.json ===")
    print(json.dumps(metrics_payload, indent=2, default=float))

    print("\n=== Per-instrument (strategy vs buy-hold) ===")
    print(f"{'Symbol':<10}{'Trades':>8}{'WinRate':>9}{'PF':>9}{'Return':>10}{'Sharpe':>9}{'MaxDD':>9}{'BH':>9}")
    for row in summary_rows:
        s = row["strategy"]
        b = row["buy_hold"]["total_return"]
        print(
            f"{row['symbol']:<10}{s['trades']:>8d}{s['win_rate']:>9.3f}"
            f"{s['profit_factor']:>9.3f}{s['total_return']:>10.4f}"
            f"{s['sharpe']:>9.3f}{s['max_dd']:>9.3f}{b:>9.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())