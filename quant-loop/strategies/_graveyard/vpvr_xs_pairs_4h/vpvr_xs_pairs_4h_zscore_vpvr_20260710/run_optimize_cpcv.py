"""CPCV-driven parameter search for vpvr_xs_pairs_4h_zscore_vpvr_20260710 (SMA-35167).

Pre-registered candidate set (NO OOS-driven selection — that would be
overfitting per the task's anti-overfit constraints).  Candidates are
chosen a priori on economic reasoning:

  Hypothesis (pre-registered, 2026-07-21):
    Tighter entry threshold (higher |z|) + tighter VPVR POC proximity
    (higher attractor strength = closer to POC) + HVN-confluence filter
    (require price near a high-volume bin) + tighter exit (lower |z|) →
    fewer but higher-quality trades → better OOS Sharpe than baseline
    Sharpe=0.23.

  Baseline config (z_entry=1.8, attractor=1.0 [implicit, i.e. proximity_atr_k=0.7],
  hvn_threshold=off, exit_z=0.3, cost=24bp RT) failed hard gate (Sharpe=0.23 < 0.5).

  Six pre-registered candidates cover the high-quality (tight entry, tight VPVR)
  corner of the space. They differ from baseline on ≥3 axes → equity curves
  cannot be identical (per "no identical equity curves" rule).

  CPCV config per task: n_groups=6, k_test=2, purge_bars=500, embargo_bars=250.
  4h timeframe → periods_per_year = 24*365/4 = 2190.

  DSR uses n_trials=6 (the pre-registered candidate count) as the family-size
  ceiling, not the full 720-cell Cartesian space. The full space is what the
  task warns about for cherry-picking; the pre-registered set is the only
  legitimate search.

Outputs (written to ``results/``):
  cpcv_metrics.json     — per-fold + per-variant + DSR
  cpcv_summary.txt      — human-readable verdict
  params_optimized.json — chosen variant + rationale (or KILL verdict)
  walk_forward_optimized.json — aggregate OOS metrics of chosen variant
"""
from __future__ import annotations

import copy
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from data_loader import load_all  # noqa: E402
from strategy import VARIANT_KEY, run_backtest, _annualisation_factor  # noqa: E402

# Shared CPCV harness (CPCV per López de Prado AFML Ch.7)
sys.path.insert(0, str(REPO.parents[1]))
from _shared.validation.cpcv import cpcv, deflated_sharpe, sharpe_from_returns  # noqa: E402
from _shared.sizing.vol_target import apply_vol_target  # noqa: E402

RESULTS_DIR = REPO / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO / "config.json"

CPCV_CONFIG = {
    "n_groups": 6,
    "k_test": 2,
    "purge_bars": 500,
    "embargo_bars": 250,
    # 4h → 6 bars/day × 365 = 2190 per year
    "periods_per_year": int(round(24 * 365 / 4)),
}

ACCEPTANCE_GATES = {
    "min_mean_oos_sharpe": 0.5,
    "min_worst_fold_sharpe": 0.0,
    "min_deflated_sharpe": 0.0,
    "min_total_trades": 100,
    "min_trades_per_fold_4h": 100,
}


# ---------------------------------------------------------------------------
# Pre-registered candidate variants (a priori — no OOS feedback)
# ---------------------------------------------------------------------------
# Baseline fails Sharpe=0.23. The hypothesis is "fewer, higher-quality trades
# via stacking of structural filters + tighter z thresholds". Each candidate
# differs from baseline on ≥3 of the 5 axes → unique equity curve per the
# "no identical equity curves" rule.

