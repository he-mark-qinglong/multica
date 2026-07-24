"""Run per-symbol backtest for vpvr_funding_aware_v1_20260711 (iter#82)."""

from __future__ import annotations

import csv
import json
import math
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


def _summarise(trades: List, equity: np.ndarray, starting_cap: float,
               n_bars: int, timeframe: str) -> dict:
    base = {
        "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "avg_hold_bars": 0.0, "total_return_pct": 0.0,
        "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0,
        "trades_per_year": 0.0,
    }
    n = len(trades)
    if n == 0:
        return {**base, "tag": "NOT-PROFITABLE", "exit_reasons": {},
                "annualised_return_pct": 0.0}
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
    ann_return = (1.0 + total) ** (1.0 / years) - 1.0
    mu_t = float(np.mean(pnls))
    sd_t = float(np.std(pnls, ddof=0))
    sharpe = (mu_t / sd_t) * math.sqrt(_bars_per_year(timeframe)) if sd_t > 0 else 0.0
    ds = pnls[pnls < 0]
    dsigma = float(np.std(ds, ddof=0)) if ds.size else 0.0
    sortino = (mu_t / dsigma) * math.sqrt(_bars_per_year(timeframe)) if dsigma > 0 else 0.0
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    max_dd = float(dd.min())
    exit_reasons: Dict[str, int] = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
    tag = "PROFITABLE" if sharpe >= 1.0 else "NOT-PROFITABLE"
    return {
        **base,
        "n_trades": n, "win_rate": win_rate, "profit_factor": float(pf),
        "avg_hold_bars": avg_hold, "total_return_pct": total,
        "annualised_return_pct": float(ann_return),
        "sharpe": float(sharpe), "sortino": float(sortino),
        "max_drawdown_pct": max_dd, "trades_per_year": tpy,
        "tag": tag, "exit_reasons": exit_reasons,
    }


def _write_trades_csv(trades: List, p: Path) -> None:
    fields = [
        "variant", "symbol", "direction", "entry_ts", "entry_price",
        "exit_ts", "exit_price", "pnl_pct", "pnl_price_pct", "pnl_carry_pct",
        "bars_held", "exit_reason",
        "funding_sum_24h_bps_at_entry", "funding_vol_bps_at_entry",
        "cum_carry_pct_at_exit",
    ]
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for t in trades:
            w.writerow({
                "variant": t.variant, "symbol": t.symbol, "direction": t.direction,
                "entry_ts": t.entry_ts, "entry_price": f"{t.entry_price:.6f}",
                "exit_ts": t.exit_ts, "exit_price": f"{t.exit_price:.6f}",
                "pnl_pct": f"{t.pnl_pct:.6f}",
                "pnl_price_pct": f"{t.pnl_price_pct:.6f}",
                "pnl_carry_pct": f"{t.pnl_carry_pct:.6f}",
                "bars_held": t.bars_held, "exit_reason": t.exit_reason,
                "funding_sum_24h_bps_at_entry": f"{t.funding_sum_24h_bps_at_entry:.3f}",
                "funding_vol_bps_at_entry": f"{t.funding_vol_bps_at_entry:.3f}",
                "cum_carry_pct_at_exit": f"{t.cum_carry_pct_at_exit:.6f}",
            })


