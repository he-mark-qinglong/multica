"""Run V10 (vol_breakout_vpvr_val_fade_1h_5m_20260714, iter#74) backtest on BTCUSDT.

Outputs:
    results/v10/metrics.json     - aggregate metrics across the full sample
    results/v10/summary.json    - per-symbol row + headline verdict
    results/v10/equity_<sym>.csv - bar-indexed equity curve (5m bars)
    results/v10/trades_<sym>.csv - per-trade ledger
    results/v10/walk_forward.json - walk-forward OOS folds

Multi-TF note: the input frame carries `higher_ema_50` and `vpvr_val`
columns merged from the 1h frame (see data_loader.py). This script
operates only on the 5m stream.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path
from typing import Dict

import numpy as np

from data_loader import load_symbol
from strategy import run_backtest as run_strategy_backtest
import walk_forward as wf_mod

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
RESULTS_DIR = ROOT / "results" / "v10"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

ITERATION = 74
VARIANT = "V10"


def _bars_per_year(timeframe: str) -> int:
    tf = timeframe.strip().lower()
    if tf.endswith("m"):
        m = int(tf[:-1])
        return int(365.25 * 24 * 60 / m)
    if tf.endswith("h"):
        h = int(tf[:-1])
        return int(365.25 * 24 / h)
    if tf.endswith("d"):
        d = int(tf[:-1])
        return int(365.25 / d)
    raise ValueError(tf)


def _write_trades_csv(trades, p: Path) -> None:
    fields = [
        "variant", "symbol", "direction", "entry_signal_date", "entry_fill_date",
        "entry_price", "exit_signal_date", "exit_fill_date", "exit_price",
        "pnl_usd", "pnl_pct", "bars_held", "exit_reason",
        "val_pierce_atr", "vol_mult_at_entry",
    ]
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for t in trades:
            w.writerow({
                "variant": VARIANT,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_signal_date": str(t.entry_signal_date),
                "entry_fill_date": str(t.entry_fill_date),
                "entry_price": f"{t.entry_price:.6f}",
                "exit_signal_date": "" if t.exit_signal_date is None else str(t.exit_signal_date),
                "exit_fill_date": str(t.exit_fill_date),
                "exit_price": f"{t.exit_price:.6f}",
                "pnl_usd": f"{t.pnl_usd:.6f}",
                "pnl_pct": f"{t.pnl_pct:.6f}",
                "bars_held": t.bars_held,
                "exit_reason": t.reason,
                "val_pierce_atr": f"{t.val_pierce_atr:.4f}",
                "vol_mult_at_entry": f"{t.vol_mult_at_entry:.4f}",
            })


def _write_equity_csv(equity: np.ndarray, p: Path) -> None:
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bar", "equity"])
        for i, e in enumerate(equity):
            w.writerow([i, f"{e:.6f}"])


def _gate_row(metrics: Dict) -> Dict:
    """Hard gates from issue spec."""
    g1 = metrics["sharpe"] >= 1.0
    g2 = metrics["annualised_pct"] >= 0.15
    g3 = metrics["profit_factor"] > 1.5
    g4 = metrics["max_drawdown_pct"] > -0.25
    return {
        "g1_sharpe": bool(g1),
        "g2_annualized": bool(g2),
        "g3_profit_factor": bool(g3),
        "g4_max_drawdown": bool(g4),
    }


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    df = load_symbol(refresh=False)  # cache hit expected after data_loader.main()
    print(f"loaded 5m frame: shape={df.shape}, range={df.index.min()} -> {df.index.max()}")

    sym = "BTCUSDT"
    bars_per_year = _bars_per_year(cfg["timeframe"])
    res = run_strategy_backtest(
        df,
        cfg=cfg["params"],
        initial_capital=cfg["starting_capital_usd"],
        fee_bps=cfg["fees_bps_per_side"],
        slippage_bps=cfg["slippage_bps_per_side"],
        position_size_pct=cfg["position_size_pct"],
        bars_per_year=bars_per_year,
        max_hold_bars=cfg["exit"]["max_hold_bars"],
    )
    metrics = {k: v for k, v in res.items() if k not in {"trades", "equity_curve"}}
    metrics["symbol"] = sym
    metrics["span_start"] = res["span_start"]
    metrics["span_end"] = res["span_end"]
    metrics["n_bars"] = res["n_bars"]
    gates = _gate_row(metrics)
    metrics.update(gates)

    # Walk-forward OOS validation.
    print("\nrunning walk-forward validation...")
    wf_summary = wf_mod.walk_forward(
        df,
        cfg=cfg["params"],
        initial_capital=cfg["starting_capital_usd"],
        fee_bps=cfg["fees_bps_per_side"],
        slippage_bps=cfg["slippage_bps_per_side"],
        position_size_pct=cfg["position_size_pct"],
        bars_per_year=bars_per_year,
        max_hold_bars=cfg["exit"]["max_hold_bars"],
        n_splits=5,
        train_pct=0.6,
    )

    # Persist per-symbol artifacts.
    _write_trades_csv(res["trades"], RESULTS_DIR / f"trades_{sym}.csv")
    _write_equity_csv(res["equity_curve"], RESULTS_DIR / f"equity_{sym}.csv")

    # metrics.json — machine-readable summary
    metrics_out = {
        "iteration": ITERATION,
        "variant": VARIANT,
        "strategy_key": cfg["strategy"],
        "date": cfg["date"],
        "timeframe": cfg["timeframe"],
        "filter_timeframe": cfg.get("filter_timeframe"),
        "instruments": cfg["instruments"],
        "axis": cfg["axis"],
        "starting_capital_usd": cfg["starting_capital_usd"],
        "fees_bps_per_side": cfg["fees_bps_per_side"],
        "slippage_bps_per_side": cfg["slippage_bps_per_side"],
        "by_symbol": {
            sym: {k: metrics[k] for k in (
                "sharpe", "annualised_pct", "profit_factor", "max_drawdown_pct",
                "n_trades", "win_rate", "total_return_pct", "n_wins", "n_losses",
                "g1_sharpe", "g2_annualized", "g3_profit_factor", "g4_max_drawdown",
            )}
        },
        "walk_forward": wf_summary,
    }
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(metrics_out, indent=2, default=float))

    # summary.json — human-friendly verdict
    oos_sharpe_mean = wf_summary["oos_sharpe_mean"] if "oos_sharpe_mean" in wf_summary else 0.0
    oos_sharpe_min = wf_summary["oos_sharpe_min"] if "oos_sharpe_min" in wf_summary else 0.0
    oos_positive_folds = wf_summary["oos_positive_folds"] if "oos_positive_folds" in wf_summary else 0
    n_folds = wf_summary["n_folds"] if "n_folds" in wf_summary else 0
    summary_verdict = "PROFITABLE" if all(gates.values()) and oos_sharpe_mean >= 1.0 else "NOT-PROFITABLE"
    summary = {
        "iteration": ITERATION,
        "variant": VARIANT,
        "strategy_key": cfg["strategy"],
        "date": cfg["date"],
        "timeframe": cfg["timeframe"],
        "filter_timeframe": cfg.get("filter_timeframe"),
        "instruments": cfg["instruments"],
        "axis": cfg["axis"],
        "verdict": summary_verdict,
        "per_symbol": {
            sym: {k: metrics[k] for k in (
                "sharpe", "annualised_pct", "profit_factor", "max_drawdown_pct",
                "n_trades", "win_rate", "total_return_pct", "n_wins", "n_losses",
                "bars_per_year", "span_start", "span_end", "n_bars",
                "g1_sharpe", "g2_annualized", "g3_profit_factor", "g4_max_drawdown",
            )}
        },
        "walk_forward_summary": {
            "n_folds": n_folds,
            "oos_sharpe_mean": oos_sharpe_mean,
            "oos_sharpe_median": wf_summary.get("oos_sharpe_median", 0.0),
            "oos_sharpe_min": oos_sharpe_min,
            "oos_positive_folds": oos_positive_folds,
        },
    }
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    # Console headline.
    print(f"\n{VARIANT} ({cfg['strategy']}) {sym} iter#{ITERATION} -> "
          f"sharpe={metrics['sharpe']:.3f} ann={metrics['annualised_pct']:.3f} "
          f"pf={metrics['profit_factor']:.3f} mdd={metrics['max_drawdown_pct']:.3f} "
          f"n={metrics['n_trades']} oos_sharpe_mean={oos_sharpe_mean:.3f} "
          f"verdict={summary_verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
