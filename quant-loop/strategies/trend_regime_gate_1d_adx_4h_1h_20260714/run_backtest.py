"""Run the regime-gated trend backtest per symbol + portfolio aggregate."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data_loader import load_all
from strategy import annotate, baseline_hold, run_backtest

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _trade_rows(result) -> list:
    return [{
        "symbol": t.symbol,
        "direction": t.direction,
        "entry_date": t.entry_date.date().isoformat() if t.entry_date is not None else None,
        "entry_price": t.entry_price,
        "exit_date": t.exit_date.date().isoformat() if t.exit_date is not None else None,
        "exit_price": t.exit_price,
        "reason": t.reason,
        "pnl_usd": t.pnl_usd,
        "pnl_pct": t.pnl_pct,
        "bars_held": t.bars_held,
        "atr_at_entry": t.atr_at_entry,
        "risk_per_trade": t.risk_per_trade,
    } for t in result.trades]


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    data = load_all(cfg["instruments"])

    summary_rows = []
    portfolio_equity = None
    starting_capital = float(cfg["sizing"]["starting_capital_usd"])

    for sym, frames in data.items():
        cfg_t = dict(cfg)
        cfg_t["_symbol"] = sym
        annotated = annotate(frames["1h"], frames["4h"], frames["1d"], cfg_t)
        strat = run_backtest(annotated, cfg_t)
        hold = baseline_hold(frames["1h"], cfg_t)

        strat.equity_curve.to_frame("equity").to_csv(RESULTS_DIR / f"equity_{sym}.csv")
        pd.DataFrame(_trade_rows(strat)).to_csv(RESULTS_DIR / f"trades_{sym}.csv", index=False)

        summary_rows.append({
            "symbol": sym,
            "rows_1h": len(frames["1h"]),
            "rows_4h": len(frames["4h"]),
            "rows_1d": len(frames["1d"]),
            "span_start": frames["1h"].index[0].date().isoformat(),
            "span_end": frames["1h"].index[-1].date().isoformat(),
            "strategy": {
                "trades": strat.n_trades,
                "win_rate": strat.win_rate,
                "profit_factor": strat.profit_factor,
                "avg_hold_bars": strat.avg_holding_bars,
                "total_return": strat.total_return,
                "sharpe": strat.annualized_sharpe,
                "sortino": strat.annualized_sortino,
                "max_dd": strat.max_drawdown,
                "turnover_per_year": strat.turnover_per_year,
                "final_equity": float(strat.equity_curve.iloc[-1]),
            },
            "buy_hold": {
                "total_return": hold.total_return,
            },
        })

        if portfolio_equity is None:
            portfolio_equity = strat.equity_curve.copy()
            portfolio_equity.name = sym
        else:
            aligned = strat.equity_curve.reindex(portfolio_equity.index, method="ffill")
            portfolio_equity = portfolio_equity.add(aligned - starting_capital, fill_value=0.0)
            portfolio_equity.name = "portfolio"

    if portfolio_equity is not None and len(portfolio_equity) > 1:
        bar_ret = portfolio_equity.pct_change().fillna(0.0)
        bpy = 8760
        port_sharpe = float(bar_ret.mean() / bar_ret.std() * np.sqrt(bpy)) if bar_ret.std() > 0 else 0.0
        port_dd = float(((portfolio_equity - portfolio_equity.cummax()) / portfolio_equity.cummax()).min())
        years = max((portfolio_equity.index[-1] - portfolio_equity.index[0]).days / 365.25, 1.0 / 365.25)
        port_total = float(portfolio_equity.iloc[-1] / starting_capital - 1.0)
        port_ann = (1.0 + port_total) ** (1.0 / years) - 1.0
        portfolio = {
            "starting_capital_usd": starting_capital,
            "final_equity_usd": float(portfolio_equity.iloc[-1]),
            "total_return": port_total,
            "annualized_return": port_ann,
            "sharpe": port_sharpe,
            "max_drawdown": port_dd,
            "n_years": years,
        }
        portfolio_equity.to_csv(RESULTS_DIR / "equity_portfolio.csv")
    else:
        portfolio = {
            "starting_capital_usd": starting_capital,
            "final_equity_usd": starting_capital,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "n_years": 0.0,
        }

    payload = {
        "strategy": cfg["strategy"],
        "iteration": cfg["iteration"],
        "axis": cfg.get("axis", ""),
        "timeframe_entry": cfg["timeframe_entry"],
        "timeframe_trend": cfg["timeframe_trend"],
        "timeframe_regime": cfg["timeframe_regime"],
        "instruments": cfg["instruments"],
        "portfolio": portfolio,
        "per_symbol": summary_rows,
    }
    (RESULTS_DIR / "summary.json").write_text(json.dumps(payload, indent=2, default=float))
    (RESULTS_DIR / "metrics.json").write_text(json.dumps({
        "sharpe": portfolio["sharpe"],
        "annualized_return": portfolio["annualized_return"],
        "total_return": portfolio["total_return"],
        "max_drawdown": portfolio["max_drawdown"],
        "n_trades_total": sum(r["strategy"]["trades"] for r in summary_rows),
        "win_rate_avg": float(np.mean([r["strategy"]["win_rate"] for r in summary_rows])) if summary_rows else 0.0,
        "profit_factor_avg": float(np.mean([r["strategy"]["profit_factor"] for r in summary_rows if np.isfinite(r["strategy"]["profit_factor"])])) if summary_rows else 0.0,
    }, indent=2))
    print(json.dumps(payload, indent=2, default=float))
    print("\n=== Per-instrument ===")
    print(f"{'Symbol':<10}{'Trades':>8}{'WinRate':>9}{'PF':>9}{'Return':>10}{'Sharpe':>9}{'MaxDD':>9}{'BH':>9}")
    for row in summary_rows:
        s = row["strategy"]
        b = row["buy_hold"]["total_return"]
        print(f"{row['symbol']:<10}{s['trades']:>8d}{s['win_rate']:>9.3f}"
              f"{s['profit_factor']:>9.3f}{s['total_return']:>10.4f}"
              f"{s['sharpe']:>9.3f}{s['max_dd']:>9.3f}{b:>9.4f}")
    print(
        f"\n=== Portfolio ===\n"
        f"final_equity={portfolio['final_equity_usd']:.2f} "
        f"total_return={portfolio['total_return']:.4f} "
        f"ann_return={portfolio['annualized_return']:.4f} "
        f"sharpe={portfolio['sharpe']:.3f} max_dd={portfolio['max_drawdown']:.3f}"
    )
    return 0 if summary_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())