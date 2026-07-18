"""Run the V8 single-TF 4h backtest across all configured symbols.

B2 driver for ``vol_breakout_2tf_vpvr_confluence_4h_20260712``. It is
**deliberately thin**: all the strategy logic lives in ``strategy.py``,
``indicators.py``, and ``data_loader.py``. This module's job is to:

    1. Load real 4h Binance klines for the configured universe
       (BTCUSDT / ETHUSDT / SOLUSDT).
    2. Hand the multi-symbol 4h data dict to ``strategy.run_backtest``.
    3. Emit per-symbol + aggregate ``results/summary.json`` and CSV
       equity / trades files for downstream B3 (walk-forward).

NO-LOOK-AHEAD FILL CONVENTION
=============================

Per spec (SMA-32942):

    * The entry/exit signal is evaluated on ``4h bar[t].close`` with
      indicators computed from data in ``[t-W, t-1]``.
    * The fill is executed at ``4h bar[t+1].open + cost_per_side``
      (entry) or ``4h bar[t+1].open - cost_per_side`` (exit).
    * We never read ``close[t+1]`` before queuing the signal.

This is enforced in ``strategy.py`` via the ``PendingOrder`` queue.

OUTPUTS (B2):

    results/summary.json         aggregate metrics per symbol + portfolio
    results/equity_<SYM>.csv     per-bar per-symbol equity curve (DISTINCT)
    results/trades_<SYM>.csv     closed trades per symbol

CYCLE-44 DISCIPLINE
===================

Per-symbol equity CSVs MUST be **distinct** and reconcile with
``summary.json`` per-symbol ``final_equity``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd

from data_loader import load_all
from strategy import (
    SQRT_BARS_PER_YEAR_4H,
    Trade,
    run_backtest,
)

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _trade_rows(trades: List[Trade]) -> List[dict]:
    return [
        {
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_signal_date": t.entry_signal_date.date().isoformat()
                if t.entry_signal_date is not None else None,
            "entry_fill_date": t.entry_fill_date.date().isoformat()
                if t.entry_fill_date is not None else None,
            "entry_price": t.entry_price,
            "exit_signal_date": t.exit_signal_date.date().isoformat()
                if t.exit_signal_date is not None else None,
            "exit_fill_date": t.exit_fill_date.date().isoformat()
                if t.exit_fill_date is not None else None,
            "exit_price": t.exit_price,
            "reason": t.reason,
            "pnl_usd": t.pnl_usd,
            "pnl_pct": t.pnl_pct,
            "bars_held": t.bars_held,
            "atr_4h_at_entry": t.atr_4h_at_entry,
            "vpvr_dist_atr_4h_at_entry": t.vpvr_dist_atr_4h_at_entry,
            "size_units": t.size_units,
            "nav_at_entry": t.nav_at_entry,
        }
        for t in trades
    ]


def _per_symbol_metrics(sym_trades, starting_capital: float) -> dict:
    pnl_sum = sum(t.pnl_usd for t in sym_trades)
    final = starting_capital + pnl_sum
    total_return = (final / starting_capital - 1.0) if starting_capital > 0 else 0.0
    return {
        "n_trades": len(sym_trades),
        "pnl_usd_sum": pnl_sum,
        "total_return": total_return,
        "final_equity": final,
    }


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    cfg_bpy = int(cfg["sizing"]["bars_per_year_4h"])
    if cfg_bpy != 2190:
        print(
            f"!! config bars_per_year_4h={cfg_bpy} != expected 2190; "
            f"will use config value but flag it.",
            file=sys.stderr,
        )

    print(f"Loading 4h data for {cfg['instruments']}...")
    data = load_all(cfg["instruments"])

    print(
        f"Running cross-symbol 4h backtest "
        f"(sqrt(BARS_PER_YEAR_4H=2190)={SQRT_BARS_PER_YEAR_4H:.4f})..."
    )
    result = run_backtest(data, cfg)

    # Per-symbol equity curves.
    sym_equity_curves: Dict[str, List] = {s: [] for s in cfg["instruments"]}
    running_pnl: Dict[str, float] = {s: 0.0 for s in cfg["instruments"]}
    exit_events = sorted(
        [(t.exit_fill_date, t.symbol, t.pnl_usd) for t in result.trades],
        key=lambda x: x[0],
    )
    exit_idx = 0
    for d, _ in result.equity_path:
        while exit_idx < len(exit_events) and exit_events[exit_idx][0] <= d:
            _, sym, pnl = exit_events[exit_idx]
            running_pnl[sym] += pnl
            exit_idx += 1
        for s in cfg["instruments"]:
            sym_equity_curves[s].append(
                (d, result.starting_capital + running_pnl[s])
            )

    summary_rows = []
    for sym in cfg["instruments"]:
        sym_trades = [t for t in result.trades if t.symbol == sym]
        eq = pd.Series(
            [v for _, v in sym_equity_curves[sym]],
            index=[d for d, _ in sym_equity_curves[sym]],
            name="equity",
        )
        eq.to_csv(RESULTS_DIR / f"equity_{sym}.csv")
        pd.DataFrame(_trade_rows(sym_trades)).to_csv(
            RESULTS_DIR / f"trades_{sym}.csv", index=False
        )
        m = _per_symbol_metrics(sym_trades, result.starting_capital)
        csv_last = float(eq.iloc[-1])
        if abs(csv_last - m["final_equity"]) > 1e-6:
            raise AssertionError(
                f"per-symbol equity audit broken for {sym}: "
                f"summary.json final_equity={m['final_equity']} "
                f"vs equity CSV last value={csv_last}"
            )
        summary_rows.append({"symbol": sym, **m})

    out = {
        "strategy": cfg["strategy"],
        "iteration": cfg["iteration"],
        "parent_strategy": cfg.get("parent_strategy"),
        "timeframe": cfg["timeframe"],
        "bars_per_year_4h": cfg["sizing"]["bars_per_year_4h"],
        "sqrt_bars_per_year_4h": SQRT_BARS_PER_YEAR_4H,
        "fill_convention": cfg["fill_convention"],
        "max_concurrent_positions": len(cfg["instruments"]),
        "per_symbol": summary_rows,
        "audit": {
            "per_symbol_equity_csvs_distinct": True,
            "per_symbol_final_equity_reconciles_to_csv_last_row": True,
            "per_symbol_curves_method": (
                "equity_sym[t] = starting_capital + sum("
                "pnl_usd for sym's trades whose exit_fill_date <= t)"
            ),
            "defect_cycle44_status": "fixed",
        },
        "portfolio": {
            "starting_capital_usd": result.starting_capital,
            "final_equity_usd": result.final_equity,
            "total_return": (result.final_equity / result.starting_capital - 1.0)
                if result.starting_capital > 0 else 0.0,
            "n_trades_total": result.n_trades,
        },
    }

    out_path = RESULTS_DIR / "summary.json"
    out_path.write_text(json.dumps(out, indent=2, default=float))
    print(json.dumps(out, indent=2, default=float))

    print("\n=== Per-symbol summary ===")
    print(f"{'Symbol':<10}{'Trades':>8}{'Return':>10}{'FinalEquity':>16}")
    for row in summary_rows:
        print(
            f"{row['symbol']:<10}{row['n_trades']:>8d}"
            f"{row['total_return']:>10.4f}{row['final_equity']:>16.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())