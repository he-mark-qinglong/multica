"""CPCV harness for bb_reversion_rsi_1m_20260707_p3opt_100.

P3-OPT-100 single-variable sweep. Uses the shared CPCV harness
(_shared.validation.cpcv) with n_groups=6, k_test=2 as required by the issue.
Single changed parameter: exit_rsi_mid (50.0 -> 1.0). Strategy is rule-based
(no parameter fitting per fold), so per-fold refit is identity — replay the
same fixed config on the train slice and let the harness slice test bars.

Outputs:
  results/cpcv_metrics.json — per-fold + aggregate + DSR.
  results/cpcv_summary.txt  — human-readable verdict.

Run with: ``python3 run_cpcv.py``.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(QUANT_LOOP))

from data_loader import load_all  # noqa: E402
from strategy import run_backtest, BacktestResult  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO_ROOT / "config.json"
N_GROUPS = 6
K_TEST = 2
PURGE_BARS = 200       # 200 1m bars ≈ 3h20m — covers 60-bar time_stop + cooldown
EMBARGO_BARS = 100     # 100 1m bars ≈ 1h40m
PERIODS_PER_YEAR_1M = 60 * 24 * 365   # match run_backtest.py annualization

VARIANT_KEY = "bb_reversion_rsi_1m_20260707_p3opt_100"
SINGLE_PARAM_CHANGED = "exit_rsi_mid=1.0 (baseline 50.0)"


def _build_per_bar_returns(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Run the strategy on the full frame; return per-bar returns on df.index.

    Reindexes the equity curve to the full bar index so the CPCV harness can
    slice test returns directly from the pre-computed series. This is the
    'no parameter fitting per fold' degenerate case — every fold sees the same
    parameter setting — which is correct for a rule-based single-variable sweep.
    """
    cfg_run = dict(cfg)
    cfg_run["_symbol"] = cfg["instruments"][0]
    res: BacktestResult = run_backtest(df, cfg_run)
    eq = res.equity_curve.reindex(df.index).ffill().fillna(cfg["starting_capital_usd"])
    rets = eq.pct_change().fillna(0.0)
    return rets


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} CPCV start", flush=True)

    print("  loading 1m data...", flush=True)
    data = load_all(cfg["instruments"])
    df = data[cfg["instruments"][0]]
    print(f"  bars={len(df)} span={df.index.min()} -> {df.index.max()}", flush=True)

    # Pre-compute per-bar returns on the full index.
    rets = _build_per_bar_returns(df, cfg)

    def strategy_fn(_data_train: pd.DataFrame, data_full: pd.DataFrame) -> pd.Series:
        # Rule-based strategy: same config every fold → return the same series.
        return rets.reindex(data_full.index).fillna(0.0)

    from _shared.validation.cpcv import cpcv, deflated_sharpe  # noqa: E402

    cpcv_result = cpcv(
        df,
        strategy_fn,
        n_groups=N_GROUPS,
        k_test=K_TEST,
        purge_bars=PURGE_BARS,
        embargo_bars=EMBARGO_BARS,
        periods_per_year=PERIODS_PER_YEAR_1M,
    )
    print(
        f"  CPCV done: n_paths={cpcv_result.n_paths} "
        f"folds_complete={len(cpcv_result.folds)}",
        flush=True,
    )

    sharpes = np.array([f.oos_sharpe for f in cpcv_result.folds])
    ci_lo, ci_hi = cpcv_result.oos_sharpe_ci95
    mean_sharpe = float(cpcv_result.mean_oos_sharpe)
    std_sharpe = float(cpcv_result.std_oos_sharpe)
    sample_len = int(df.shape[0])

    # Deflated Sharpe Ratio with n_trials reflecting MAP-P3 sweep size.
    n_trials = 100  # MAP-P3 sweeps 100 single-variable variants
    dsr = deflated_sharpe(
        observed_sharpe=mean_sharpe,
        n_trials=n_trials,
        sample_len=sample_len,
        skew=0.0,
        kurt=3.0,
    )

    folds_payload = [
        {
            "fold_index": i,
            "train_start": str(f.train_start),
            "train_end": str(f.train_end),
            "test_start": str(f.test_start),
            "test_end": str(f.test_end),
            "oos_sharpe": float(f.oos_sharpe),
            "n_trades": int(f.n_trades),
        }
        for i, f in enumerate(cpcv_result.folds)
    ]

    aggregate = {
        "n_paths": int(cpcv_result.n_paths),
        "folds_complete": int(len(cpcv_result.folds)),
        "mean_oos_sharpe": round(mean_sharpe, 4),
        "std_oos_sharpe": round(std_sharpe, 4),
        "ci95": [round(ci_lo, 4) if np.isfinite(ci_lo) else None,
                 round(ci_hi, 4) if np.isfinite(ci_hi) else None],
        "deflated_sharpe": round(dsr, 4),
        "n_trials_for_dsr": n_trials,
    }

    # The variant acceptance is delta vs baseline OOS Sharpe ≥ 0.1 (issue text),
    # not the absolute PROFITABLE bar (≥ 1.0). We surface both here; the
    # baseline comparison is performed in the result comment.
    verdict = "PROFITABLE" if mean_sharpe >= 1.0 and dsr > 0.0 else "NOT-PROFITABLE"

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": cfg.get("iteration", 100),
        "date": cfg.get("date", "2026-07-21"),
        "instruments": cfg["instruments"],
        "timeframe": cfg["timeframe"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cpcv_config": {
            "n_groups": N_GROUPS,
            "k_test": K_TEST,
            "purge_bars": PURGE_BARS,
            "embargo_bars": EMBARGO_BARS,
            "periods_per_year": PERIODS_PER_YEAR_1M,
            "n_trials_for_dsr": n_trials,
        },
        "single_param_changed": SINGLE_PARAM_CHANGED,
        "aggregate": aggregate,
        "verdict": verdict,
        "folds": folds_payload,
    }

    (RESULTS_DIR / "cpcv_metrics.json").write_text(json.dumps(_sanitize(envelope), indent=2, default=str))

    lines: List[str] = []
    lines.append(f"=== {VARIANT_KEY} CPCV (P3-OPT-100) ===")
    lines.append(f"  single_param_changed: {SINGLE_PARAM_CHANGED}")
    lines.append(
        f"  n_groups={N_GROUPS} k_test={K_TEST} "
        f"purge={PURGE_BARS} embargo={EMBARGO_BARS}"
    )
    lines.append(
        f"  paths={cpcv_result.n_paths} folds_complete={len(cpcv_result.folds)}"
    )
    lines.append(
        f"  mean_oos_sharpe={mean_sharpe:.4f}  std_oos_sharpe={std_sharpe:.4f}"
    )
    lines.append(
        f"  ci95=[{ci_lo if np.isfinite(ci_lo) else float('nan'):.4f}, "
        f"{ci_hi if np.isfinite(ci_hi) else float('nan'):.4f}]"
    )
    lines.append(f"  deflated_sharpe={dsr:.4f}  (n_trials={n_trials})")
    lines.append(f"VERDICT: {verdict}")
    summary_text = "\n".join(lines) + "\n"
    (RESULTS_DIR / "cpcv_summary.txt").write_text(summary_text)
    print(summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())