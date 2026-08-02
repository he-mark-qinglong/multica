#!/usr/bin/env python3
"""Run the cash-and-carry combo backtest and print the report.

Usage:
    python run_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy import CarryConfig, run_backtest

CFG_PATH = Path(__file__).parent / "config.json"


def verdict(metrics: dict, gates: dict) -> tuple[str, str]:
    """Relaxed evaluation: return + DD only."""
    ann = metrics["annualized_return"]
    dd = metrics["max_drawdown_pct"]
    calmar = metrics["calmar"]
    ok = (ann >= gates["min_annualized_return"]
          and dd >= gates["max_drawdown_floor"]
          and calmar >= gates["min_calmar"])
    return ("PASS" if ok else "FAIL"), f"ann={ann:+.2%} dd={dd:.2%} calmar={calmar:.2f}"


def main():
    raw = json.loads(CFG_PATH.read_text())
    gates = raw["evaluation"]["relaxed_gates"]

    cfg = CarryConfig.from_json(CFG_PATH)
    results = run_backtest(cfg)

    print("=" * 78)
    print("CASH-AND-CARRY COMBO v1 — delta-neutral funding harvest")
    print("=" * 78)
    print(f"Symbols: {', '.join(results['config']['symbols'])}  |  "
          f"Leverage: {results['config']['leverage']}x  |  "
          f"Filter: {', '.join(results['config']['filter_symbols'])}")

    print(f"\n{'symbol':<10} {'days':>6} {'events':>7} {'ann_ret':>9} {'maxDD':>9} {'calmar':>8} {'verdict':>8}")
    print("-" * 78)
    for sym, m in results["per_symbol"].items():
        v, detail = verdict(m, gates)
        star = " ★" if v == "PASS" else ""
        print(f"{sym:<10} {m['days']:>6} {m['n_events']:>7} "
              f"{m['annualized_return']:>8.2%} {m['max_drawdown_pct']:>8.2%} "
              f"{m['calmar']:>8.2f} {v:>7}{star}")

    print("-" * 78)
    c = results["combo"]
    v, _ = verdict(c, gates)
    print(f"{'COMBO':<10} {c['days']:>6} {'—':>7} "
          f"{c['annualized_return']:>8.2%} {c['max_drawdown_pct']:>8.2%} "
          f"{c['calmar']:>8.2f} {v:>7}")
    print(f"\nCombo total return: {c['total_return_pct']:+.1f}% over {c['days']} days")
    print(f"Relaxed gates: ann>={gates['min_annualized_return']:.0%}, "
          f"dd>={gates['max_drawdown_floor']:.0%}, calmar>={gates['min_calmar']}")

    out = Path(__file__).parent / "results.json"
    summary = {
        "config": results["config"],
        "per_symbol": results["per_symbol"],
        "combo": {k: v for k, v in c.items()},
        "verdict": v,
    }
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
