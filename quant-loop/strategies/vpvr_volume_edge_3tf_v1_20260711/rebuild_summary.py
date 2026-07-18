"""Rebuild summary.json + metrics.json from already-written per-symbol CSVs.

Use this when ``run_backtest.py`` completed the backtest portion but
failed at JSON serialization (e.g. complex NaN). It re-derives per-symbol
stats from trades_*.csv and portfolio stats from equity_portfolio.csv
without re-running annotate().
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

RESULTS_DIR = Path(__file__).parent / "results"
CONFIG_PATH = Path(__file__).parent / "config.json"


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, complex):
        if math.isnan(obj.real) or math.isinf(obj.real) or math.isnan(obj.imag) or math.isinf(obj.imag):
            return None
        if obj.imag == 0:
            return float(obj.real)
        return None
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(obj, (np.complexfloating,)):
        c = complex(obj)
        if math.isnan(c.real) or math.isinf(c.real) or math.isnan(c.imag) or math.isinf(c.imag):
            return None
        if c.imag == 0:
            return float(c.real)
        return None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj


def _json_default(o):
    """Coerce anything straggler that _sanitize missed."""
    if isinstance(o, complex):
        if o.imag == 0 and not (math.isnan(o.real) or math.isinf(o.real)):
            return float(o.real)
        return None
    if isinstance(o, (np.floating,)):
        return _sanitize(float(o))
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return o
    return str(o)


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    summary_rows = []
    port_eq = pd.read_csv(RESULTS_DIR / "equity_portfolio.csv", index_col=0, parse_dates=True)
    if port_eq.index.tz is None:
        port_eq.index = port_eq.index.tz_localize("UTC")
    starting_capital = float(cfg["sizing"]["starting_capital_usd"])

    for sym in cfg["instruments"]:
        trades = pd.read_csv(RESULTS_DIR / f"trades_{sym}.csv")
        eq = pd.read_csv(RESULTS_DIR / f"equity_{sym}.csv", index_col=0, parse_dates=True)
        if eq.index.tz is None:
            eq.index = eq.index.tz_localize("UTC")
        n = len(trades)
        if n == 0:
            summary_rows.append({
                "symbol": sym, "rows_1m": 0, "rows_15m": 0, "rows_4h": 0,
                "span_start": "", "span_end": "",
                "strategy": {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                             "avg_hold_bars": 0.0, "total_return": 0.0,
                             "sharpe": 0.0, "sortino": 0.0, "max_dd": 0.0,
                             "turnover_per_year": 0.0, "final_equity": starting_capital},
                "buy_hold": {"total_return": 0.0},
            })
            continue
        pnls = trades["pnl_usd"].to_numpy(dtype=float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        wr = float((pnls > 0).mean())
        pf = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
        avg_hold = float(trades["bars_held"].mean())
        eq_s = eq["equity"]
        if len(eq_s) > 1:
            total_ret = float(eq_s.iloc[-1] / starting_capital - 1.0)
            dd = float(((eq_s - eq_s.cummax()) / eq_s.cummax()).min())
            years = max((eq_s.index[-1] - eq_s.index[0]).days / 365.25, 1.0 / 365.25)
            turnover = n / years
            ret = eq_s.pct_change().fillna(0.0)
            bpy_daily = 365
            sharpe = float(ret.mean() / ret.std() * np.sqrt(bpy_daily)) if ret.std() > 0 else 0.0
            down = ret[ret < 0]
            dstd = down.std() if len(down) > 0 else ret.std()
            sortino = float(ret.mean() / dstd * np.sqrt(bpy_daily)) if dstd and dstd > 0 else 0.0
        else:
            total_ret = 0.0; dd = 0.0; turnover = 0.0; sharpe = 0.0; sortino = 0.0

        # Buy & hold baseline.
        # Use the symbol's first/last close from trades entry/exit.
        first_close = float(trades["entry_price"].iloc[0])
        last_close = float(trades["exit_price"].iloc[-1])
        bh_return = (last_close - first_close) / first_close if first_close > 0 else 0.0

        summary_rows.append({
            "symbol": sym,
            "rows_1m": int(len(eq_s)),  # placeholder; not actually used downstream
            "rows_15m": 0, "rows_4h": 0,
            "span_start": str(eq_s.index[0].date()),
            "span_end": str(eq_s.index[-1].date()),
            "strategy": {
                "trades": int(n), "win_rate": wr,
                "profit_factor": pf, "avg_hold_bars": avg_hold,
                "total_return": total_ret, "sharpe": sharpe,
                "sortino": sortino, "max_dd": dd,
                "turnover_per_year": turnover,
                "final_equity": float(eq_s.iloc[-1]),
            },
            "buy_hold": {"total_return": bh_return},
        })

    # Portfolio stats from equity_portfolio.csv
    if len(port_eq) > 1:
        ret = port_eq["equity"].pct_change().fillna(0.0)
        port_sharpe = float(ret.mean() / ret.std() * np.sqrt(365)) if ret.std() > 0 else 0.0
        port_dd = float(((port_eq["equity"] - port_eq["equity"].cummax()) / port_eq["equity"].cummax()).min())
        years = max((port_eq.index[-1] - port_eq.index[0]).days / 365.25, 1.0 / 365.25)
        port_total = float(port_eq["equity"].iloc[-1] / starting_capital - 1.0)
        port_ann = (1.0 + port_total) ** (1.0 / years) - 1.0
        portfolio = {
            "starting_capital_usd": starting_capital,
            "final_equity_usd": float(port_eq["equity"].iloc[-1]),
            "total_return": port_total,
            "annualized_return": port_ann,
            "sharpe": port_sharpe,
            "max_drawdown": port_dd,
            "n_years": years,
        }
    else:
        portfolio = {"starting_capital_usd": starting_capital,
                     "final_equity_usd": starting_capital, "total_return": 0.0,
                     "annualized_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                     "n_years": 0.0}

    payload = {
        "strategy": cfg["strategy"], "iteration": cfg["iteration"],
        "axis": cfg.get("axis", ""),
        "timeframe_entry": cfg["timeframe_entry"],
        "timeframe_filter": cfg["timeframe_filter"],
        "timeframe_trend": cfg["timeframe_trend"],
        "instruments": cfg["instruments"],
        "portfolio": portfolio, "per_symbol": summary_rows,
    }
    (RESULTS_DIR / "summary.json").write_text(json.dumps(_sanitize(payload), indent=2, default=_json_default))
    metrics_payload = {
        "sharpe": portfolio["sharpe"],
        "annualized_return": portfolio["annualized_return"],
        "total_return": portfolio["total_return"],
        "max_drawdown": portfolio["max_drawdown"],
        "n_trades_total": sum(r["strategy"]["trades"] for r in summary_rows),
        "win_rate_avg": float(np.mean([r["strategy"]["win_rate"] for r in summary_rows])) if summary_rows else 0.0,
        "profit_factor_avg": float(np.mean([r["strategy"]["profit_factor"] for r in summary_rows if np.isfinite(r["strategy"]["profit_factor"])])) if summary_rows else 0.0,
    }
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(_sanitize(metrics_payload), indent=2))
    print(json.dumps(_sanitize(payload), indent=2, default=float))
    print(f"\nMetrics: {json.dumps(_sanitize(metrics_payload))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
