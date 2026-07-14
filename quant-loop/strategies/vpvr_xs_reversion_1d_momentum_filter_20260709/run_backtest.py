"""Run V4: vpvr_xs_reversion_1d_momentum_filter_20260709 (iter 68)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from data_loader import load_all
from strategy import run_backtest

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
            "pnl_usd": t.pnl, "pnl_pct": t.pnl_pct, "bars_held": t.bars_held,
        }
        for t in result.trades
    ]


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    tf = cfg["timeframe"]
    data = load_all(cfg["instruments"], tf)
    res = run_backtest(data, cfg)

    res.equity_curve.to_frame("equity").to_csv(RESULTS_DIR / "equity_portfolio.csv")

    # Per-symbol trades_*.csv — grouped by symbol, file name pattern trades_<SYMBOL>.csv.
    by_sym: dict[str, list] = {}
    for t in res.trades:
        by_sym.setdefault(t.symbol, []).append(t)
    for sym, trs in by_sym.items():
        rows = [
            {
                "symbol": t.symbol, "direction": t.direction,
                "entry_date": t.entry_date.isoformat() if t.entry_date is not None else None,
                "entry_price": t.entry_price,
                "exit_date": t.exit_date.isoformat() if t.exit_date is not None else None,
                "exit_price": t.exit_price, "reason": t.reason,
                "pnl_usd": t.pnl, "pnl_pct": t.pnl_pct, "bars_held": t.bars_held,
            }
            for t in trs
        ]
        pd.DataFrame(rows).to_csv(RESULTS_DIR / f"trades_A_{tf}_{sym}.csv", index=False)
        pd.DataFrame(rows).to_csv(RESULTS_DIR / f"trades_{sym}.csv", index=False)

    summary_rows = [
        {
            "portfolio": True,
            "symbols": cfg["instruments"],
            "rows_total": sum(len(d) for d in data.values()),
            "span_start": res.equity_curve.index[0].date().isoformat(),
            "span_end": res.equity_curve.index[-1].date().isoformat(),
            "timeframe": tf, "iteration": cfg["iteration"],
            "strategy": {
                "trades": res.n_trades, "win_rate": res.win_rate,
                "profit_factor": res.profit_factor, "total_return": res.total_return,
                "sharpe": res.annualized_sharpe, "sortino": res.annualized_sortino,
                "max_dd": res.max_drawdown, "turnover_per_year": res.turnover_per_year,
            },
            "by_symbol": res.per_symbol,
        }
    ]
    out_path = RESULTS_DIR / "summary.json"
    out_path.write_text(json.dumps(summary_rows, indent=2, default=float))
    print(json.dumps(summary_rows, indent=2, default=float))

    s = summary_rows[0]["strategy"]
    agg = {
        "iteration": cfg["iteration"], "timeframe": tf,
        "instruments": cfg["instruments"], "portfolio": True,
        "sharpe": s["sharpe"], "mdd": s["max_dd"], "n_trades": s["trades"],
        "win_rate": s["win_rate"], "profit_factor": s["profit_factor"],
        "total_return": s["total_return"], "by_symbol": res.per_symbol,
    }
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(agg, indent=2, default=float))

    print(f"\n=== V4 vpvr_xs_reversion_1d_momentum_filter_20260709 iter={cfg['iteration']} ===")
    print(f"{'Portfolio':<12}{'Trades':>8}{'WinRate':>9}{'PF':>9}{'Return':>10}{'Sharpe':>9}{'MaxDD':>9}")
    print(f"{'BTC+ETH+SOL':<12}{s['trades']:>8d}{s['win_rate']:>9.3f}"
          f"{s['profit_factor']:>9.3f}{s['total_return']:>10.4f}"
          f"{s['sharpe']:>9.3f}{s['max_dd']:>9.3f}")
    print("\nBy symbol:")
    for sym, d in res.per_symbol.items():
        print(f"  {sym:<10} trades={d['n_trades']:>4d} win_rate={d['win_rate']:.3f} avg_pnl_pct={d['avg_pnl_pct']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())