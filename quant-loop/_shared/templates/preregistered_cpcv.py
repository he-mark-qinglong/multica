"""Pre-registered CPCV evaluation template (Phase D — HF pipeline).

Generalizes `strategies/_graveyard/vpvr_xs_pairs_4h/.../run_optimize_cpcv.py`
into a reusable library so new strategies evaluate a pre-registered candidate
set through the shared CPCV harness without copy-pasting the orchestration.

Anti-overfit contract (inherited from the graveyard reference):

  1. Candidates are chosen a priori on economic reasoning — no OOS-driven
     re-ranking of parameter axes after seeing results.
  2. DSR (Bailey & López de Prado 2014) penalizes multiple testing with
     ``n_trials = len(candidates)`` (the pre-registered family size), NOT the
     full Cartesian space.
  3. Selection rule: the FIRST candidate (in pre-registration order) passing
     ALL acceptance gates wins; if none pass, the verdict is KILL.

Signal function contract::

    signal_fn(params: dict,
              data_train: pandas.DataFrame,
              data_full: pandas.DataFrame) -> pandas.Series

returns per-bar simple returns for ALL bars of ``data_full`` (indexed by
``data_full.index``). The CPCV harness slices test bars and applies
purge/embargo itself. Any parameter fitting (e.g. rolling window estimation)
MUST happen on ``data_train`` only — that is the contract CPCV enforces.

Usage::

    from _shared.templates.preregistered_cpcv import (
        run_preregistered_cpcv, DEFAULT_CPCV_CONFIG, DEFAULT_GATES,
    )

    candidates = [
        {"label": "tight_z2.5", "rationale": "...", "params": {"z": 2.5}},
        {"label": "wide_z3.0",  "rationale": "...", "params": {"z": 3.0}},
    ]

    def signal_fn(params, data_train, data_full):
        ...  # refit on data_train only; emit per-bar returns on data_full.index

    cfg = {**DEFAULT_CPCV_CONFIG, "periods_per_year": 365 * 24 * 60}  # 1m bars
    envelope = run_preregistered_cpcv(candidates, data, signal_fn, cfg, DEFAULT_GATES)
    # envelope["verdict"] in {"PASS-OPTIMIZED", "KILL"}
    write_results(envelope, out_dir)  # cpcv_metrics.json + cpcv_summary.txt
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from _shared.validation.cpcv import CPCVResult, cpcv, deflated_sharpe
from _shared.validation.compute_metrics import compute_metrics

__all__ = [
    "DEFAULT_CPCV_CONFIG",
    "DEFAULT_GATES",
    "evaluate_candidate",
    "run_preregistered_cpcv",
    "decide_chosen",
    "write_results",
]

# Default harness settings mirror the graveyard reference (4h bars → purge 500
# / embargo 250). Strategies on other timeframes MUST override
# ``periods_per_year`` (and usually purge/embargo scaled to bar frequency).
DEFAULT_CPCV_CONFIG: dict[str, int] = {
    "n_groups": 6,
    "k_test": 2,
    "purge_bars": 500,
    "embargo_bars": 250,
    "periods_per_year": 365,
}

# Default acceptance gates (T09 discipline, per PLAN_20260724 §Phase E):
# mean OOS Sharpe ≥ 0.5, worst-fold ≥ 0.0, DSR > 0.
DEFAULT_GATES: dict[str, float] = {
    "min_mean_oos_sharpe": 0.5,
    "min_worst_fold_sharpe": 0.0,
    "min_deflated_sharpe": 0.0,
    "min_total_trades": 0,
}

#: Type alias for the strategy signal function (see module docstring).
SignalFn = Callable[[dict, pd.DataFrame, pd.DataFrame], pd.Series]


def _nan_candidate_result(candidate: dict, cpcv_result: CPCVResult) -> dict:
    """Result for a candidate whose CPCV produced no usable folds."""
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


def evaluate_candidate(
    candidate: dict,
    data: pd.DataFrame,
    signal_fn: SignalFn,
    cpcv_config: dict | None = None,
    n_trials: int | None = None,
) -> dict:
    """Evaluate one pre-registered candidate through the shared CPCV harness.

    Args:
        candidate: dict with ``label`` (str), ``params`` (dict) and optional
            ``rationale`` (str) — the pre-registered candidate spec.
        data: full DataFrame indexed by timestamp (tz-naive), columns required
            by ``signal_fn``.
        signal_fn: strategy signal function (module docstring contract).
        cpcv_config: harness settings; defaults to :data:`DEFAULT_CPCV_CONFIG`.
        n_trials: multiple-testing family size for DSR. Defaults to 1 (no
            penalty); ``run_preregistered_cpcv`` passes ``len(candidates)``.

    Returns:
        Dict with per-fold detail plus mean/worst-fold/DSR summary and a
        9-key ``aggregate`` metrics dict (from compute_metrics) computed on
        the stitched out-of-sample returns across all folds.
    """
    cfg = {**DEFAULT_CPCV_CONFIG, **(cpcv_config or {})}

    def strategy_fn(data_train: pd.DataFrame, data_full: pd.DataFrame) -> pd.Series:
        return signal_fn(candidate["params"], data_train, data_full)

    cpcv_result = cpcv(
        data,
        strategy_fn,
        n_groups=cfg["n_groups"],
        k_test=cfg["k_test"],
        purge_bars=cfg["purge_bars"],
        embargo_bars=cfg["embargo_bars"],
        periods_per_year=cfg["periods_per_year"],
    )

    folds = cpcv_result.folds
    if not folds:
        return _nan_candidate_result(candidate, cpcv_result)

    sharpes = np.array([f.oos_sharpe for f in folds])
    total_trades = int(sum(int(f.n_trades) for f in folds))

    # Aggregate OOS metrics: stitch all folds' OOS returns into one curve.
    oos = np.concatenate([f.oos_returns for f in folds])
    equity = pd.Series(np.cumprod(1.0 + oos) * 100_000.0)
    aggregate = compute_metrics(
        equity,
        n_trades=total_trades,
        freq_per_year=cfg["periods_per_year"],
    )

    # DSR over the pre-registered candidate set (n_trials = family size).
    dsr = deflated_sharpe(
        observed_sharpe=float(np.mean(sharpes)),
        n_trials=int(n_trials) if n_trials else 1,
        sample_len=int(data.shape[0]),
        skew=0.0,
        kurt=3.0,
    )

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
        "folds": [
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
        ],
    }


def _apply_family_trial_variance(
    results: list[dict], n_trials: int, sample_len: int
) -> None:
    """Recompute each candidate's DSR with the spec V̂[{SRₙ}] (in place).

    Bailey & López de Prado (2014) deflate by the expected max over the
    trial family, scaled by the ACROSS-CANDIDATE Sharpe variance — not by
    the per-candidate estimator variance used as fallback inside
    ``evaluate_candidate``. Once the whole pre-registered family has been
    evaluated, that variance is known, so every candidate's
    ``deflated_sharpe`` is recomputed against it and the value used is
    pinned to ``trial_sharpe_var`` for auditability. With fewer than two
    finite candidates the family variance is undefined and the per-candidate
    fallback values are kept.
    """
    means = [v["mean_oos_sharpe"] for v in results if np.isfinite(v["mean_oos_sharpe"])]
    if len(means) < 2:
        return
    var = float(np.var(means, ddof=1))
    for v in results:
        if not np.isfinite(v["mean_oos_sharpe"]):
            continue
        v["deflated_sharpe"] = deflated_sharpe(
            observed_sharpe=v["mean_oos_sharpe"],
            n_trials=n_trials,
            sample_len=sample_len,
            trial_sharpe_var=var,
        )
        v["trial_sharpe_var"] = var


def decide_chosen(
    results: Sequence[dict],
    gates: dict | None = None,
) -> tuple[dict | None, str]:
    """Pick the chosen candidate WITHOUT re-ranking on OOS metrics.

    Rule: the FIRST candidate in pre-registration order that passes ALL
    acceptance gates is chosen; if none pass, KILL. Candidates with
    non-finite mean Sharpe (no usable folds) are skipped.
    """
    g = {**DEFAULT_GATES, **(gates or {})}
    for v in results:
        if not np.isfinite(v["mean_oos_sharpe"]):
            continue
        if (
            v["mean_oos_sharpe"] >= g["min_mean_oos_sharpe"]
            and v["worst_oos_sharpe"] >= g["min_worst_fold_sharpe"]
            and v["deflated_sharpe"] > g["min_deflated_sharpe"]
            and v["total_oos_trades"] >= g["min_total_trades"]
        ):
            return v, "PASS-OPTIMIZED"
    return None, "KILL"


def run_preregistered_cpcv(
    candidates: Sequence[dict],
    data: pd.DataFrame,
    signal_fn: SignalFn,
    cpcv_config: dict | None = None,
    gates: dict | None = None,
) -> dict:
    """Evaluate the full pre-registered candidate set and decide the verdict.

    Args:
        candidates: pre-registered candidates, in registration order. Each is
            a dict with ``label``, ``params`` and optional ``rationale``.
        data: full DataFrame indexed by timestamp.
        signal_fn: strategy signal function (module docstring contract).
        cpcv_config: harness settings; defaults to :data:`DEFAULT_CPCV_CONFIG`.
        gates: acceptance gates; defaults to :data:`DEFAULT_GATES`. Pass
            ``None`` explicitly (``gates=None`` is the default) to skip
            selection — the envelope then has ``chosen_label=None`` and
            ``verdict=None``.

    Returns:
        Envelope dict with per-candidate results, chosen label and verdict.
        JSON-serializable (via ``default=str`` for timestamps).
    """
    cfg = {**DEFAULT_CPCV_CONFIG, **(cpcv_config or {})}
    n_trials = len(candidates)

    results = [
        evaluate_candidate(c, data, signal_fn, cpcv_config=cfg, n_trials=n_trials)
        for c in candidates
    ]
    _apply_family_trial_variance(results, n_trials, sample_len=int(data.shape[0]))

    if gates is None:
        chosen, verdict = None, None
    else:
        chosen, verdict = decide_chosen(results, gates)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cpcv_config": cfg,
        "acceptance_gates": {**DEFAULT_GATES, **(gates or {})} if gates is not None else None,
        "n_trials": n_trials,
        "pre_registered_candidates": results,
        "chosen_label": chosen["label"] if chosen else None,
        "verdict": verdict,
        "anti_overfit_notes": [
            "Pre-registered candidate set was chosen a priori on economic reasoning;",
            "no variant parameters were tuned based on OOS Sharpe readings.",
            "DSR penalty applied with n_trials = len(pre_registered_candidates) "
            "and V̂[{SRₙ}] = across-candidate variance of mean OOS Sharpe.",
            "Selection is first-pass-in-registration-order; no OOS re-ranking.",
        ],
    }


def write_results(envelope: dict, out_dir: str | Path) -> list[Path]:
    """Write ``cpcv_metrics.json`` + ``cpcv_summary.txt``; returns paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    metrics_path = out / "cpcv_metrics.json"
    metrics_path.write_text(json.dumps(envelope, indent=2, default=str))

    candidates = envelope["pre_registered_candidates"]
    cfg = envelope["cpcv_config"]
    lines = [
        "=== Pre-registered CPCV evaluation ===",
        f"  n_groups={cfg['n_groups']} k_test={cfg['k_test']} "
        f"purge={cfg['purge_bars']} embargo={cfg['embargo_bars']} "
        f"periods_per_year={cfg['periods_per_year']}",
        f"  pre-registered candidates: {len(candidates)}",
        "",
        "=== Per-candidate OOS metrics ===",
        f"{'label':<48} {'mean':>8} {'worst':>8} {'dsr':>8} {'trades':>8} {'tpf':>6}",
    ]
    for v in candidates:
        lines.append(
            f"{v['label']:<48} {v['mean_oos_sharpe']:>+8.4f} "
            f"{v['worst_oos_sharpe']:>+8.4f} {v['deflated_sharpe']:>+8.4f} "
            f"{v['total_oos_trades']:>8d} {v['trades_per_fold']:>6.1f}"
        )
    lines += ["", f"VERDICT: {envelope['verdict']}", f"CHOSEN:  {envelope['chosen_label'] or 'NONE'}"]

    summary_path = out / "cpcv_summary.txt"
    summary_path.write_text("\n".join(lines) + "\n")
    return [metrics_path, summary_path]
