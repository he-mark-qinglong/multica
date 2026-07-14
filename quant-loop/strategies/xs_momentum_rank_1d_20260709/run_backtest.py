"""Runner for the cross-sectional momentum rank 1d backtest.

Loads the active universe from the local 1d parquet caches, runs the
backtest, and writes the canonical artifacts to ``results/``:

    - summary.json                   -- machine-readable verdict + best metrics
    - equity_curve.csv               -- full-period equity, 1 row per rebalance
    - gross_schedule.csv             -- gross exposure schedule
    - turnover_schedule.csv          -- per-rebalance turnover fraction
    - factor_exposure.json           -- per-symbol average weight & contribution
    - rebalance_log.csv              -- one row per rebalance with target portfolio

CLI:
    PYTHONPATH=. python3 run_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import result_summary_dict, run_backtest
from data_loader import load_all
from universe import load_universe_config

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"


def _avg_weight_contribution(events) -> dict:
    """Per-symbol average absolute weight and a long/short attribution."""
    long_w: dict = {}
    short_w: dict = {}
    for ev in events:
        for p in ev.target_positions:
            key = p.symbol
            if p.side == "LONG":
                long_w[key] = long_w.get(key, 0.0) + abs(p.weight)
            else:
                short_w[key] = short_w.get(key, 0.0) + abs(p.weight)
    n = max(len(events), 1)
    avg_long = {s: w / n for s, w in long_w.items()}
    avg_short = {s: w / n for s, w in short_w.items()}
    return {"long": avg_long, "short": avg_short}


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG_PATH.read_text())
    universe_cfg = load_universe_config()
    per_symbol_dfs = load_all(symbols=list(universe_cfg.active))
    if not per_symbol_dfs:
        print("No active symbols loaded -- check data_loader.", file=sys.stderr)
        return 1
    print(
        f"Loaded {len(per_symbol_dfs)} symbol(s): "
        f"{sorted(per_symbol_dfs.keys())}",
    )

    result = run_backtest(per_symbol_dfs, cfg=cfg, universe_cfg=universe_cfg)

    # Summary JSON.
    summary = result_summary_dict(result)
    summary["active_universe"] = sorted(per_symbol_dfs.keys())
    summary["target_universe"] = list(universe_cfg.target)
    summary["active_vs_target"] = {
        "target_n": len(universe_cfg.target),
        "active_n": len(per_symbol_dfs),
        "missing": [s for s in universe_cfg.target if s not in per_symbol_dfs],
    }
    summary["factor_exposure"] = _avg_weight_contribution(result.events)
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    # Equity curve.
    if not result.equity_curve.empty:
        result.equity_curve.to_frame("equity").to_csv(RESULTS_DIR / "equity_curve.csv")
        result.gross_series.to_frame("gross").to_csv(RESULTS_DIR / "gross_schedule.csv")
        result.turnover_series.to_frame("turnover").to_csv(RESULTS_DIR / "turnover_schedule.csv")
    (RESULTS_DIR / "factor_exposure.json").write_text(
        json.dumps(summary["factor_exposure"], indent=2, default=str)
    )

    # Rebalance log.
    rows = []
    for ev in result.events:
        longs = ";".join(
            f"{p.symbol}:{p.weight:.4f}" for p in ev.target_positions if p.side == "LONG"
        )
        shorts = ";".join(
            f"{p.symbol}:{p.weight:.4f}" for p in ev.target_positions if p.side == "SHORT"
        )
        rows.append({
            "date": ev.date.date().isoformat(),
            "n_longs": sum(1 for p in ev.target_positions if p.side == "LONG"),
            "n_shorts": sum(1 for p in ev.target_positions if p.side == "SHORT"),
            "longs": longs,
            "shorts": shorts,
            "gross": ev.gross,
            "turnover": ev.turnover,
            "notes": ev.notes,
        })
    pd.DataFrame(rows).to_csv(RESULTS_DIR / "rebalance_log.csv", index=False)

    # Console summary.
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())