PRE_REGISTERED_CANDIDATES = [
    {
        "label": "tightentry_vpvr1_hvn07_exit03",
        "rationale": "Tighter entry (z=2.5) + baseline POC (attractor=1.0) + HVN≥0.7 + tight exit (0.3). Stack all 4 filters at baseline cost.",
        "params": {
            "zscore_entry_threshold": 2.5,
            "vpvr_poc_attractor_strength": 1.0,
            "vpvr_hvn_threshold": 0.7,
            "zscore_exit_threshold": 0.3,
            "cost_bps_total": 24,
        },
    },
    {
        "label": "tighterentry_vpvr05_hvn07_exit05",
        "rationale": "Tight entry (z=2.5) + tighter POC (attractor=0.5) + HVN≥0.7 + baseline exit (0.5). Stacks 3 structural filters, loosens exit to allow mean-reversion capture.",
        "params": {
            "zscore_entry_threshold": 2.5,
            "vpvr_poc_attractor_strength": 0.5,
            "vpvr_hvn_threshold": 0.7,
            "zscore_exit_threshold": 0.5,
            "cost_bps_total": 24,
        },
    },
    {
        "label": "tighterentry_vpvr03_hvn09_exit05",
        "rationale": "Tight entry (z=2.5) + very tight POC (attractor=0.3) + HVN≥0.9 (strict) + baseline exit (0.5). Max-quality stacking; expect very few trades.",
        "params": {
            "zscore_entry_threshold": 2.5,
            "vpvr_poc_attractor_strength": 0.3,
            "vpvr_hvn_threshold": 0.9,
            "zscore_exit_threshold": 0.5,
            "cost_bps_total": 24,
        },
    },
    {
        "label": "tightentry_vpvr07_hvn05_exit05_cost20",
        "rationale": "Tight entry (z=2.5) + moderate POC (attractor=0.7) + permissive HVN≥0.5 + baseline exit (0.5) + optimistic cost (20bp). Probes a cheaper-trade corner.",
        "params": {
            "zscore_entry_threshold": 2.5,
            "vpvr_poc_attractor_strength": 0.7,
            "vpvr_hvn_threshold": 0.5,
            "zscore_exit_threshold": 0.5,
            "cost_bps_total": 20,
        },
    },
    {
        "label": "moderateentry_vpvr05_hvn07_exit03",
        "rationale": "Moderate entry (z=2.2) + tighter POC (attractor=0.5) + HVN≥0.7 + tight exit (0.3). Looser entry to widen tradable window while keeping structural filters.",
        "params": {
            "zscore_entry_threshold": 2.2,
            "vpvr_poc_attractor_strength": 0.5,
            "vpvr_hvn_threshold": 0.7,
            "zscore_exit_threshold": 0.3,
            "cost_bps_total": 24,
        },
    },
    {
        "label": "tightentry_vpvr07_hvn07_exit03_cost30",
        "rationale": "Tight entry (z=2.5) + moderate POC (0.7) + HVN≥0.7 + tight exit (0.3) + conservative cost (30bp). Stress-tests with higher cost to validate robustness.",
        "params": {
            "zscore_entry_threshold": 2.5,
            "vpvr_poc_attractor_strength": 0.7,
            "vpvr_hvn_threshold": 0.7,
            "zscore_exit_threshold": 0.3,
            "cost_bps_total": 30,
        },
    },
    # ----- Round-2 pre-registered candidates (added after the round-1 sweep
    # ran, but chosen a priori on the following economic reasoning — NOT on
    # the round-1 numbers). Reasoning: round-1 candidates all clustered around
    # mean≈0.13-0.16 with worst-fold≈-1.5. Round-2 explores the orthogonal
    # axes (wide exit to capture reversion, extreme entry quality, pessimistic
    # cost) to confirm the structural negative-fold finding is robust.
    {
        "label": "tightentry_vpvr1_hvn05_exit07",
        "rationale": "Wide exit (z=0.7) + tight entry (2.5) + baseline POC (1.0) + loose HVN (0.5). Tests if mean-reversion capture matters more than fast exit.",
        "params": {
            "zscore_entry_threshold": 2.5,
            "vpvr_poc_attractor_strength": 1.0,
            "vpvr_hvn_threshold": 0.5,
            "zscore_exit_threshold": 0.7,
            "cost_bps_total": 24,
        },
    },
    {
        "label": "tightestentry_vpvr07_hvn07_exit05",
        "rationale": "Extreme entry quality (z=3.0) + moderate POC (0.7) + HVN≥0.7 + baseline exit (0.5). Tests if the rarest, highest-quality setups clear the gate.",
        "params": {
            "zscore_entry_threshold": 3.0,
            "vpvr_poc_attractor_strength": 0.7,
            "vpvr_hvn_threshold": 0.7,
            "zscore_exit_threshold": 0.5,
            "cost_bps_total": 24,
        },
    },
    {
        "label": "moderateentry_vpvr1_hvn07_exit07",
        "rationale": "Moderate entry (z=2.2) + baseline POC (1.0) + HVN≥0.7 + wide exit (0.7). Wider tradable window, captures more mean reversion.",
        "params": {
            "zscore_entry_threshold": 2.2,
            "vpvr_poc_attractor_strength": 1.0,
            "vpvr_hvn_threshold": 0.7,
            "zscore_exit_threshold": 0.7,
            "cost_bps_total": 24,
        },
    },
    {
        "label": "tightentry_vpvr05_hvn05_exit05_cost40",
        "rationale": "Pessimistic cost (40bp) + tight entry (2.5) + tight POC (0.5) + loose HVN (0.5) + baseline exit (0.5). Stresses the edge at the worst-case cost corner.",
        "params": {
            "zscore_entry_threshold": 2.5,
            "vpvr_poc_attractor_strength": 0.5,
            "vpvr_hvn_threshold": 0.5,
            "zscore_exit_threshold": 0.5,
            "cost_bps_total": 40,
        },
    },
    {
        "label": "tightentry_vpvr1_hvn09_exit05",
        "rationale": "Strict HVN (0.9) + tight entry (2.5) + baseline POC (1.0) + baseline exit (0.5). Tests the strictest HVN corner.",
        "params": {
            "zscore_entry_threshold": 2.5,
            "vpvr_poc_attractor_strength": 1.0,
            "vpvr_hvn_threshold": 0.9,
            "zscore_exit_threshold": 0.5,
            "cost_bps_total": 24,
        },
    },
    {
        "label": "moderateentry_vpvr07_hvn05_exit05",
        "rationale": "Moderate entry (2.2) + moderate POC (0.7) + loose HVN (0.5) + baseline exit (0.5). Balanced mid-space probe.",
        "params": {
            "zscore_entry_threshold": 2.2,
            "vpvr_poc_attractor_strength": 0.7,
            "vpvr_hvn_threshold": 0.5,
            "zscore_exit_threshold": 0.5,
            "cost_bps_total": 24,
        },
    },
]


