"""V10-B6: Aggregate walk_forward_summary.json + framework_cv.json from individual outputs.

Reads:
- results/v10/walk_forward.json (already produced by B3)
- results/v10/framework_cv_freqtrade.json
- results/v10/framework_cv_backtrader.json
Writes:
- results/v10/walk_forward_summary.json
- results/v10/framework_cv.json (combined)
"""
import json
from pathlib import Path

ROOT = Path("/home/smark/multica/quant-loop/strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714")
WF_OUT = ROOT / "results/v10/walk_forward_summary.json"
FCV_OUT = ROOT / "results/v10/framework_cv.json"


def main():
    wf = json.loads((ROOT / "results/v10/walk_forward.json").read_text())
    ft = json.loads((ROOT / "results/v10/framework_cv_freqtrade.json").read_text())
    bt = json.loads((ROOT / "results/v10/framework_cv_backtrader.json").read_text())

    # walk_forward_summary.json: condensed view
    summary = {
        "variant": "vol_breakout_vpvr_val_fade_1h_5m_20260714",
        "iteration": 74,
        "n_folds": wf.get("n_folds"),
        "is_sharpe_mean": wf.get("is_sharpe_mean"),
        "oos_sharpe_mean": wf.get("oos_sharpe_mean"),
        "oos_sharpe_median": wf.get("oos_sharpe_median"),
        "oos_sharpe_min": wf.get("oos_sharpe_min"),
        "oos_positive_folds": wf.get("oos_positive_folds"),
        "oos_total_trades": wf.get("oos_total_trades"),
        "verdict": "NOT-PROFITABLE",
        "g1_pass": (wf.get("oos_sharpe_mean") or 0) >= 1.0,
        "g2_min_annualized_pass": False,
        "note": (
            "All V10 signals fall in 2022-2023 IS region; OOS windows "
            "(2024-09-18 onwards) produce 0 trades in every fold. "
            "Walk-forward OOS Sharpe cannot pass G1/G5 in this regime."
        ),
    }
    WF_OUT.write_text(json.dumps(summary, indent=2))

    # framework_cv.json: both engines
    both_pass = (ft["oos_sharpe_mean"] >= 1.0) and (bt["oos_sharpe_mean"] >= 1.0)
    fcw = {
        "variant": "vol_breakout_vpvr_val_fade_1h_5m_20260714",
        "iteration": 74,
        "engines": {
            "freqtrade": {
                "engine_available": ft["engine_available"],
                "n_trades_total": ft["n_trades_total"],
                "oos_sharpe_mean": ft["oos_sharpe_mean"],
                "oos_sharpe_median": ft["oos_sharpe_median"],
                "oos_sharpe_min": ft["oos_sharpe_min"],
                "oos_positive_folds": ft["oos_positive_folds"],
            },
            "backtrader": {
                "engine_available": bt["engine_available"],
                "n_trades_total": bt["n_trades_total"],
                "oos_sharpe_mean": bt["oos_sharpe_mean"],
                "oos_sharpe_median": bt["oos_sharpe_median"],
                "oos_sharpe_min": bt["oos_sharpe_min"],
                "oos_positive_folds": bt["oos_positive_folds"],
            },
        },
        "g5_threshold": 1.0,
        "g5_passed": both_pass,
        "parity_note": (
            "Both engines report OOS Sharpe mean = 0.0 because B3's "
            "walk-forward test windows (2024-09-18+) contain zero V10 "
            "signals. Engines agree; framework-CV parity confirmed at "
            "the 0/0 baseline, but G5 (>= 1.0) cannot be reached."
        ),
        "folds": [
            {
                "fold": ft["folds"][i].get("fold"),
                "oos_window": ft["folds"][i].get("oos_window"),
                "freqtrade_n_trades": ft["folds"][i].get("n_trades"),
                "freqtrade_oos_sharpe": ft["folds"][i].get("oos_sharpe"),
                "backtrader_n_trades": bt["folds"][i].get("n_trades"),
                "backtrader_oos_sharpe": bt["folds"][i].get("oos_sharpe"),
            }
            for i in range(len(ft["folds"]))
        ],
    }
    FCV_OUT.write_text(json.dumps(fcw, indent=2))
    print(json.dumps({"summary": summary, "framework_cv": fcw}, indent=2))


if __name__ == "__main__":
    main()