def _write_equity_csv(equity: np.ndarray, p: Path) -> None:
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bar", "equity"])
        for i, e in enumerate(equity):
            w.writerow([i, f"{e:.6f}"])


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    data = load_all()
    if not data:
        print("no instruments loaded", file=sys.stderr); return 1

    summary_rows: List[dict] = []
    starting_cap_total = float(cfg["starting_capital_usd"])
    starting_cap_per = float(cfg.get("starting_capital_per_symbol_usd",
                                     starting_cap_total / len(cfg["instruments"])))
    timeframe = cfg["timeframe"]
    portfolio_pnl_usd = 0.0
    portfolio_starting = 0.0
    portfolio_n = 0

    for sym, df in data.items():
        cfg_t = dict(cfg); cfg_t["instruments"] = [sym]
        cfg_t["starting_capital_per_symbol_usd"] = starting_cap_per
        cfg_t["_symbol"] = sym
        res = run_backtest(df, cfg_t)
        m = _summarise(res["trades"], res["equity"], starting_cap_per,
                       res["n_bars"], timeframe)
        m["symbol"] = sym
        m["span_start"] = res["span_start"]
        m["span_end"] = res["span_end"]
        m["n_bars"] = res["n_bars"]
        m["pnl_usd_sum"] = float(sum(t.pnl_pct for t in res["trades"]) * starting_cap_per)
        m["pnl_usd_price_sum"] = float(sum(t.pnl_price_pct for t in res["trades"]) * starting_cap_per)
        m["pnl_usd_carry_sum"] = float(sum(t.pnl_carry_pct for t in res["trades"]) * starting_cap_per)
        m["final_equity"] = float(res["equity"][-1])
        _write_trades_csv(res["trades"], RESULTS_DIR / f"trades_A_4h_{sym}.csv")
        _write_equity_csv(res["equity"], RESULTS_DIR / f"equity_4h_{sym}.csv")
        summary_rows.append(m)
        portfolio_pnl_usd += m["pnl_usd_sum"]
        portfolio_starting += starting_cap_per
        portfolio_n += m["n_trades"]

    agg_sharpe = float(np.mean([r["sharpe"] for r in summary_rows])) if summary_rows else 0.0
    agg_mdd = float(min(r["max_drawdown_pct"] for r in summary_rows)) if summary_rows else 0.0
    pfs = [r["profit_factor"] for r in summary_rows if r["profit_factor"] != float("inf")]
    agg_pf = float(np.mean(pfs)) if pfs else 0.0
    agg_return = float(np.mean([r["total_return_pct"] for r in summary_rows])) if summary_rows else 0.0
    agg_ann_return = float(np.mean([r["annualised_return_pct"] for r in summary_rows])) if summary_rows else 0.0
    portfolio_total_return = portfolio_pnl_usd / portfolio_starting if portfolio_starting else 0.0
    portfolio_final_equity = portfolio_starting + portfolio_pnl_usd
    tag = "PROFITABLE" if agg_sharpe >= 1.0 else "NOT-PROFITABLE"

    summary_doc = {
        "strategy": VARIANT_KEY,
        "strategy_key": VARIANT_KEY,
        "variant": cfg.get("variant", "?"),
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "timeframe": cfg["timeframe"],
        "bars_per_year": int(cfg.get("bars_per_year_4h", _bars_per_year(cfg["timeframe"]) * 1)),
        "sqrt_bars_per_year": float(cfg.get("annualisation_factor_4h_sqrt",
                                            math.sqrt(_bars_per_year(cfg["timeframe"])))),
        "fill_convention": cfg.get("fill_convention"),
        "funding_carry_model": cfg.get("funding_carry_model"),
        "direction": cfg.get("direction"),
        "max_concurrent_positions": len(cfg["instruments"]),
        "per_symbol": summary_rows,
        "portfolio": {
            "starting_capital_usd": float(portfolio_starting),
            "final_equity_usd": float(portfolio_final_equity),
            "total_return": float(portfolio_total_return),
            "n_trades_total": int(portfolio_n),
        },
        "archived": tag == "NOT-PROFITABLE",
        "archive_reason": None,
        "tag": tag,
    }
    if tag == "NOT-PROFITABLE":
        summary_doc["archive_reason"] = (
            f"agg_sharpe={agg_sharpe:.3f}, ann_return={agg_ann_return:.4%}, "
            f"mdd={agg_mdd:.4%}, pf={agg_pf:.3f}, n_trades={portfolio_n}; "
            "fails G1 (Sharpe>=1)."
        )

    (RESULTS_DIR / "summary.json").write_text(
        json.dumps(summary_doc, indent=2, default=str))

    metrics_doc = {
        "iteration": cfg["iteration"],
        "variant": cfg.get("variant", "?"),
        "strategy_key": VARIANT_KEY,
        "date": cfg["date"],
        "timeframe": cfg["timeframe"],
        "instruments": cfg["instruments"],
        "axis": cfg.get("axis"),
        "tag": tag,
        "agg_sharpe_mean": agg_sharpe,
        "agg_annualised_return_pct": agg_ann_return,
        "agg_return_pct": agg_return,
        "agg_mdd_worst": agg_mdd,
        "agg_profit_factor": agg_pf,
        "agg_n_trades_total": int(portfolio_n),
        "by_symbol": {r["symbol"]: {
            "sharpe": r["sharpe"], "sortino": r["sortino"], "mdd": r["max_drawdown_pct"],
            "n_trades": r["n_trades"], "win_rate": r["win_rate"],
            "profit_factor": r["profit_factor"],
            "total_return_pct": r["total_return_pct"],
            "annualised_return_pct": r["annualised_return_pct"],
            "exit_reasons": r["exit_reasons"],
            "final_equity": r["final_equity"],
        } for r in summary_rows},
    }
    (RESULTS_DIR / "metrics.json").write_text(
        json.dumps(metrics_doc, indent=2, default=float))

    for r in summary_rows:
        print(
            f"V8_rev2 ({VARIANT_KEY}) {r['symbol']} iter#{cfg['iteration']} -> "
            f"sharpe={r['sharpe']:.3f} mdd={r['max_drawdown_pct']:.4f} "
            f"n={r['n_trades']} tag={r['tag']}"
        )
    print(
        f"V8_rev2 AGG -> sharpe={agg_sharpe:.3f} return={agg_return:.4f} "
        f"ann_return={agg_ann_return:.4%} n={portfolio_n} mdd={agg_mdd:.4f} "
        f"pf={agg_pf:.3f} tag={tag}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