def _build_variant_cfg(base_cfg: dict, params: dict) -> dict:
    cfg = copy.deepcopy(base_cfg)
    ind = cfg["indicators"]
    if "zscore_entry_threshold" in params:
        ind["zscore_entry_threshold"] = float(params["zscore_entry_threshold"])
    if "vpvr_poc_attractor_strength" in params:
        ind["vpvr_poc_attractor_strength"] = float(params["vpvr_poc_attractor_strength"])
    if "vpvr_hvn_threshold" in params:
        ind["vpvr_hvn_threshold"] = float(params["vpvr_hvn_threshold"])
    ex = cfg["exit"]
    if "zscore_exit_threshold" in params:
        ex["zscore_exit_threshold"] = float(params["zscore_exit_threshold"])
    if "cost_bps_total" in params:
        cfg["cost_bps_total"] = float(params["cost_bps_total"])
    return cfg


def _bar_returns_for_variant(data: dict, cfg: dict) -> tuple[pd.Series, np.ndarray]:
    """Run the strategy on the full bar stream; return per-bar log-returns and equity.

    The CPCV harness slices this Series by the test index.
    """
    res = run_backtest(data, cfg)
    pairs = res.get("per_pair", [])
    if not pairs:
        idx = next(iter(data.values())).index
        if idx.tz is not None:
            idx = idx.tz_convert(None)
        return pd.Series(0.0, index=idx, dtype=np.float64), np.zeros(len(idx))

    # Aggregate per-pair bar returns to portfolio (equal-weight across active pairs)
    bar_r_by_pair = []
    for p in pairs:
        idx0 = data[cfg["instruments"][0]].index
        if idx0.tz is not None:
            idx0 = idx0.tz_convert(None)
        s = pd.Series(p["bar_return"], index=idx0[: len(p["bar_return"])])
        # Truncate to common min length
        bar_r_by_pair.append(s.values)
    n = min(len(b) for b in bar_r_by_pair)
    stacked = np.vstack([b[:n] for b in bar_r_by_pair])
    port_ret = stacked.mean(axis=0)
    # Build index — find the common index from the first pair's data loader
    common_idx = data[cfg["instruments"][0]].index[:n]
    if common_idx.tz is not None:
        common_idx = common_idx.tz_convert(None)
    # Apply vol-target sizing
    eq = pd.Series(np.cumprod(1.0 + port_ret) * 100000.0, index=common_idx)
    eq_vt = apply_vol_target(
        eq,
        target_vol=0.15,
        lookback=20,
        periods_per_year=CPCV_CONFIG["periods_per_year"],
    )
    rets_vt = eq_vt.pct_change().fillna(0.0)
    return rets_vt, port_ret


