"""CPCV harness for vpvr_stable_depeg_regime_4h_20260716 (baseline).

Companion to P3-OPT-091. Uses the shared CPCV harness with n_groups=6,
k_test=2 on identical synthetic data so the variant vs baseline comparison
is apples-to-apples.

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

from build_signals import build_signals  # noqa: E402
from strategy import run_backtest, VARIANT_KEY  # noqa: E402
from run_backtest import _make_synthetic_data  # noqa: E402
from _shared.validation.cpcv import cpcv, deflated_sharpe  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO_ROOT / "config.json"
N_GROUPS = 6
K_TEST = 2
PURGE_BARS = 50
EMBARGO_BARS = 20
PERIODS_PER_YEAR_4H = 2190
N_TRIALS = 100


def _build_strategy_returns(df: pd.DataFrame, cfg: dict) -> pd.Series:
    result = run_backtest(df, cfg)
    eq = pd.Series(result["equity"], index=df.index, dtype=np.float64)
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} CPCV start (baseline)", flush=True)

    print("  generating synthetic 4h data (seed=44)...", flush=True)
    df = _make_synthetic_data(n_bars=10000, seed=44)
    print(f"  bars={len(df)} span={df.index.min()} -> {df.index.max()}", flush=True)

    rets = _build_strategy_returns(df, cfg)

    def strategy_fn(_data_train: pd.DataFrame, data_full: pd.DataFrame) -> pd.Series:
        return rets.reindex(data_full.index).fillna(0.0)

    cpcv_result = cpcv(
        df,
        strategy_fn,
        n_groups=N_GROUPS,
        k_test=K_TEST,
        purge_bars=PURGE_BARS,
        embargo_bars=EMBARGO_BARS,
        periods_per_year=PERIODS_PER_YEAR_4H,
    )

    sharpes = np.array([f.oos_sharpe for f in cpcv_result.folds])
    ci_lo, ci_hi = cpcv_result.oos_sharpe_ci95
    mean_sharpe = float(cpcv_result.mean_oos_sharpe)
    std_sharpe = float(cpcv_result.std_oos_sharpe)
    sample_len = int(df.shape[0])

    dsr = deflated_sharpe(
        observed_sharpe=mean_sharpe,
        n_trials=N_TRIALS,
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
        "n_trials_for_dsr": N_TRIALS,
    }

    verdict = "PROFITABLE" if mean_sharpe >= 1.0 and dsr > 0.0 else "NOT-PROFITABLE"

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "instruments": cfg["instruments"],
        "timeframe": cfg["timeframe"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cpcv_config": {
            "n_groups": N_GROUPS,
            "k_test": K_TEST,
            "purge_bars": PURGE_BARS,
            "embargo_bars": EMBARGO_BARS,
            "periods_per_year": PERIODS_PER_YEAR_4H,
            "n_trials_for_dsr": N_TRIALS,
        },
        "role": "baseline_for_p3opt091",
        "aggregate": aggregate,
        "verdict": verdict,
        "folds": folds_payload,
    }

    (RESULTS_DIR / "cpcv_metrics.json").write_text(json.dumps(_sanitize(envelope), indent=2, default=str))

    lines: List[str] = []
    lines.append(f"=== {VARIANT_KEY} CPCV (BASELINE for P3-OPT-091) ===")
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
    lines.append(f"  deflated_sharpe={dsr:.4f}  (n_trials={N_TRIALS})")
    lines.append(f"VERDICT: {verdict}")
    summary_text = "\n".join(lines) + "\n"
    (RESULTS_DIR / "cpcv_summary.txt").write_text(summary_text)
    print(summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())