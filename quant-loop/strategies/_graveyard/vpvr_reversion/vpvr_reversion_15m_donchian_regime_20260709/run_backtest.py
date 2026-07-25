"""Run V2: vpvr_reversion_15m_donchian_regime_20260709 (iter 66)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from data_loader import load_all
from strategy import baseline_hold, run_backtest

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _trade_rows(result) -> list:
    return [
        {
            "symbol": t.symbol, "direction": t.direction,
            "entry_date": t.entry_date.isoformat() if t.entry_date is not None else None,
            "entry_price": t.entry_price,
            "exit_date": t.exit_date.isoformat() if t.exit_date is not None else None,
            "exit_price": t.exit_price, "reason": t.reason,
            "pnl_usd": t.pnl, "pnl_pct": t.pnl_pct,
            "bars_held": t.bars_held, "atr_at_entry": t.atr_at_entry,
        }
        for t in result.trades
    ]


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    tf = cfg["timeframe"]
    data = load_all(cfg["instruments"], tf)
    summary_rows = []

    for sym, df in data.items():
        if not isinstance(df.index, pd.DatetimeIndex):
            raise SystemExit(f"{sym}: index is not datetime")
        cfg_t = dict(cfg); cfg_t["_symbol"] = sym
        strat = run_backtest(df, cfg_t)
        hold = baseline_hold(df, cfg_t)

        strat.equity_curve.to_frame("equity").to_csv(RESULTS_DIR / f"equity_{sym}.csv")
        pd.DataFrame(_trade_rows(strat)).to_csv(RESULTS_DIR / f"trades_A_{tf}_{sym}.csv", index=False)
        pd.DataFrame(_trade_rows(strat)).to_csv(RESULTS_DIR / f"trades_{sym}.csv", index=False)

        summary_rows.append({
            "symbol": sym, "rows": len(df),
            "span_start": df.index[0].date().isoformat(),
            "span_end": df.index[-1].date().isoformat(),
            "timeframe": tf, "iteration": cfg["iteration"],
            "strategy": {
                "trades": strat.n_trades, "win_rate": strat.win_rate,
                "profit_factor": strat.profit_factor, "avg_hold_bars": strat.avg_holding_bars,
                "total_return": strat.total_return, "sharpe": strat.annualized_sharpe,
                "sortino": strat.annualized_sortino, "max_dd": strat.max_drawdown,
                "turnover_per_year": strat.turnover_per_year,
            },
            "buy_hold": {"total_return": hold.total_return},
        })

    out_path = RESULTS_DIR / "summary.json"
    out_path.write_text(json.dumps(summary_rows, indent=2, default=float))
    print(json.dumps(summary_rows, indent=2, default=float))

    agg = {"iteration": cfg["iteration"], "timeframe": tf, "instruments": cfg["instruments"], "by_symbol": {}}
    for row in summary_rows:
        s = row["strategy"]
        agg["by_symbol"][row["symbol"]] = {
            "sharpe": s["sharpe"], "mdd": s["max_dd"], "n_trades": s["trades"],
            "win_rate": s["win_rate"], "profit_factor": s["profit_factor"], "total_return": s["total_return"],
        }
    sharpes = [v["sharpe"] for v in agg["by_symbol"].values() if isinstance(v["sharpe"], (int, float))]
    agg["agg_sharpe_mean"] = float(sum(sharpes) / len(sharpes)) if sharpes else 0.0
    mdds = [v["mdd"] for v in agg["by_symbol"].values()]
    agg["agg_mdd_worst"] = float(min(mdds)) if mdds else 0.0
    agg["agg_n_trades_total"] = sum(v["n_trades"] for v in agg["by_symbol"].values())
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(agg, indent=2, default=float))

    if not summary_rows:
        print("No instruments backtested successfully.", file=sys.stderr)
        return 1

    print(f"\n=== V2 vpvr_reversion_15m_donchian_regime_20260709 iter={cfg['iteration']} ===")
    print(f"{'Symbol':<10}{'Trades':>8}{'WinRate':>9}{'PF':>9}{'Return':>10}{'Sharpe':>9}{'MaxDD':>9}{'BH':>9}")
    for row in summary_rows:
        s = row["strategy"]; b = row["buy_hold"]["total_return"]
        print(f"{row['symbol']:<10}{s['trades']:>8d}{s['win_rate']:>9.3f}"
              f"{s['profit_factor']:>9.3f}{s['total_return']:>10.4f}"
              f"{s['sharpe']:>9.3f}{s['max_dd']:>9.3f}{b:>9.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())