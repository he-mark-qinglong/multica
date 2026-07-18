"""Run V3_xs_smart_routing (iter#105) backtest.

TF = 15m. BTCUSDT. Microprice = Binance taker-buy-share imbalance proxy.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np

from data_loader import load_all
from strategy import VARIANT_KEY, run_backtest

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _bars_per_year(timeframe: str) -> float:
    tf = timeframe.lower()
    if tf.endswith("m"):
        m = int(tf[:-1]); return (60 * 24 * 365) / m
    if tf.endswith("h"):
        h = int(tf[:-1]); return (24 * 365) / h
    if tf.endswith("d"):
        d = int(tf[:-1]); return 365 / d
    raise ValueError(tf)


def _summarise(trades, equity, starting_cap, n_bars, timeframe) -> dict:
    n = len(trades)
    base = {
        "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "avg_hold_bars": 0.0, "total_return_pct": 0.0,
        "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0,
        "trades_per_year": 0.0, "exit_reasons": {},
    }
    if n == 0:
        return {**base, "tag": "NOT-PROFITABLE",
                "data_status": "MULTI-VENUE-DATA-MISSING microprice=binance_buy_share_proxy"}
    pnls = np.array([t.pnl_pct for t in trades], dtype=np.float64)
    wins = pnls > 0; losses = pnls <= 0
    win_rate = float(wins.mean())
    gw = float(pnls[wins].sum()) if wins.any() else 0.0
    gl = float(-pnls[losses].sum()) if losses.any() else 0.0
    pf = gw / gl if gl > 0 else float("inf")
    avg_hold = float(np.mean([t.bars_held for t in trades]))
    total = float(equity[-1] / starting_cap - 1.0)
    years = max(n_bars / _bars_per_year(timeframe), 1e-9)
    tpy = n / years
    mu_t = float(np.mean(pnls))
    sd_t = float(np.std(pnls, ddof=0))
    sharpe = (mu_t / sd_t) * np.sqrt(tpy) if sd_t > 0 else 0.0
    ds = pnls[pnls < 0]
    dsigma = float(np.std(ds, ddof=0)) if ds.size else 0.0
    sortino = (mu_t / dsigma) * np.sqrt(tpy) if dsigma > 0 else 0.0
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    max_dd = float(dd.min())
    exit_reasons: Dict[str, int] = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
    tag = "PROFITABLE" if sharpe >= 1.0 else "NOT-PROFITABLE"
    return {**base,
        "n_trades": n, "win_rate": win_rate, "profit_factor": float(pf),
        "avg_hold_bars": avg_hold, "total_return_pct": total,
        "sharpe": float(sharpe), "sortino": float(sortino),
        "max_drawdown_pct": max_dd, "trades_per_year": tpy, "tag": tag,
        "data_status": "MULTI-VENUE-DATA-MISSING microprice=binance_buy_share_proxy"}


def _write_trades_csv(trades, p: Path) -> None:
    fields = ["variant", "symbol", "direction", "entry_ts", "entry_price",
              "exit_ts", "exit_price", "pnl_pct", "bars_held", "exit_reason",
              "twap_slices_used", "cancel_replace_triggered", "micro_z_at_entry"]
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for t in trades:
            w.writerow({
                "variant": t.variant, "symbol": t.symbol,
                "direction": t.direction,
                "entry_ts": t.entry_ts, "entry_price": f"{t.entry_price:.6f}",
                "exit_ts": t.exit_ts, "exit_price": f"{t.exit_price:.6f}",
                "pnl_pct": f"{t.pnl_pct:.6f}", "bars_held": t.bars_held,
                "exit_reason": t.exit_reason,
                "twap_slices_used": t.twap_slices_used,
                "cancel_replace_triggered": str(t.cancel_replace_triggered),
                "micro_z_at_entry": f"{t.micro_z_at_entry:.3f}",
            })


def _write_equity_csv(equity, p: Path) -> None:
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bar", "equity"])
        for i, e in enumerate(equity):
            w.writerow([i, f"{e:.6f}"])


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--last-bars", type=int, default=None)
    args = parser.parse_args()
    cfg = json.loads(CONFIG_PATH.read_text())
    data = load_all(cfg["instruments"])
    if not data:
        print("no instruments loaded", file=sys.stderr); return 1

    summary_rows: List[dict] = []
    starting_cap = float(cfg["starting_capital_usd"])
    timeframe = cfg["timeframe"]

    for sym, df in data.items():
        df_run = df
        if args.last_bars is not None and len(df_run) > args.last_bars:
            df_run = df_run.iloc[-args.last_bars:].copy()
        cfg_t = dict(cfg); cfg_t["_symbol"] = sym
        res = run_backtest(df_run, cfg_t)
        metrics = _summarise(res["trades"], res["equity"], starting_cap,
                             res["n_bars"], timeframe)
        metrics["symbol"] = sym
        metrics["span_start"] = res["span_start"]
        metrics["span_end"] = res["span_end"]
        metrics["n_bars"] = res["n_bars"]
        _write_trades_csv(res["trades"], RESULTS_DIR / f"trades_A_15m_{sym}.csv")
        _write_equity_csv(res["equity"], RESULTS_DIR / f"equity_15m_{sym}.csv")
        summary_rows.append(metrics)

    agg_sharpe = float(np.mean([r["sharpe"] for r in summary_rows])) if summary_rows else 0.0
    agg_n = sum(r["n_trades"] for r in summary_rows)
    agg_mdd = float(min(r["max_drawdown_pct"] for r in summary_rows)) if summary_rows else 0.0
    pfs = [r["profit_factor"] for r in summary_rows if r["profit_factor"] != float("inf")]
    agg_pf = float(np.mean(pfs)) if pfs else 0.0
    agg_return = float(np.mean([r["total_return_pct"] for r in summary_rows])) if summary_rows else 0.0
    tag = "PROFITABLE" if agg_sharpe >= 1.0 else "NOT-PROFITABLE"

    agg = {
        "iteration": cfg["iteration"], "variant": cfg["variant"],
        "strategy_key": VARIANT_KEY, "date": cfg["date"],
        "timeframe": cfg["timeframe"], "instruments": cfg["instruments"],
        "axis": cfg["axis"], "tag": tag,
        "data_status": "MULTI-VENUE-DATA-MISSING microprice=binance_buy_share_proxy",
        "agg_sharpe_mean": agg_sharpe, "agg_n_trades_total": agg_n,
        "agg_mdd_worst": agg_mdd, "agg_profit_factor": agg_pf,
        "agg_return_pct": agg_return,
        "by_symbol": {r["symbol"]: {
            "sharpe": r["sharpe"], "mdd": r["max_drawdown_pct"],
            "n_trades": r["n_trades"], "win_rate": r["win_rate"],
            "profit_factor": r["profit_factor"],
            "total_return_pct": r["total_return_pct"],
        } for r in summary_rows},
    }
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(agg, indent=2, default=float))
    (RESULTS_DIR / "summary.json").write_text(json.dumps({
        "strategy": VARIANT_KEY, "iteration": cfg["iteration"],
        "variant": cfg["variant"], "date": cfg["date"],
        "timeframe": cfg["timeframe"], "instruments": cfg["instruments"],
        "axis": cfg["axis"], "tag": tag, "per_symbol": summary_rows,
    }, indent=2, default=str))

    for r in summary_rows:
        print(
            f"V3_xs_smart_routing ({VARIANT_KEY}) {r['symbol']} iter#{cfg['iteration']} -> "
            f"sharpe={r['sharpe']:.3f} mdd={r['max_drawdown_pct']:.4f} "
            f"n={r['n_trades']} tag={r['tag']} (data: binance taker-buy microprice proxy)"
        )
    print(
        f"V3_xs_smart_routing AGG -> sharpe={agg_sharpe:.3f} return={agg_return:.4f} "
        f"n={agg_n} mdd={agg_mdd:.4f} pf={agg_pf:.3f} tag={tag}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
