"""Run V3 (xs-pair z-score + VPVR confluence, iter#75) per pair + combined."""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data_loader import load_all
from strategy import VARIANT_KEY, _annualisation_factor, run_backtest

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
DATA_DIR = Path(__file__).resolve().parent / "data"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _summarise_pair(pair_result: dict, cfg: dict) -> dict:
    trades = pair_result["trades"]
    n_trades = len(trades)
    equity = pair_result["equity"]
    if n_trades == 0:
        return {
            "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "avg_hold_bars": 0.0, "total_return_pct": 0.0,
            "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0,
            "trades_per_year": 0.0,
        }
    pnls = np.array([t["pnl_pct"] for t in trades], dtype=np.float64)
    wins = pnls > 0
    losses = pnls <= 0
    win_rate = float(wins.mean())
    gw = float(pnls[wins].sum()) if wins.any() else 0.0
    gl = float(-pnls[losses].sum()) if losses.any() else 0.0
    pf = gw / gl if gl > 0 else float("inf")
    avg_hold = float(np.mean([t["bars_held"] for t in trades]))
    total = float(equity[-1] / float(cfg["starting_capital_usd"]) - 1.0)
    bar_r = pair_result["bar_return"][1:]
    ann = _annualisation_factor(cfg["timeframe"])
    mu = float(np.mean(bar_r))
    sigma = float(np.std(bar_r, ddof=0))
    sharpe = (mu / sigma) * ann if sigma > 0 else 0.0
    ds = bar_r[bar_r < 0]
    dsigma = float(np.std(ds, ddof=0)) if ds.size else 0.0
    sortino = (mu / dsigma) * ann if dsigma > 0 else 0.0
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    max_dd = float(dd.min())
    span_days = max(pair_result["n_bars"] * 4 / 24, 1)
    years = span_days / 365.25
    tpy = n_trades / years if years > 0 else 0.0
    return {
        "n_trades": n_trades, "win_rate": win_rate, "profit_factor": float(pf),
        "avg_hold_bars": avg_hold, "total_return_pct": total,
        "sharpe": float(sharpe), "sortino": float(sortino),
        "max_drawdown_pct": max_dd, "trades_per_year": tpy,
    }


def _write_trades(trades, p):
    fields = ["variant", "pair", "direction", "entry_ts", "entry_price_a",
              "entry_price_b", "exit_ts", "exit_price_a", "exit_price_b",
              "pnl_pct", "bars_held", "z_at_entry", "z_at_exit", "exit_reason"]
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for t in trades:
            w.writerow(t)


def _write_equity(equity, idx, p):
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "equity"])
        for ts, eq in zip(idx, equity):
            w.writerow([ts.isoformat(), f"{eq:.6f}"])


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    instruments = list(cfg["instruments"])
    data = load_all(instruments)
    res = run_backtest(data, cfg)
    per_pair_results = res["per_pair"]

    per_pair_metrics = []
    for pr in per_pair_results:
        label = pr["pair"].replace("/", "_")
        m = _summarise_pair(pr, cfg)
        m["pair"] = pr["pair"]
        m["span_start"] = pr["span_start"]
        m["span_end"] = pr["span_end"]
        m["n_bars"] = pr["n_bars"]
        per_pair_metrics.append(m)
        _write_trades(pr["trades"], RESULTS_DIR / f"trades_{VARIANT_KEY}_iter{cfg['iteration']}_{label}.csv")
        # equity uses the original (per-pair) df index; we approximate using common span
        equity_idx = pd.date_range(pr["span_start"], periods=len(pr["equity"]), freq="4h")
        _write_equity(pr["equity"], equity_idx,
                      RESULTS_DIR / f"equity_{VARIANT_KEY}_iter{cfg['iteration']}_{label}.csv")

    avg_sharpe = float(np.mean([m["sharpe"] for m in per_pair_metrics])) if per_pair_metrics else 0.0
    avg_return = float(np.mean([m["total_return_pct"] for m in per_pair_metrics])) if per_pair_metrics else 0.0
    avg_mdd = float(np.mean([m["max_drawdown_pct"] for m in per_pair_metrics])) if per_pair_metrics else 0.0
    avg_pf = float(np.mean([m["profit_factor"] for m in per_pair_metrics])) if per_pair_metrics else 0.0
    avg_win = float(np.mean([m["win_rate"] for m in per_pair_metrics])) if per_pair_metrics else 0.0
    n_total = sum(m["n_trades"] for m in per_pair_metrics)
    tag = "PROFITABLE" if avg_sharpe >= 0.5 else "NOT-PROFITABLE"

    summary = {
        "strategy": cfg["strategy"],
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "timeframe": cfg["timeframe"],
        "instruments": instruments,
        "pairs": cfg["pairs"],
        "axis": cfg["axis"],
        "tag": tag,
        "variant": VARIANT_KEY,
        "n_trades": n_total,
        "win_rate": avg_win,
        "profit_factor": avg_pf,
        "total_return_pct": avg_return,
        "sharpe": avg_sharpe,
        "max_drawdown_pct": avg_mdd,
        "evidence_gate": {"sharpe_threshold": 0.5, "sharpe_observed": avg_sharpe, "passed": avg_sharpe >= 0.5},
        "params": cfg["indicators"],
        "per_pair": {m["pair"]: m for m in per_pair_metrics},
    }
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    (RESULTS_DIR / "metrics.json").write_text(json.dumps({
        "strategy": cfg["strategy"], "iteration": cfg["iteration"], "date": cfg["date"],
        "timeframe": cfg["timeframe"], "instruments": instruments, "pairs": cfg["pairs"],
        "variant": VARIANT_KEY,
        "n_trades": n_total, "win_rate": avg_win, "profit_factor": avg_pf,
        "total_return_pct": avg_return, "sharpe": avg_sharpe, "max_drawdown_pct": avg_mdd,
        "tag": tag, "evidence_gate_sharpe_threshold": 0.5,
        "per_pair": {m["pair"]: m for m in per_pair_metrics},
    }, indent=2))
    print(f"=== {cfg['strategy']} (iter {cfg['iteration']}, {cfg['timeframe']}) ===")
    print(f"tag           : [{tag}]  (gate: avg sharpe >= 0.5)")
    print(f"pairs         : {cfg['pairs']}")
    print(f"total trades  : {n_total}")
    print(f"avg sharpe    : {avg_sharpe:.3f}")
    print(f"avg return    : {avg_return:.4f}")
    print(f"avg mdd       : {avg_mdd:.4f}")
    print(f"avg profit_f  : {avg_pf:.3f}")
    for m in per_pair_metrics:
        print(f"  {m['pair']:<22} trades={m['n_trades']:>5} sharpe={m['sharpe']:.3f} "
              f"return={m['total_return_pct']:.4f} mdd={m['max_drawdown_pct']:.4f} "
              f"pf={m['profit_factor']:.3f} win={m['win_rate']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())