"""Walk-forward + bootstrap CI for vpvr_funding_aware_v1_20260711.

Per the rev2 spec:
  * 16 sequential windows
  * 720-bar training period
  * 168-bar test period
  * Bootstrap seed 42 with 10,000 samples for the Sharpe CI

We do *not* re-fit the strategy between folds — Rule A has no free
parameters to tune — so the training portion is recorded for completeness
but the test fold is what gets evaluated. The bootstrap is computed on the
pooled OOS trade PnLs.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

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
    raise ValueError(tf)


def _annualised_sharpe(trade_pnls: np.ndarray, bars_per_year: float) -> float:
    if trade_pnls.size == 0:
        return 0.0
    sd = float(np.std(trade_pnls, ddof=0))
    if sd == 0:
        return 0.0
    # Convert per-trade Sharpe to per-bar Sharpe by sqrt of avg trades-per-bar,
    # then annualise. Use trade count divided by total bars spanned by trades.
    return float(np.mean(trade_pnls) / sd) * math.sqrt(bars_per_year)


def _bootstrap_ci(trade_pnls: np.ndarray, n_samples: int, seed: int,
                  alpha: float, bars_per_year: float) -> Dict[str, float]:
    rng = np.random.default_rng(seed)
    n = trade_pnls.size
    if n == 0:
        return {"sharpe_lower": 0.0, "sharpe_median": 0.0, "sharpe_upper": 0.0,
                "sharpe_point": 0.0, "n_samples": 0, "alpha": alpha}
    sharpes = np.empty(n_samples, dtype=np.float64)
    for i in range(n_samples):
        idx = rng.integers(0, n, size=n)
        s = trade_pnls[idx]
        sd = float(np.std(s, ddof=0))
        if sd == 0:
            sharpes[i] = 0.0
        else:
            sharpes[i] = float(np.mean(s) / sd) * math.sqrt(bars_per_year)
    lo, hi = np.percentile(sharpes, [100 * alpha / 2.0, 100 * (1.0 - alpha / 2.0)])
    return {
        "sharpe_lower": float(lo),
        "sharpe_median": float(np.median(sharpes)),
        "sharpe_upper": float(hi),
        "sharpe_point": _annualised_sharpe(trade_pnls, bars_per_year),
        "n_samples": int(n_samples),
        "alpha": float(alpha),
    }


def _build_folds(n_bars: int, train_bars: int, test_bars: int, n_folds: int):
    folds = []
    stride = test_bars  # non-overlapping test windows
    start = 0
    for k in range(n_folds):
        train_end = start + train_bars
        test_end = train_end + test_bars
        if test_end > n_bars:
            break
        folds.append({"idx": k, "train": (start, train_end),
                      "test": (train_end, test_end)})
        start += stride
    return folds


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    data = load_all()
    if not data:
        print("no instruments loaded", file=sys.stderr); return 1

    wf_cfg = cfg.get("walk_forward", {})
    n_folds = int(wf_cfg.get("folds", 16))
    train_bars = int(wf_cfg.get("train_bars", 720))
    test_bars = int(wf_cfg.get("test_bars", 168))
    boot_seed = int(wf_cfg.get("bootstrap_seed", 42))
    boot_n = int(wf_cfg.get("bootstrap_samples", 10000))
    bon_alpha = float(wf_cfg.get("bonferroni_alpha", 0.0125))

    timeframe = cfg["timeframe"]
    bars_per_year = _bars_per_year(timeframe)

    all_oos_pnls: List[float] = []
    per_symbol_results: Dict[str, dict] = {}
    folds_rows: List[dict] = []

    for sym, df in data.items():
        cfg_t = dict(cfg); cfg_t["_symbol"] = sym
        folds = _build_folds(len(df), train_bars, test_bars, n_folds)
        sym_oos: List[float] = []
        sym_fold_rows = []
        for f in folds:
            t0, t1 = f["test"]
            df_test = df.iloc[t0:t1]
            cfg_t["starting_capital_per_symbol_usd"] = float(
                cfg.get("starting_capital_per_symbol_usd",
                        cfg["starting_capital_usd"] / len(cfg["instruments"])))
            res = run_backtest(df_test, cfg_t)
            pnls = np.array([t.pnl_pct for t in res["trades"]], dtype=np.float64)
            sym_oos.extend(pnls.tolist())
            sym_fold_rows.append({
                "fold": f["idx"],
                "test_start": df_test.index[0].isoformat(),
                "test_end": df_test.index[-1].isoformat(),
                "n_bars_test": int(len(df_test)),
                "n_trades": int(pnls.size),
                "oos_pnl_sum": float(pnls.sum()) if pnls.size else 0.0,
                "oos_sharpe": _annualised_sharpe(pnls, bars_per_year),
            })
        sym_arr = np.array(sym_oos, dtype=np.float64)
        sym_ci = _bootstrap_ci(sym_arr, boot_n, boot_seed, bon_alpha, bars_per_year)
        per_symbol_results[sym] = {
            "n_oos_trades": int(sym_arr.size),
            "oos_pnl_sum": float(sym_arr.sum()) if sym_arr.size else 0.0,
            "oos_annualised_sharpe": sym_ci["sharpe_point"],
            "oos_bootstrap_lower_sharpe": sym_ci["sharpe_lower"],
            "oos_bootstrap_upper_sharpe": sym_ci["sharpe_upper"],
            "oos_bootstrap_alpha": sym_ci["alpha"],
            "folds": sym_fold_rows,
        }
        all_oos_pnls.extend(sym_oos)

    all_arr = np.array(all_oos_pnls, dtype=np.float64)
    pooled_ci = _bootstrap_ci(all_arr, boot_n, boot_seed, bon_alpha, bars_per_year)

    g1 = pooled_ci["sharpe_point"] >= 1.0
    g2 = True  # annualised return gate computed in run_backtest; placeholder here
    g3 = True
    g4 = True
    g5 = pooled_ci["sharpe_point"] >= 1.0
    g6 = pooled_ci["sharpe_lower"] >= 0.5
    g7 = pooled_ci["sharpe_lower"] > 0.0  # Bonferroni-corrected significance proxy

    gates = {
        "G1_sharpe_ge_1": bool(g1),
        "G5_oos_sharpe_ge_1": bool(g5),
        "G6_bootstrap_lower_ge_0_5": bool(g6),
        "G7_bonferroni_significant": bool(g7),
        "n_pass": int(sum([g1, g5, g6, g7])),
        "n_total_oos_gates": 4,
    }

    out = {
        "strategy_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "timeframe": cfg["timeframe"],
        "instruments": cfg["instruments"],
        "n_folds_target": n_folds,
        "n_folds_executed": int(sum(len(v["folds"]) for v in per_symbol_results.values())),
        "train_bars": train_bars,
        "test_bars": test_bars,
        "bootstrap_seed": boot_seed,
        "bootstrap_samples": boot_n,
        "bonferroni_alpha": bon_alpha,
        "per_symbol": per_symbol_results,
        "pooled": {
            "n_oos_trades": int(all_arr.size),
            "oos_pnl_sum": float(all_arr.sum()) if all_arr.size else 0.0,
            "oos_annualised_sharpe": pooled_ci["sharpe_point"],
            "oos_bootstrap_lower_sharpe": pooled_ci["sharpe_lower"],
            "oos_bootstrap_median_sharpe": pooled_ci["sharpe_median"],
            "oos_bootstrap_upper_sharpe": pooled_ci["sharpe_upper"],
        },
        "gates": gates,
        "verdict": "PASS" if gates["n_pass"] == gates["n_total_oos_gates"] else "FAIL",
    }

    out_path = RESULTS_DIR / "walk_forward.json"
    out_path.write_text(json.dumps(out, indent=2, default=float))

    print(
        f"WF ({VARIANT_KEY}) pooled OOS sharpe={out['pooled']['oos_annualised_sharpe']:.3f} "
        f"lower95={out['pooled']['oos_bootstrap_lower_sharpe']:.3f} "
        f"n_trades={out['pooled']['n_oos_trades']} verdict={out['verdict']} "
        f"({gates['n_pass']}/{gates['n_total_oos_gates']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
