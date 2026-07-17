"""Run V8 (vpvr_carry_term_8h_20260711, iter#72) per-symbol backtest."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np

from data_loader import load_all
from strategy import VARIANT_KEY, _annualisation_factor, run_backtest

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _summarise(res: dict, cfg: dict) -> dict:
    trades = res["trades"]
    n = len(trades)
    base = {
        "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "avg_hold_bars": 0.0, "total_return_pct": 0.0,
        "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0,
        "trades_per_year": 0.0,
    }
    if n == 0:
        return {**base, "tag": "NOT-PROFITABLE", "exit_reasons": {}}
    pnls = np.array([t.pnl_pct for t in trades], dtype=np.float64)
    wins = pnls > 0
    losses = pnls <= 0
    win_rate = float(wins.mean())
    gw = float(pnls[wins].sum()) if wins.any() else 0.0
    gl = float(-pnls[losses].sum()) if losses.any() else 0.0
    pf = gw / gl if gl > 0 else float("inf")
    avg_hold = float(np.mean([t.bars_held for t in trades]))
    equity = res["equity"]
    total = float(equity[-1] / float(cfg["starting_capital_usd"]) - 1.0)
    span_days = max((res["n_bars"]) * 8 / 24, 1)
    years = span_days / 365.25
    tpy = n / years if years > 0 else 0.0
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
    tag = "PROFITABLE" if sharpe >= 0.5 else "NOT-PROFITABLE"
    return {
        **base,
        "n_trades": n, "win_rate": win_rate, "profit_factor": float(pf),
        "avg_hold_bars": avg_hold, "total_return_pct": total,
        "sharpe": float(sharpe), "sortino": float(sortino),
        "max_drawdown_pct": max_dd, "trades_per_year": tpy,
        "tag": tag, "exit_reasons": exit_reasons,
    }


def _write_trades_csv(trades, p: Path) -> None:
    fields = [
        "variant", "symbol", "direction", "entry_ts", "entry_price",
        "exit_ts", "exit_price", "pnl_pct", "bars_held", "exit_reason",
        "funding_carry_pnl_pct", "spread_at_entry_bps",
    ]
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for t in trades:
            w.writerow({
                "variant": t.variant, "symbol": t.symbol, "direction": t.direction,
                "entry_ts": t.entry_ts, "entry_price": f"{t.entry_price:.6f}",
                "exit_ts": t.exit_ts, "exit_price": f"{t.exit_price:.6f}",
                "pnl_pct": f"{t.pnl_pct:.6f}", "bars_held": t.bars_held,
                "exit_reason": t.exit_reason,
                "funding_carry_pnl_pct": f"{t.funding_carry_pnl_pct:.6f}",
                "spread_at_entry_bps": f"{t.spread_at_entry_bps:.3f}",
            })


def _write_equity_csv(equity, p: Path) -> None:
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bar", "equity"])
        for i, e in enumerate(equity):
            w.writerow([i, f"{e:.6f}"])


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    data = load_all()
    if not data:
        print("no instruments loaded", file=sys.stderr)
        return 1

    summary_rows: List[dict] = []
    for sym, df in data.items():
        cfg_t = dict(cfg)
        cfg_t["_symbol"] = sym
        res = run_backtest(df, cfg_t)
        metrics = _summarise(res, cfg_t)
        metrics["symbol"] = sym
        metrics["span_start"] = res["span_start"]
        metrics["span_end"] = res["span_end"]
        metrics["n_bars"] = res["n_bars"]

        _write_trades_csv(res["trades"], RESULTS_DIR / f"trades_A_8h_{sym}.csv")
        _write_equity_csv(res["equity"], RESULTS_DIR / f"equity_8h_{sym}.csv")
        summary_rows.append(metrics)

    agg = {
        "iteration": cfg["iteration"], "variant": cfg["variant"],
        "strategy_key": VARIANT_KEY, "date": cfg["date"],
        "timeframe": cfg["timeframe"], "instruments": cfg["instruments"],
        "axis": cfg["axis"],
        "tag": summary_rows[0]["tag"] if summary_rows else "NOT-PROFITABLE",
        "by_symbol": {row["symbol"]: {
            "sharpe": row["sharpe"], "mdd": row["max_drawdown_pct"],
            "n_trades": row["n_trades"], "win_rate": row["win_rate"],
            "profit_factor": row["profit_factor"],
            "total_return_pct": row["total_return_pct"],
        } for row in summary_rows},
    }
    sharpes = [v["sharpe"] for v in agg["by_symbol"].values()]
    agg["agg_sharpe_mean"] = float(np.mean(sharpes)) if sharpes else 0.0
    mdds = [v["mdd"] for v in agg["by_symbol"].values()]
    agg["agg_mdd_worst"] = float(min(mdds)) if mdds else 0.0
    agg["agg_n_trades_total"] = sum(v["n_trades"] for v in agg["by_symbol"].values())

    (RESULTS_DIR / "metrics.json").write_text(json.dumps(agg, indent=2, default=float))
    (RESULTS_DIR / "summary.json").write_text(json.dumps({
        "strategy": VARIANT_KEY, "iteration": cfg["iteration"], "variant": cfg["variant"],
        "date": cfg["date"], "timeframe": cfg["timeframe"],
        "instruments": cfg["instruments"], "axis": cfg["axis"],
        "tag": agg["tag"], "per_symbol": summary_rows,
    }, indent=2, default=str))
    for row in summary_rows:
        print(
            f"V8 ({VARIANT_KEY}) {row['symbol']} iter#{cfg['iteration']} -> "
            f"sharpe={row['sharpe']:.3f} mdd={row['max_drawdown_pct']:.3f} "
            f"n={row['n_trades']} tag={row['tag']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())