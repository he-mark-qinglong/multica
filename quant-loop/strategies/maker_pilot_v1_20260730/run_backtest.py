#!/usr/bin/env python3
"""Run the maker pilot backtest and evaluate against gate system.

Usage:
    python run_backtest.py [--gate]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Import strategy runner
sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategy import run

# Gate evaluator
from _shared.gates.enforce import certify_metrics


def main():
    print("Loading data and running maker simulator...")
    metrics = run()
    trades = metrics.pop("trades", [])
    config = metrics.pop("config", {})

    # Print summary
    print(f"\n{'='*60}")
    print(f"Maker Pilot v1 — Results Summary")
    print(f"{'='*60}")
    print(f"  Round-trips:     {metrics.get('n_trades', 0)}")
    print(f"  Trades scanned:  {metrics.get('n_trades_in', 0):,}")
    print(f"  Quotes generated:{metrics.get('quotes_generated', 0):,}")
    print(f"  Quotes filled:   {metrics.get('quotes_filled', 0):,}")
    print(f"  Fill rate:       {metrics.get('fill_rate', 0):.2%}")
    print(f"  Maker ratio:     {metrics.get('maker_ratio', 0):.2%}")
    print(f"  ---")
    print(f"  Sharpe (daily):  {metrics.get('sharpe_daily', 0):.2f}")
    print(f"  Ann return:      {metrics.get('annualized_return', 0):.2%}")
    print(f"  Max DD:          {metrics.get('max_drawdown_pct', 0):.2%}")
    print(f"  Profit factor:   {metrics.get('profit_factor', 0):.2f}")
    print(f"  Avg pnl (bp):    {metrics.get('avg_pnl_bp', 0):.2f}")
    print(f"  Win rate:        {metrics.get('win_rate', 0):.2%}")
    print(f"  Exit reasons:    {metrics.get('exit_reasons', {})}")
    print(f"  Elapsed:         {metrics.get('elapsed_seconds', 0):.1f}s")

    # Gate evaluation
    gate_thresholds = config.get("gate_thresholds", {})
    if gate_thresholds:
        print(f"\n{'='*60}")
        print("Gate Evaluation (G1-G7 + T1)")
        print(f"{'='*60}")

        gate_result = certify_metrics(metrics, strict=False)
        if gate_result.passed:
            print("  ✅ ALL GATES PASSED — strategy is LIVE-eligible")
        else:
            print(f"  ❌ {len(gate_result.failed_gates)} gate(s) FAILED:")
            for reason in gate_result.reasons:
                print(f"     • {reason}")

    # Save results
    out_path = Path(__file__).parent / "results.json"
    serializable = {k: v for k, v in metrics.items()
                    if isinstance(v, (int, float, str, bool, dict, list, type(None)))}
    with open(out_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
