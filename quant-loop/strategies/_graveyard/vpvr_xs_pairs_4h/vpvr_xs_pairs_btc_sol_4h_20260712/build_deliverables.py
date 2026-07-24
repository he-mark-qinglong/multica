"""Build the params_optimized.json + walk_forward_optimized.json deliverables
from results/cpcv_sweep.json for vpvr_xs_pairs_btc_sol_4h_20260712.

Per the issue:
  1. params_optimized.json — chosen param-set + rationale
  2. walk_forward_optimized.json — CPCV results

Even on KILL we save the candidate param-set + full sweep so the next
researcher can see what was tried.
"""

from __future__ import annotations

import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def main() -> int:
    sweep = json.loads((RESULTS / "cpcv_sweep.json").read_text())
    cells = sweep["cells"]
    non_clones = [c for c in cells if not c.get("is_clone", False)]
    clones = [c for c in cells if c.get("is_clone", False)]

    # Best cell: max mean_test_sharpe, then min, then trades.
    best = max(non_clones, key=lambda c: (c["mean_test_sharpe"], c["min_test_sharpe"], c["n_trades_total"]))

    # Acceptance gate evaluation per cell (re-evaluate explicitly).
    def gate(c):
        return {
            "mean_sharpe_ge_0.7": c["mean_test_sharpe"] >= 0.7,
            "worst_sharpe_ge_0.0": c["min_test_sharpe"] >= 0.0,
            "dsr_gt_0.0": c["dsr_z"] > 0.0,
            "trades_ge_100": c["n_trades_total"] >= 100,
            "is_clone": c.get("is_clone", False),
            "all_pass": (
                c["mean_test_sharpe"] >= 0.7
                and c["min_test_sharpe"] >= 0.0
                and c["dsr_z"] > 0.0
                and c["n_trades_total"] >= 100
                and not c.get("is_clone", False)
            ),
        }

    gate_counts = {
        "mean_sharpe_ge_0.7": sum(gate(c)["mean_sharpe_ge_0.7"] for c in cells),
        "worst_sharpe_ge_0.0": sum(gate(c)["worst_sharpe_ge_0.0"] for c in cells),
        "dsr_gt_0.0": sum(gate(c)["dsr_gt_0.0"] for c in cells),
        "trades_ge_100": sum(gate(c)["trades_ge_100"] for c in cells),
        "all_pass": sum(gate(c)["all_pass"] for c in cells),
    }
    gate_counts_nonclone = {
        "mean_sharpe_ge_0.7": sum(gate(c)["mean_sharpe_ge_0.7"] for c in non_clones),
        "worst_sharpe_ge_0.0": sum(gate(c)["worst_sharpe_ge_0.0"] for c in non_clones),
        "dsr_gt_0.0": sum(gate(c)["dsr_gt_0.0"] for c in non_clones),
        "trades_ge_100": sum(gate(c)["trades_ge_100"] for c in non_clones),
        "all_pass": sum(gate(c)["all_pass"] for c in non_clones),
    }

    # params_optimized.json — even on KILL we record the candidate that
    # best matches the gates short of DSR.
    params_optimized = {
        "strategy": sweep["strategy"],
        "timeframe": sweep["timeframe"],
        "cost_model": sweep["cost_model"],
        "cpcv_config": sweep["cpcv_config"],
        "parameter_space": sweep["parameter_space"],
        "chosen_params": {
            "zscore_entry_threshold": best["z_entry"],
            "zscore_exit_threshold": best["z_exit"],
            "vpvr_lookback_bars": best["vpvr_lookback"],
            "regime_threshold": best["regime_threshold"],
            "funding_filter_ema_window": "SKIPPED — not implemented in strategy.py",
        },
        "verdict": "KILL",
        "verdict_reason": (
            "Acceptance gate requires DSR > 0.0 (deflated Sharpe, Bailey & "
            "Lopez de Prado 2014). For N=15 CPCV folds, SR_cap = sqrt(2*ln(15)) "
            "- 0.577/sqrt(2*ln(15)) = 1.43, so any cell with mean OOS Sharpe < 1.43 "
            "is mechanically negative. Best cell achieves mean OOS Sharpe = +1.0 "
            "(PASSES mean >= 0.7 gate) with all 15 folds positive (min +0.43, PASSES "
            "worst >= 0.0 gate), but DSR z = -10.3 (FAILS DSR > 0 gate by construction). "
            "No parameter combination in the 320-cell Cartesian space can clear DSR > 0 "
            "given N=15. Original walk_forward.json already had DSR z = -8.4 (pre-existing "
            "fail), confirming this is a structural gate conflict, not a parameter-tuning failure."
        ),
        "candidate_metrics": {
            "mean_test_sharpe": best["mean_test_sharpe"],
            "min_test_sharpe": best["min_test_sharpe"],
            "dsr_z": best["dsr_z"],
            "n_trades_total": best["n_trades_total"],
        },
        "candidate_gate_eval": gate(best),
        "alternative_candidates": [
            {
                "params": {
                    "z_entry": c["z_entry"],
                    "z_exit": c["z_exit"],
                    "vpvr_lookback": c["vpvr_lookback"],
                    "regime_threshold": c["regime_threshold"],
                },
                "metrics": {
                    "mean_test_sharpe": c["mean_test_sharpe"],
                    "min_test_sharpe": c["min_test_sharpe"],
                    "dsr_z": c["dsr_z"],
                    "n_trades_total": c["n_trades_total"],
                },
                "gate_eval": gate(c),
            }
            for c in sorted(
                non_clones,
                key=lambda x: -x["mean_test_sharpe"],
            )[:6]
        ],
        "sweep_summary": {
            "n_cells": len(cells),
            "n_unique_equity_curves": sweep["n_unique_equity_curves"],
            "n_clones": len(clones),
            "gate_pass_counts_all_cells": gate_counts,
            "gate_pass_counts_nonclone_only": gate_counts_nonclone,
        },
        "rationale": (
            "Best cell is z_entry=2.5, z_exit=0.3, vpvr_lookback=240, regime_threshold=2.0. "
            "All 15 CPCV folds positive (Sharpe range +0.43 to +1.76), 172 trades total "
            "(above 100 minimum), equity curve differs from baseline (sha256 hash differs). "
            "Passes 4/5 acceptance gates (mean>=0.7, min>=0.0, trades>=100, no-clone). "
            "Fails 1/5 (DSR>0.0) because the gate's SR_cap formula is mechanically above the "
            "achievable Sharpe for N=15. Not promoted to PASS-OPTIMIZED per the gate's strict "
            "definition; recorded here so future work can either (a) lower DSR threshold with "
            "justification, (b) extend CPCV to N>=30 to raise SR_cap adaptively, or (c) build "
            "a different signal class entirely."
        ),
    }
    (RESULTS / "params_optimized.json").write_text(json.dumps(params_optimized, indent=2, default=float))

    # walk_forward_optimized.json — full CPCV results
    walk_forward_optimized = {
        "strategy": sweep["strategy"],
        "timeframe": sweep["timeframe"],
        "method": "CPCV",
        "cpcv_config": sweep["cpcv_config"],
        "cost_model": sweep["cost_model"],
        "parameter_space": sweep["parameter_space"],
        "sweep_summary": params_optimized["sweep_summary"],
        "best_cell": {
            "params": params_optimized["chosen_params"],
            "fold_metrics": best["fold_metrics"],
            "aggregate": {
                "mean_test_sharpe": best["mean_test_sharpe"],
                "std_test_sharpe": float(best.get("std_test_sharpe", 0.0)),
                "min_test_sharpe": best["min_test_sharpe"],
                "dsr_z": best["dsr_z"],
                "n_trades_total": best["n_trades_total"],
            },
            "gate_eval": gate(best),
        },
        "verdict": "KILL",
        "all_cells_summary_table": [
            {
                "z_entry": c["z_entry"],
                "z_exit": c["z_exit"],
                "vpvr_lookback": c["vpvr_lookback"],
                "regime_threshold": c["regime_threshold"],
                "is_clone": c.get("is_clone", False),
                "n_trades": c["n_trades_total"],
                "mean_test_sharpe": c["mean_test_sharpe"],
                "min_test_sharpe": c["min_test_sharpe"],
                "dsr_z": c["dsr_z"],
            }
            for c in cells
        ],
    }
    (RESULTS / "walk_forward_optimized.json").write_text(json.dumps(walk_forward_optimized, indent=2, default=float))

    print(f"wrote {RESULTS / 'params_optimized.json'}")
    print(f"wrote {RESULTS / 'walk_forward_optimized.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())