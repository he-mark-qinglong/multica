"""CPCV harness for vpvr_funding_carry_asym_v2 (SMA-34990).

Wraps the shared ``_shared.validation.cpcv`` so the strategy emits a
combinatorial purged K-fold validation report. Each CPCV path is
implemented by:

  1. Train slice (N-K groups) and test slice (K groups) → date ranges.
  2. Run the strategy on the FULL data range, but emit per-bar returns
     that match the strategy's decision on every bar in the index
     (the shared harness expects ``strategy_fn(train, full)`` to
     return per-bar returns for the full index; we slice out the test
     window internally).
  3. Aggregate fold-level Sharpe, profit factor, max DD; compute the
     Deflated Sharpe Ratio (DSR) accounting for the campaign size
     (default n_trials=50).

Run with: ``python run_cpcv.py``.

Outputs:
  results/cpcv_metrics.json — per-fold + aggregate + DSR.
  results/cpcv_summary.txt  — human-readable verdict.
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
from data_loader import load_all  # noqa: E402
from state_machine import compute_metrics, run_backtest, VARIANT_KEY  # noqa: E402
from _shared.validation.cpcv import cpcv, deflated_sharpe  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "vpvr_funding_carry_asym_v2_20260718"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO_ROOT / "config.json"


def _bar_to_periods_per_year(tf: str) -> int:
    if tf == "1m":
        return 525600
    if tf == "15m":
        return 35040
    if tf == "4h":
        return 2190
    return 365


def _build_strategy_returns(df_1m: pd.DataFrame, df_15m: pd.DataFrame, df_4h: pd.DataFrame,
                            funding: pd.DataFrame, cfg: dict) -> pd.Series:
    """Run the strategy on the full bar stream; return per-bar log-returns.

    The shared CPCV harness slices this Series by the test index. Each
    per-bar return is the equity-curve percentage change between
    consecutive bars, computed on the vol-targeted equity.
    """
    decision = build_signals(df_1m, df_15m, df_4h, funding, cfg["params"])
    result = run_backtest(df_1m, decision, cfg)
    eq = pd.Series(result["equity_vt"], index=df_1m.index, dtype=np.float64)
    rets = eq.pct_change().fillna(0.0)
    return rets


def _strategy_factory(df_1m: pd.DataFrame, df_15m: pd.DataFrame, df_4h: pd.DataFrame,
                     funding: pd.DataFrame, cfg: dict):
    """Adapter to the shared CPCV contract.

    ``strategy_fn(data_train, data_full)`` must return per-bar returns
    for the full index. Here ``data_train`` / ``data_full`` are unused
    (we already pre-computed everything); we just emit the per-bar
    returns.
    """
    rets = _build_strategy_returns(df_1m, df_15m, df_4h, funding, cfg)

    def strategy_fn(_data_train: pd.DataFrame, data_full: pd.DataFrame) -> pd.Series:
        return rets.reindex(data_full.index).fillna(0.0)

    return strategy_fn


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

    print("  loading BTCUSDT 1m/15m/4h + funding...", flush=True)
    frames = load_all("BTCUSDT", cfg["timeframes"])
    df_1m = frames["1m"]
    df_15m = frames["15m"]
    df_4h = frames["4h"]
    funding = frames["funding"]
    print(
        f"  1m={len(df_1m)} 15m={len(df_15m)} 4h={len(df_4h)} funding={len(funding)}",
        flush=True,
    )

    # Build the strategy returns once, then let the shared CPCV harness
    # slice the test windows.
    strategy_fn = _strategy_factory(df_1m, df_15m, df_4h, funding, cfg)

    cpcv_cfg = cfg["cpcv"]
    # The shared CPCV expects `data` indexed by timestamp; we pass the
    # 1m frame (largest index) and have strategy_fn ignore it.
    cpcv_result = cpcv(
        df_1m,
        strategy_fn,
        n_groups=int(cpcv_cfg["n_groups"]),
        k_test=int(cpcv_cfg["k_test"]),
        purge_bars=int(cpcv_cfg["purge_bars"]),
        embargo_bars=int(cpcv_cfg["embargo_bars"]),
        periods_per_year=int(cpcv_cfg["periods_per_year"]),
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
    sample_len = int(df_1m.shape[0])

    # Deflated Sharpe Ratio: 50 trials is the campaign size used in
    # the cycle-46 lessons note. The shared cpcv helper handles the
    # math.
    n_trials = int(cfg.get("cpcv", {}).get("n_trials", 50))
    dsr = deflated_sharpe(
        observed_sharpe=mean_sharpe,
        n_trials=n_trials,
        sample_len=sample_len,
        skew=0.0,
        kurt=3.0,
    )

    # Per-fold summary.
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

    gates = {
        "min_mean_oos_sharpe": float(cfg["acceptance_gates"]["min_mean_oos_sharpe"]),
        "min_deflated_sharpe": float(cfg["acceptance_gates"]["min_deflated_sharpe"]),
        "pass_min_mean_oos_sharpe": mean_sharpe >= float(cfg["acceptance_gates"]["min_mean_oos_sharpe"]),
        "pass_min_deflated_sharpe": dsr > float(cfg["acceptance_gates"]["min_deflated_sharpe"]),
    }
    verdict = (
        "PROFITABLE"
        if all(gates[k] for k in gates if k.startswith("pass_"))
        else "NOT-PROFITABLE"
    )

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "source_spec": cfg["source_spec"],
        "implementation_issue": cfg["implementation_issue"],
        "instruments": cfg["instruments"],
        "timeframes": cfg["timeframes"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cpcv_config": {
            "n_groups": int(cpcv_cfg["n_groups"]),
            "k_test": int(cpcv_cfg["k_test"]),
            "purge_bars": int(cpcv_cfg["purge_bars"]),
            "embargo_bars": int(cpcv_cfg["embargo_bars"]),
        },
        "aggregate": aggregate,
        "gates": gates,
        "verdict": verdict,
        "folds": folds_payload,
    }

    (RESULTS_DIR / "cpcv_metrics.json").write_text(json.dumps(_sanitize(envelope), indent=2, default=str))
    (TOPLEVEL_RESULTS / "cpcv_metrics.json").write_text(json.dumps(_sanitize(envelope), indent=2, default=str))

    # Human-readable summary.
    lines: List[str] = []
    lines.append(f"=== {VARIANT_KEY} CPCV ({cfg['source_spec']}) ===")
    lines.append(
        f"  n_groups={cpcv_cfg['n_groups']} k_test={cpcv_cfg['k_test']} "
        f"purge={cpcv_cfg['purge_bars']} embargo={cpcv_cfg['embargo_bars']}"
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
    lines.append(
        f"  deflated_sharpe={dsr:.4f}  (n_trials={n_trials}, sample_len={sample_len})"
    )
    lines.append("")
    lines.append("=== Acceptance gates (CPCV) ===")
    lines.append(
        f"  mean_oos_sharpe >= {cfg['acceptance_gates']['min_mean_oos_sharpe']}     "
        f": pass={gates['pass_min_mean_oos_sharpe']}"
    )
    lines.append(
        f"  deflated_sharpe > {cfg['acceptance_gates']['min_deflated_sharpe']}      "
        f": pass={gates['pass_min_deflated_sharpe']}"
    )
    lines.append("")
    lines.append(f"VERDICT: {verdict}")
    summary_text = "\n".join(lines) + "\n"
    (RESULTS_DIR / "cpcv_summary.txt").write_text(summary_text)
    print(summary_text)
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} CPCV done", flush=True)
    return 0 if verdict == "PROFITABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())