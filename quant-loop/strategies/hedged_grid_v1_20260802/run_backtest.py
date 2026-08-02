#!/usr/bin/env python3
"""Run the hedged-grid v1 backtest and print the report.

Usage:
    python run_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy import GridConfig, run_backtest

CFG_PATH = Path(__file__).parent / "config.json"


def verdict(metrics: dict, min_calmar: float) -> tuple[str, str]:
    """Gate: Calmar >= min_calmar."""
    calmar = metrics["calmar"]
    ok = calmar >= min_calmar
    return ("PASS" if ok else "FAIL"), f"calmar={calmar:.2f} (gate {min_calmar})"


def main():
    raw = json.loads(CFG_PATH.read_text())
    min_calmar = raw["evaluation"]["min_calmar"]
    proto = raw["evaluation"].get("prototype_reference_calmar", {})

    cfg = GridConfig.from_json(CFG_PATH)
    results = run_backtest(cfg)

    print("=" * 84)
    print("HEDGED GRID v1 — ER-gated range grid, perp-hedged inventory")
    print("=" * 84)
    print(f"Symbols: {', '.join(results['config']['symbols'])}  |  "
          f"Capital: {results['config']['initial_capital']:,.0f}")

    header = (f"{'symbol':<10} {'days':>6} {'ann_ret':>9} {'maxDD':>9} "
              f"{'calmar':>7} {'proto':>6} {'grid_tr':>8} {'rebal':>6} "
              f"{'fund$':>8} {'verdict':>8}")
    print(f"\n{header}")
    print("-" * 84)
    n_pass = 0
    for sym, m in results["per_symbol"].items():
        v, _ = verdict(m, min_calmar)
        n_pass += v == "PASS"
        p = proto.get(sym)
        print(f"{sym:<10} {m['days']:>6.0f} {m['annualized_return']:>8.2%} "
              f"{m['max_drawdown_pct']:>8.2%} {m['calmar']:>7.2f} "
              f"{p if p is not None else '—':>6} "
              f"{m['n_grid_trades']:>8} {m['n_rebalances']:>6} "
              f"{m['funding_received']:>8.1f} {v:>8}")

    print("-" * 84)
    c = results["combo"]
    v, _ = verdict(c, min_calmar)
    print(f"{'COMBO':<10} {c['days']:>6.0f} {c['annualized_return']:>8.2%} "
          f"{c['max_drawdown_pct']:>8.2%} {c['calmar']:>7.2f} {'—':>6} "
          f"{'—':>8} {'—':>6} {'—':>8} {v:>8}")
    print(f"\nCombo total return: {c['total_return']:+.1%} over {c['days']:.0f} days")
    print(f"Gate: Calmar >= {min_calmar}  →  {n_pass}/{len(results['per_symbol'])} symbols + combo {'PASS' if v == 'PASS' else 'FAIL'}")

    out = Path(__file__).parent / "results.json"
    summary = {
        "config": results["config"],
        "per_symbol": results["per_symbol"],
        "combo": results["combo"],
        "verdict": v,
    }
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