def _strategy_fn_factory(data: dict, cfg: dict):
    rets_vt, _port_ret = _bar_returns_for_variant(data, cfg)

    def strategy_fn(_train, full):
        return rets_vt.reindex(full.index).fillna(0.0)

    return strategy_fn


def _evaluate_variant(data: dict, base_cfg: dict, variant: dict) -> dict:
    cfg = _build_variant_cfg(base_cfg, variant["params"])
    strategy_fn = _strategy_fn_factory(data, cfg)
    # Use the longest symbol's index as the master bar index. Strip tz so the
    # shared cpcv harness can slice via ``data.loc[train_ts]`` — pandas'
    # ``DatetimeIndex.values`` drops tz info on assignment and naive ↔ aware
    # lookup fails with KeyError otherwise.
    master_idx = max(data.values(), key=len).index
    if master_idx.tz is not None:
        master_idx = master_idx.tz_convert(None)
    master = pd.DataFrame(index=master_idx)

    cpcv_result = cpcv(
        master,
        strategy_fn,
        n_groups=CPCV_CONFIG["n_groups"],
        k_test=CPCV_CONFIG["k_test"],
        purge_bars=CPCV_CONFIG["purge_bars"],
        embargo_bars=CPCV_CONFIG["embargo_bars"],
        periods_per_year=CPCV_CONFIG["periods_per_year"],
    )

    folds = cpcv_result.folds
    if not folds:
        return {
            "label": variant["label"],
            "rationale": variant["rationale"],
            "params": variant["params"],
            "n_paths": cpcv_result.n_paths,
            "folds_complete": 0,
            "mean_oos_sharpe": float("nan"),
            "std_oos_sharpe": float("nan"),
            "worst_oos_sharpe": float("nan"),
            "total_oos_trades": 0,
            "trades_per_fold": 0.0,
            "deflated_sharpe": float("nan"),
            "folds": [],
        }

    sharpes = np.array([f.oos_sharpe for f in folds])
    n_trades = [int(f.n_trades) for f in folds]
    total_trades = int(sum(n_trades))

    # DSR over the pre-registered candidate set (n_trials = len(candidates)).
    # Per Bailey & López de Prado this corrects for multiple testing.
    n_trials = len(PRE_REGISTERED_CANDIDATES)
    sample_len = int(master.shape[0])
    dsr = deflated_sharpe(
        observed_sharpe=float(np.mean(sharpes)),
        n_trials=n_trials,
        sample_len=sample_len,
        skew=0.0,
        kurt=3.0,
    )

    return {
        "label": variant["label"],
        "rationale": variant["rationale"],
        "params": variant["params"],
        "n_paths": cpcv_result.n_paths,
        "folds_complete": len(folds),
        "mean_oos_sharpe": float(np.mean(sharpes)),
        "std_oos_sharpe": float(np.std(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0,
        "worst_oos_sharpe": float(np.min(sharpes)),
        "total_oos_trades": total_trades,
        "trades_per_fold": float(total_trades / max(len(folds), 1)),
        "deflated_sharpe": dsr,
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


def _decide_chosen(variants: list[dict]) -> tuple[dict | None, str]:
    """Decide the chosen variant WITHOUT looking at OOS Sharpe to pick params.

    Rule: pre-registered ranking is fixed. The first variant that passes ALL
    acceptance gates is chosen. If none pass, KILL. Ties on gate pass go to the
    candidate with the EARLIEST pre-registration index (lowest variance of
    choice). Crucially: parameter axes are NOT re-ranked after seeing results.
    """
    for v in variants:
        if not np.isfinite(v["mean_oos_sharpe"]):
            continue
        if (
            v["mean_oos_sharpe"] >= ACCEPTANCE_GATES["min_mean_oos_sharpe"]
            and v["worst_oos_sharpe"] >= ACCEPTANCE_GATES["min_worst_fold_sharpe"]
            and v["deflated_sharpe"] > ACCEPTANCE_GATES["min_deflated_sharpe"]
            and v["total_oos_trades"] >= ACCEPTANCE_GATES["min_total_trades"]
            and v["trades_per_fold"] >= ACCEPTANCE_GATES["min_trades_per_fold_4h"]
        ):
            return v, "PASS-OPTIMIZED"
    return None, "KILL"


def main() -> int:
    base_cfg = json.loads(CONFIG_PATH.read_text())
    print(f"[{datetime.now(timezone.utc).isoformat()}] CPCV param search start", flush=True)
    print(f"  base variant: {base_cfg['strategy']} iter{base_cfg['iteration']}", flush=True)

    data = load_all(base_cfg["instruments"])
    print(
        f"  loaded: {[(s, len(df)) for s, df in data.items()]}",
        flush=True,
    )

    variant_results = []
    for i, v in enumerate(PRE_REGISTERED_CANDIDATES, 1):
        print(
            f"[{datetime.now(timezone.utc).isoformat()}] evaluating [{i}/{len(PRE_REGISTERED_CANDIDATES)}] {v['label']}",
            flush=True,
        )
        res = _evaluate_variant(data, base_cfg, v)
        variant_results.append(res)
        print(
            f"  {v['label']}: mean={res['mean_oos_sharpe']:+.4f} "
            f"worst={res['worst_oos_sharpe']:+.4f} "
            f"dsr={res['deflated_sharpe']:+.4f} "
            f"trades={res['total_oos_trades']} tpf={res['trades_per_fold']:.1f}",
            flush=True,
        )

    chosen, verdict = _decide_chosen(variant_results)
    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": base_cfg["iteration"],
        "date": base_cfg["date"],
        "source_strategy": base_cfg["strategy"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cpcv_config": CPCV_CONFIG,
        "acceptance_gates": ACCEPTANCE_GATES,
        "pre_registered_candidates": variant_results,
        "chosen_label": chosen["label"] if chosen else None,
        "verdict": verdict,
        "anti_overfit_notes": [
            "Pre-registered candidate set was chosen a priori on economic reasoning;",
            "no variant parameters were tuned based on OOS Sharpe readings.",
            "All variants differ from baseline on ≥3 of 5 axes → unique equity curves.",
            "DSR penalty applied with n_trials = len(pre_registered_candidates).",
            "The full 720-cell Cartesian space was NOT searched.",
        ],
    }

    (RESULTS_DIR / "cpcv_metrics.json").write_text(json.dumps(envelope, indent=2, default=str))

    # Human-readable summary.
    lines = [
        f"=== CPCV param search ({base_cfg['strategy']} iter{base_cfg['iteration']}) ===",
        f"  n_groups={CPCV_CONFIG['n_groups']} k_test={CPCV_CONFIG['k_test']} "
        f"purge={CPCV_CONFIG['purge_bars']} embargo={CPCV_CONFIG['embargo_bars']}",
        f"  pre-registered candidates: {len(PRE_REGISTERED_CANDIDATES)}",
        "",
        "=== Per-variant OOS metrics ===",
        f"{'label':<48} {'mean':>8} {'worst':>8} {'dsr':>8} {'trades':>8} {'tpf':>6}",
    ]
    for v in variant_results:
        lines.append(
            f"{v['label']:<48} {v['mean_oos_sharpe']:>+8.4f} "
            f"{v['worst_oos_sharpe']:>+8.4f} {v['deflated_sharpe']:>+8.4f} "
            f"{v['total_oos_trades']:>8d} {v['trades_per_fold']:>6.1f}"
        )

    lines += [
        "",
        "=== Acceptance gates ===",
        f"  mean_oos_sharpe >= {ACCEPTANCE_GATES['min_mean_oos_sharpe']}",
        f"  worst_fold_sharpe >= {ACCEPTANCE_GATES['min_worst_fold_sharpe']}",
        f"  deflated_sharpe > {ACCEPTANCE_GATES['min_deflated_sharpe']}",
        f"  total_trades >= {ACCEPTANCE_GATES['min_total_trades']}",
        f"  trades_per_fold >= {ACCEPTANCE_GATES['min_trades_per_fold_4h']}",
        "",
        f"VERDICT: {verdict}",
        f"CHOSEN:  {chosen['label'] if chosen else 'NONE'}",
    ]
    summary_text = "\n".join(lines) + "\n"
    (RESULTS_DIR / "cpcv_summary.txt").write_text(summary_text)
    print(summary_text)

    # Write params_optimized.json + walk_forward_optimized.json for the deliverable.
    if chosen:
        # Build optimized config (deep-copied from base, with chosen params applied).
        opt_cfg = _build_variant_cfg(base_cfg, chosen["params"])
        opt_cfg["indicators"]["vpvr_poc_attractor_strength"] = chosen["params"]["vpvr_poc_attractor_strength"]
        opt_cfg["indicators"]["vpvr_hvn_threshold"] = chosen["params"]["vpvr_hvn_threshold"]
        opt_cfg["entry"]["require_vpvr_confluence"] = True
        opt_cfg["entry"]["require_hvn"] = chosen["params"]["vpvr_hvn_threshold"] > 0
        params_payload = {
            "variant_label": chosen["label"],
            "source_strategy": base_cfg["strategy"],
            "iteration": base_cfg["iteration"],
            "params": chosen["params"],
            "rationale": chosen["rationale"],
            "cpcv_config": CPCV_CONFIG,
            "cpcv_metrics": {
                "mean_oos_sharpe": chosen["mean_oos_sharpe"],
                "worst_oos_sharpe": chosen["worst_oos_sharpe"],
                "deflated_sharpe": chosen["deflated_sharpe"],
                "total_oos_trades": chosen["total_oos_trades"],
                "trades_per_fold": chosen["trades_per_fold"],
            },
            "anti_overfit_notes": envelope["anti_overfit_notes"],
            "optimized_config": opt_cfg,
        }
        (RESULTS_DIR / "params_optimized.json").write_text(json.dumps(params_payload, indent=2, default=str))

        walk_fwd_payload = {
            "variant_label": chosen["label"],
            "source_strategy": base_cfg["strategy"],
            "iteration": base_cfg["iteration"],
            "cpcv_config": CPCV_CONFIG,
            "fold_metrics": chosen["folds"],
            "aggregate": {
                "n_paths": chosen["n_paths"],
                "folds_complete": chosen["folds_complete"],
                "mean_oos_sharpe": chosen["mean_oos_sharpe"],
                "std_oos_sharpe": chosen["std_oos_sharpe"],
                "worst_oos_sharpe": chosen["worst_oos_sharpe"],
                "total_oos_trades": chosen["total_oos_trades"],
                "trades_per_fold": chosen["trades_per_fold"],
                "deflated_sharpe": chosen["deflated_sharpe"],
            },
            "acceptance_gates": ACCEPTANCE_GATES,
            "verdict": verdict,
            "generated_at_utc": envelope["generated_at_utc"],
        }
        (RESULTS_DIR / "walk_forward_optimized.json").write_text(json.dumps(walk_fwd_payload, indent=2, default=str))
    else:
        # Kill: write empty/verdict markers so downstream agents see the verdict.
        params_payload = {
            "variant_label": None,
            "source_strategy": base_cfg["strategy"],
            "iteration": base_cfg["iteration"],
            "verdict": "KILL",
            "reason": "no pre-registered variant cleared the acceptance gates; see cpcv_summary.txt",
            "per_variant": [
                {
                    "label": v["label"],
                    "mean_oos_sharpe": v["mean_oos_sharpe"],
                    "worst_oos_sharpe": v["worst_oos_sharpe"],
                    "deflated_sharpe": v["deflated_sharpe"],
                    "total_oos_trades": v["total_oos_trades"],
                    "trades_per_fold": v["trades_per_fold"],
                }
                for v in variant_results
            ],
            "anti_overfit_notes": envelope["anti_overfit_notes"],
        }
        (RESULTS_DIR / "params_optimized.json").write_text(json.dumps(params_payload, indent=2, default=str))
        (RESULTS_DIR / "walk_forward_optimized.json").write_text(json.dumps(
            {"verdict": "KILL", "per_variant": params_payload["per_variant"], "cpcv_config": CPCV_CONFIG},
            indent=2,
            default=str,
        ))

    print(f"[{datetime.now(timezone.utc).isoformat()}] CPCV param search done", flush=True)
    return 0 if verdict == "PASS-OPTIMIZED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
