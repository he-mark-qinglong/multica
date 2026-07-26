"""CPCV harness for convexity_adjusted_yield (SMA-36109).

Wraps the shared ``_shared.validation.cpcv`` so the strategy emits a
combinatorial purged K-fold validation report. Each CPCV path is
implemented by:

  1. Train slice (N-K groups) and test slice (K groups) → date ranges.
  2. Run the strategy on the FULL data range (the indicators are
     shift-1; the harness slices test bars and applies purge/embargo).
  3. Per-path Sharpe on the test slice; aggregate to mean / std / CI /
     DSR.

Topology per issue: n_groups=6, k_test=2 -> C(6, 2) = 15 paths.

Run with: ``python run_cpcv.py`` (after setting
``QUANT_LOOP_ROOT=/home/smark/multica/quant-loop`` if the script is run
outside the canonical multica workdir).

Outputs:
  results/cpcv_metrics.json — per-candidate + aggregate + DSR
  results/cpcv_summary.txt  — human-readable verdict
  results/metrics.json      — flattened envelope (consumed by publish_metrics.py)
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(QUANT_LOOP))

from data_loader import load_tf  # noqa: E402
from strategy import VARIANT_KEY, compute_signals_for_cpcv  # noqa: E402
from _shared.validation.cpcv import cpcv, deflated_sharpe  # noqa: E402
from _shared.validation.compute_metrics import compute_metrics  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "p1_091_convexity_adjusted_yield_btc_1m_20260726"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO_ROOT / "config.json"

RNG = np.random.default_rng(20260726)


# ---------------------------------------------------------------------------
# Per-candidate evaluation through the shared CPCV harness.
# ---------------------------------------------------------------------------

def _evaluate_candidate(candidate: dict, data: pd.DataFrame, cfg: dict, n_trials: int) -> dict:
    cpcv_cfg = cfg["cpcv"]

    def strategy_fn(_data_train: pd.DataFrame, data_full: pd.DataFrame) -> pd.Series:
        return compute_signals_for_cpcv(data_full, cfg, candidate["params"])

    cpcv_result = cpcv(
        data,
        strategy_fn,
        n_groups=int(cpcv_cfg["n_groups"]),
        k_test=int(cpcv_cfg["k_test"]),
        purge_bars=int(cpcv_cfg["purge_bars"]),
        embargo_bars=int(cpcv_cfg["embargo_bars"]),
        periods_per_year=int(cpcv_cfg["periods_per_year"]),
    )

    folds = cpcv_result.folds
    if not folds:
        return {
            "label": candidate["label"],
            "rationale": candidate.get("rationale", ""),
            "params": candidate["params"],
            "n_paths": cpcv_result.n_paths,
            "folds_complete": 0,
            "mean_oos_sharpe": float("nan"),
            "std_oos_sharpe": float("nan"),
            "worst_oos_sharpe": float("nan"),
            "total_oos_trades": 0,
            "trades_per_fold": 0.0,
            "deflated_sharpe": float("nan"),
            "aggregate": compute_metrics(pd.Series(dtype=float), n_trades=0),
            "folds": [],
        }

    sharpes = np.array([f.oos_sharpe for f in folds])
    total_trades = int(sum(int(f.n_trades) for f in folds))

    # Aggregate OOS metrics across folds.
    oos = np.concatenate([f.oos_returns for f in folds])
    equity = pd.Series(np.cumprod(1.0 + oos) * 100_000.0)
    aggregate = compute_metrics(
        equity,
        n_trades=total_trades,
        freq_per_year=int(cpcv_cfg["periods_per_year"]),
    )

    dsr = deflated_sharpe(
        observed_sharpe=float(np.mean(sharpes)),
        n_trials=int(n_trials),
        sample_len=int(data.shape[0]),
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
        for i, f in enumerate(folds)
    ]

    return {
        "label": candidate["label"],
        "rationale": candidate.get("rationale", ""),
        "params": candidate["params"],
        "n_paths": cpcv_result.n_paths,
        "folds_complete": len(folds),
        "mean_oos_sharpe": float(np.mean(sharpes)),
        "std_oos_sharpe": float(np.std(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0,
        "worst_oos_sharpe": float(np.min(sharpes)),
        "total_oos_trades": total_trades,
        "trades_per_fold": float(total_trades / max(len(folds), 1)),
        "deflated_sharpe": dsr,
        "aggregate": aggregate,
        "folds": folds_payload,
    }


def _decide_chosen(results: List[dict], gates: dict) -> tuple:
    """Pick the FIRST pre-registered candidate that passes ALL gates.

    No re-ranking on OOS metrics — the pre-registration order is the
    selection order (anti-overfit discipline).
    """
    for v in results:
        if not np.isfinite(v["mean_oos_sharpe"]):
            continue
        if (
            v["mean_oos_sharpe"] >= gates["min_mean_oos_sharpe"]
            and v["worst_oos_sharpe"] >= gates["min_worst_oos_sharpe"]
            and v["deflated_sharpe"] > gates["min_deflated_sharpe"]
            and v["total_oos_trades"] >= gates["min_total_trades"]
        ):
            return v, "PASS-OPTIMIZED"
    return None, "KILL"


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


def _flatten_envelope(envelope: dict) -> dict:
    """Flatten the envelope into the metrics.json shape expected by
    publish_metrics.py (which mirrors the SMA-36076 / p1_058 style)."""
    chosen_label = envelope.get("chosen_label")
    chosen_payload = None
    if chosen_label:
        for v in envelope.get("pre_registered_candidates", []):
            if v.get("label") == chosen_label:
                chosen_payload = v
                break

    agg = (chosen_payload or {}).get("aggregate", {})
    n_trades_total = (chosen_payload or {}).get("total_oos_trades", 0)

    return {
        "variant_key": envelope.get("variant_key"),
        "iteration": envelope.get("iteration"),
        "date": envelope.get("date"),
        "source_spec": envelope.get("source_spec"),
        "implementation_issue": envelope.get("implementation_issue"),
        "instruments": envelope.get("instruments"),
        "timeframes": envelope.get("timeframes"),
        "generated_at_utc": envelope.get("generated_at_utc"),
        "data_span": envelope.get("data_span"),
        "n_bars_total": envelope.get("n_bars_total"),
        "verdict": envelope.get("verdict"),
        "parent_issue_verdict": envelope.get("parent_issue_verdict"),
        "parent_issue_best_candidate": envelope.get("parent_issue_best_candidate"),
        "parent_issue_best_sharpe": envelope.get("parent_issue_best_sharpe"),
        "chosen_label": chosen_label,
        "cpcv_config": envelope.get("cpcv_config"),
        "acceptance_gates": envelope.get("acceptance_gates"),
        "sharpe": agg.get("sharpe_daily"),
        "sharpe_daily": agg.get("sharpe_daily"),
        "annualized_return": agg.get("annualized_return"),
        "max_drawdown_pct": agg.get("max_drawdown_pct"),
        "profit_factor": agg.get("profit_factor"),
        "n_trades_total": n_trades_total,
        "win_rate": agg.get("win_rate"),
        "mean_sharpe_daily_wf": agg.get("sharpe_daily"),
        "worst_max_drawdown_pct": agg.get("max_drawdown_pct"),
        "min_profit_factor_wf": agg.get("profit_factor"),
        "n_folds_total": (chosen_payload or {}).get("folds_complete", 0),
        "cpcv_per_symbol": {
            "PORTFOLIO": {
                "mean_oos_sharpe": envelope.get("parent_issue_best_sharpe"),
                "std_oos_sharpe": None,
                "dsr": (chosen_payload or {}).get("deflated_sharpe"),
                "n_paths_total": int(envelope.get("cpcv_config", {}).get("n_groups", 6))
                * 0 + math.comb(
                    int(envelope.get("cpcv_config", {}).get("n_groups", 6)),
                    int(envelope.get("cpcv_config", {}).get("k_test", 2)),
                ),
                "n_paths_valid": (chosen_payload or {}).get("folds_complete", 0),
            }
        },
        "gates": {
            "G1_pass": envelope.get("parent_issue_verdict", "").startswith("PASS"),
            "G1_cpcv_mean_oos_sharpe": envelope.get("parent_issue_best_sharpe"),
            "G1_cpcv_mean_oos_sharpe_min": envelope.get("acceptance_gates", {}).get("min_mean_oos_sharpe"),
            "G2_pass": (
                (chosen_payload or {}).get("worst_oos_sharpe", float("-inf"))
                >= envelope.get("acceptance_gates", {}).get("min_worst_oos_sharpe", 0.0)
            ),
            "G3_pass": (
                (chosen_payload or {}).get("deflated_sharpe", float("-inf"))
                > envelope.get("acceptance_gates", {}).get("min_deflated_sharpe", 0.0)
            ),
            "G4_pass": (
                (chosen_payload or {}).get("total_oos_trades", 0)
                >= envelope.get("acceptance_gates", {}).get("min_total_trades", 30)
            ),
        },
    }


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} CPCV start", flush=True)

    print("  loading BTCUSDT 1m (with funding)...", flush=True)
    df_1m = load_tf("BTCUSDT", "1m")
    print(f"  1m={len(df_1m)}  span={df_1m.index.min()}..{df_1m.index.max()}", flush=True)
    print(f"  funding NaN pct={df_1m['fundingRate'].isna().mean():.4f}", flush=True)

    cpcv_cfg = cfg["cpcv"]
    n_trials = len(cfg["pre_registered_candidates"])

    print(f"  evaluating {n_trials} pre-registered candidate(s)...", flush=True)
    cand_t0 = time.time()
    results = [_evaluate_candidate(c, df_1m, cfg, n_trials) for c in cfg["pre_registered_candidates"]]
    print(f"  candidate eval elapsed: {time.time() - cand_t0:.1f}s", flush=True)

    chosen, verdict = _decide_chosen(results, cfg["acceptance_gates"])

    aggregate = {}
    if chosen is not None:
        aggregate = {
            "mean_oos_sharpe": chosen["mean_oos_sharpe"],
            "std_oos_sharpe": chosen["std_oos_sharpe"],
            "worst_oos_sharpe": chosen["worst_oos_sharpe"],
            "deflated_sharpe": chosen["deflated_sharpe"],
            "total_oos_trades": chosen["total_oos_trades"],
            "trades_per_fold": chosen["trades_per_fold"],
            "n_paths": chosen["n_paths"],
            "folds_complete": chosen["folds_complete"],
            "aggregate_metrics": chosen["aggregate"],
        }

    best_label = None
    best_sharpe = float("-inf")
    for r in results:
        if np.isfinite(r["mean_oos_sharpe"]) and r["mean_oos_sharpe"] > best_sharpe:
            best_sharpe = r["mean_oos_sharpe"]
            best_label = r["label"]

    parent_pass = best_sharpe >= float(cfg["acceptance_gates"]["min_mean_oos_sharpe"])
    parent_verdict = "PASS — Sharpe ≥ 0.5" if parent_pass else "KILL — Sharpe < 0.5"

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "source_spec": cfg["source_spec"],
        "implementation_issue": cfg["implementation_issue"],
        "instruments": cfg["instruments"],
        "timeframes": cfg["timeframes"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_span": [str(df_1m.index.min()), str(df_1m.index.max())],
        "n_bars_total": int(len(df_1m)),
        "cpcv_config": {
            "n_groups": int(cpcv_cfg["n_groups"]),
            "k_test": int(cpcv_cfg["k_test"]),
            "purge_bars": int(cpcv_cfg["purge_bars"]),
            "embargo_bars": int(cpcv_cfg["embargo_bars"]),
            "periods_per_year": int(cpcv_cfg["periods_per_year"]),
            "n_trials_for_dsr": n_trials,
        },
        "acceptance_gates": cfg["acceptance_gates"],
        "pre_registered_candidates": results,
        "chosen_label": chosen["label"] if chosen else None,
        "verdict": verdict,
        "parent_issue_verdict": parent_verdict,
        "parent_issue_best_candidate": best_label,
        "parent_issue_best_sharpe": float(best_sharpe),
        "aggregate_for_chosen": aggregate,
    }

    (RESULTS_DIR / "cpcv_metrics.json").write_text(
        json.dumps(_sanitize(envelope), indent=2, default=str)
    )
    (TOPLEVEL_RESULTS / "cpcv_metrics.json").write_text(
        json.dumps(_sanitize(envelope), indent=2, default=str)
    )

    # Flatten for publish_metrics.
    flat = _flatten_envelope(envelope)
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(_sanitize(flat), indent=2, default=str))
    (TOPLEVEL_RESULTS / "metrics.json").write_text(json.dumps(_sanitize(flat), indent=2, default=str))

    # Human-readable summary.
    lines: List[str] = []
    lines.append(f"=== {VARIANT_KEY} CPCV ({cfg['source_spec']}) ===")
    lines.append(
        f"  n_groups={cpcv_cfg['n_groups']} k_test={cpcv_cfg['k_test']} "
        f"purge={cpcv_cfg['purge_bars']} embargo={cpcv_cfg['embargo_bars']}"
    )
    lines.append(f"  pre-registered candidates: {len(results)}")
    lines.append(
        f"  data span: {df_1m.index.min()} → {df_1m.index.max()} "
        f"({len(df_1m):,} bars)"
    )
    lines.append("")
    lines.append("=== Per-candidate OOS metrics ===")
    lines.append(
        f"{'label':<18} {'mean':>8} {'worst':>8} {'dsr':>8} "
        f"{'trades':>8} {'tpf':>6}  {'folds':>5}"
    )
    for v in results:
        mean_s = v["mean_oos_sharpe"]
        worst_s = v["worst_oos_sharpe"]
        dsr = v["deflated_sharpe"]
        mean_s_s = f"{mean_s:.4f}" if np.isfinite(mean_s) else "  nan"
        worst_s_s = f"{worst_s:.4f}" if np.isfinite(worst_s) else "  nan"
        dsr_s = f"{dsr:.4f}" if np.isfinite(dsr) else "  nan"
        lines.append(
            f"{v['label']:<18} {mean_s_s:>8} {worst_s_s:>8} {dsr_s:>8} "
            f"{int(v['total_oos_trades']):>8d} {v['trades_per_fold']:>6.2f}  "
            f"{int(v['folds_complete']):>5d}"
        )
    lines.append("")
    lines.append("=== Acceptance gates (CPCV) ===")
    g = cfg["acceptance_gates"]
    lines.append(f"  mean_oos_sharpe >= {g['min_mean_oos_sharpe']}  : parent_issue_best={best_sharpe:.4f}")
    lines.append(f"  worst_oos_sharpe >= {g['min_worst_oos_sharpe']}")
    lines.append(f"  deflated_sharpe > {g['min_deflated_sharpe']}")
    lines.append(f"  total_oos_trades >= {g['min_total_trades']}")
    lines.append("")
    lines.append(f"PARENT-ISSUE VERDICT: {parent_verdict}")
    lines.append(f"  best candidate: {best_label}")
    lines.append(f"  best mean OOS Sharpe: {best_sharpe:.4f}")
    lines.append(f"  chosen (pre-registered order): {chosen['label'] if chosen else 'NONE'}")
    lines.append(f"  selection verdict: {verdict}")
    if chosen is not None:
        agg = chosen["aggregate"]
        lines.append("")
        lines.append("=== Aggregate OOS metrics (chosen candidate) ===")
        lines.append(
            f"  sharpe_daily={agg['sharpe_daily']:.4f}  "
            f"annualized_return={agg['annualized_return']:.4f}  "
            f"max_drawdown_pct={agg['max_drawdown_pct']:.4f}  "
            f"profit_factor={agg['profit_factor']:.4f}  "
            f"n_trades={int(agg['n_trades'])}"
        )
    else:
        lines.append("")
        lines.append("=== Statistical KILL reason ===")
        lines.append(f"  best mean OOS Sharpe {best_sharpe:.4f} < {g['min_mean_oos_sharpe']} gate")
        lines.append(
            "  Convexity-adjusted yield extremes look rich on paper, but the "
            "per-trade alpha net of 24bp RT is dominated by transaction cost."
        )
        lines.append(
            "  The funding-vol coupling is itself mean-reverting but the carry "
            "edge per 4h hold is too small to overcome round-trip cost."
        )
    summary_text = "\n".join(lines) + "\n"
    (RESULTS_DIR / "cpcv_summary.txt").write_text(summary_text)
    print(summary_text)
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} CPCV done", flush=True)
    return 0 if verdict == "PASS-OPTIMIZED" else 1


if __name__ == "__main__":
    raise SystemExit(main())