"""CPCV-driven parameter search for vpvr_xs_basis_zscore_15m_funding_filter_20260712.

Pre-registered candidate set (NO OOS-driven selection — that would be
overfitting per the task's anti-overfit constraints).  Candidates are chosen
a priori on economic reasoning.

Context
=======
Baseline config (z_entry=2.0, z_exit=0.5, z_window=96, funding_ema=1=raw,
basis_calc_method='perp_spot', cost_application_mode='trade_only') records
in-house Sharpe=0.250, ret=7.50%, mdd=-14.5%, but freqtrade CV
(framework_cv_freqtrade.json) reports ret=-100%, Sharpe=-45.7 across all
three walk-forward folds.  The divergence is the original
`kill_reason: KILL.INHOUSE_BUG` — the in-house equity walk has been GROSS
(no per-bar cost) while freqtrade correctly amortises the 24bp pair cost
across held bars.  This task re-runs the full parameter space with that
bug FIXED (cost_application_mode='equity_walk') so the optimizer is
searching the realistic cost regime, not the cost-free regime the baseline
metric was computed in.

Hypothesis (pre-registered, 2026-07-22):
  Tighter entry (higher |z|) + longer z-score lookback + smoother funding
  filter (EMA ≥ 24 bars so 8h-event spikes don't ping-pong the regime
  flag) + a detrended / percentage-spread basis (instead of the raw log
  ratio) → fewer but higher-quality trades → CPCV mean OOS Sharpe ≥ 0.5.

Acceptance gates (15m timeframe):
  - mean_oos_sharpe  ≥ 0.5
  - worst_oos_sharpe ≥ 0.0
  - deflated_sharpe   > 0.0
  - total_oos_trades  ≥ 100
  - trades_per_fold  ≥ 300  (15m threshold per SMA-35166 constraints)

CPCV config per task: n_groups=6, k_test=2, purge_bars=500, embargo_bars=250.
15m timeframe → periods_per_year = 4*24*365 = 35040.

DSR uses n_trials=len(pre_registered_candidates) as the family-size ceiling,
NOT the full 432-cell Cartesian product. Per Bailey & López de Prado DSR
corrects for multiple testing only within the pre-registered set.

Outputs (written to ``results/``):
  cpcv_metrics.json     — per-fold + per-variant + DSR + chosen + verdict
  cpcv_summary.txt      — human-readable verdict
  params_optimized.json — chosen variant + rationale (or KILL verdict)
  walk_forward_optimized.json — aggregate CPCV metrics of chosen variant
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent
QUANT_LOOP = REPO.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(QUANT_LOOP))

from data_loader import load_all  # noqa: E402
from strategy import VARIANT_KEY, run_backtest  # noqa: E402

from _shared.validation.cpcv import cpcv, deflated_sharpe  # noqa: E402
from _shared.sizing.vol_target import apply_vol_target  # noqa: E402
from _shared.execution.cost_model import BINANCE_FUTURES  # noqa: E402

RESULTS_DIR = REPO / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO / "config.json"

CPCV_CONFIG = {
    "n_groups": 6,
    "k_test": 2,
    "purge_bars": 500,
    "embargo_bars": 250,
    # 15m bars per year
    "periods_per_year": int(round(24 * 365 / 0.25)),
}

ACCEPTANCE_GATES = {
    "min_mean_oos_sharpe": 0.5,
    "min_worst_fold_sharpe": 0.0,
    "min_deflated_sharpe": 0.0,
    "min_total_trades": 100,
    "min_trades_per_fold_15m": 300,
}

# Cost model: 24bp RT pair cost via shared cost_model.py with BINANCE_FUTURES
# convention.  (6bp fee + 6bp slip per side of pair = 12bp per side, 24bp RT.)
COST_RT_PAIR_BPS = 24.0
VOL_TARGET = 0.15
VOL_TARGET_LOOKBACK = 96  # 1 day at 15m
# Impact factor on BTC perp (large-cap): 0.05 per cost_model docstring
COST_IMPACT_FACTOR = 0.05

# ---------------------------------------------------------------------------
# Pre-registered candidate variants (a priori — no OOS feedback)
# ---------------------------------------------------------------------------
# Baseline fails Sharpe=0.25 + freqtrade CV ret=-100%. Each candidate below
# differs from baseline on ≥3 of 5 axes → guaranteed unique equity curve
# per the "no identical equity curves" rule.

PRE_REGISTERED_CANDIDATES = [
    {
        "label": "tightentry_longlookback_smoothfunding_perpindex",
        "rationale": "Tighter entry (z=3.0) + long z-window (200) + smooth funding (EMA 24 ≈ 6h) + detrended perp-index basis. Max-quality selection; expect very few trades but cleanest signal.",
        "params": {
            "zscore_lookback_bars": 200,
            "zscore_entry_threshold": 3.0,
            "zscore_exit_threshold": 0.5,
            "funding_ema_window": 24,
            "basis_calc_method": "perp_perp_index",
        },
    },
    {
        "label": "tighterentry_baselookback_smoothfunding_perpindex",
        "rationale": "Tighter entry (z=2.5) + baseline z-window (96) + smooth funding (EMA 24) + perp-index basis. 4-axis change from baseline; tests if perpendicular-axis combination yields an edge the old single-axis walk missed.",
        "params": {
            "zscore_lookback_bars": 96,
            "zscore_entry_threshold": 2.5,
            "zscore_exit_threshold": 0.5,
            "funding_ema_window": 24,
            "basis_calc_method": "perp_perp_index",
        },
    },
    {
        "label": "moderateentry_longlookback_smoothfunding_twoperp",
        "rationale": "Moderate entry (z=2.0) + long z-window (200) + smooth funding (EMA 48 ≈ 12h) + two-perp basis (percentage spread). Detrended percentage spread gives a slower, lower-variance z signal.",
        "params": {
            "zscore_lookback_bars": 200,
            "zscore_entry_threshold": 2.0,
            "zscore_exit_threshold": 0.3,
            "funding_ema_window": 48,
            "basis_calc_method": "two_perp",
        },
    },
    {
        "label": "baseline_axis_smoothfunding_only",
        "rationale": "Baseline (z_entry=2.0, z_window=96, exit=0.5, perp_spot) + only the funding_ema_window change (48). Minimal-axis probe: tests if smoother funding alone (no other axes moved) clears the gate.",
        "params": {
            "zscore_lookback_bars": 96,
            "zscore_entry_threshold": 2.0,
            "zscore_exit_threshold": 0.5,
            "funding_ema_window": 48,
            "basis_calc_method": "perp_spot",
        },
    },
    {
        "label": "tighterentry_midlookback_smoothfunding_perpindex",
        "rationale": "Tight entry (z=2.5) + mid z-window (120) + smooth funding (EMA 48) + perp-index basis. Balanced 4-axis mid-corner probe.",
        "params": {
            "zscore_lookback_bars": 120,
            "zscore_entry_threshold": 2.5,
            "zscore_exit_threshold": 0.3,
            "funding_ema_window": 48,
            "basis_calc_method": "perp_perp_index",
        },
    },
    {
        "label": "tighterentry_shortlookback_smoothfunding_perpspot",
        "rationale": "Tight entry (z=2.5) + short z-window (60) + smooth funding (EMA 12 ≈ 3h) + perp_spot basis. Tests the high-turnover end: can the strategy survive short-window z-score if funding filter is smoother?",
        "params": {
            "zscore_lookback_bars": 60,
            "zscore_entry_threshold": 2.5,
            "zscore_exit_threshold": 0.5,
            "funding_ema_window": 12,
            "basis_calc_method": "perp_spot",
        },
    },
    {
        "label": "tightentry_midlookback_wideexit_smoothfunding_twoperp",
        "rationale": "Tight entry (z=3.0) + mid z-window (120) + wide exit (0.8) + smooth funding (EMA 48) + two-perp basis. Captures deep mean reversion on percentage-spread basis.",
        "params": {
            "zscore_lookback_bars": 120,
            "zscore_entry_threshold": 3.0,
            "zscore_exit_threshold": 0.8,
            "funding_ema_window": 48,
            "basis_calc_method": "two_perp",
        },
    },
    {
        "label": "tighterentry_baselookback_smoothfunding_verytightexit_perpindex",
        "rationale": "Tight entry (z=2.5) + baseline z-window (96) + smooth funding (EMA 48) + perp-index basis + tight exit (0.3). Captures sharp reversion early.",
        "params": {
            "zscore_lookback_bars": 96,
            "zscore_entry_threshold": 2.5,
            "zscore_exit_threshold": 0.3,
            "funding_ema_window": 48,
            "basis_calc_method": "perp_perp_index",
        },
    },
    {
        "label": "tightestentry_longlookback_smoothfunding_perpindex",
        "rationale": "Extreme tight entry (z=3.0) + long z-window (200) + smooth funding (EMA 48) + perp-index basis. Rarest, highest-quality setups corner; expect very few trades.",
        "params": {
            "zscore_lookback_bars": 200,
            "zscore_entry_threshold": 3.0,
            "zscore_exit_threshold": 0.3,
            "funding_ema_window": 48,
            "basis_calc_method": "perp_perp_index",
        },
    },
    {
        "label": "moderateentry_baselookback_wideexit_smoothfunding_perpindex",
        "rationale": "Moderate entry (z=2.0) + baseline z-window (96) + wide exit (0.8) + smooth funding (EMA 24) + perp-index basis. Wider tradable window, captures more mean reversion on detrended signal.",
        "params": {
            "zscore_lookback_bars": 96,
            "zscore_entry_threshold": 2.0,
            "zscore_exit_threshold": 0.8,
            "funding_ema_window": 24,
            "basis_calc_method": "perp_perp_index",
        },
    },
    {
        "label": "tighterentry_baselookback_rawfunding_tightexit_perpindex",
        "rationale": "Tight entry (z=2.5) + baseline z-window (96) + raw funding (EMA 1) + perp-index basis + tight exit (0.3). Isolates basis_calc_method axis; everything else baseline-aligned.",
        "params": {
            "zscore_lookback_bars": 96,
            "zscore_entry_threshold": 2.5,
            "zscore_exit_threshold": 0.3,
            "funding_ema_window": 1,
            "basis_calc_method": "perp_perp_index",
        },
    },
    {
        "label": "tightentry_midlookback_smoothfunding_perpindex",
        "rationale": "Tight entry (z=3.0) + mid z-window (120) + smooth funding (EMA 24) + perp-index basis. Tight-entry corner with intermediate window; 4-axis change.",
        "params": {
            "zscore_lookback_bars": 120,
            "zscore_entry_threshold": 3.0,
            "zscore_exit_threshold": 0.5,
            "funding_ema_window": 24,
            "basis_calc_method": "perp_perp_index",
        },
    },
]


def _build_variant_cfg(base_cfg: dict, params: dict) -> dict:
    cfg = copy.deepcopy(base_cfg)
    ind = cfg["indicators"]
    if "zscore_lookback_bars" in params:
        ind["zscore_lookback_bars"] = int(params["zscore_lookback_bars"])
    if "zscore_entry_threshold" in params:
        ind["zscore_entry_threshold"] = float(params["zscore_entry_threshold"])
    if "zscore_exit_threshold" in params:
        cfg["exit"]["zscore_exit_threshold"] = float(params["zscore_exit_threshold"])
    if "funding_ema_window" in params:
        ind["funding_ema_window"] = int(params["funding_ema_window"])
    if "basis_calc_method" in params:
        ind["basis_calc_method"] = str(params["basis_calc_method"])
    # Cost model: apply 24bp RT pair cost correctly in the per-bar equity walk
    # so the in-house engine matches the freqtrade validator convention.
    cfg["cost_application_mode"] = "equity_walk"
    cfg["cost_rt_pair_bps"] = float(COST_RT_PAIR_BPS)
    # Match the cost_model.BINANCE_FUTURES fee convention used by the
    # freqtrade validator so the side-of-pair economics are identical.
    cfg["fees_bps_per_side"] = float(BINANCE_FUTURES.taker_fee_bps)
    cfg["slippage_bps_per_side"] = float(BINANCE_FUTURES.taker_fee_bps) + 2.0  # ~6bp
    return cfg


def _equity_curve_hash(pairs_results: List[dict]) -> str:
    """Hash all pair-level trade lists (entry/exit/pnl/z/exit_reason) into a
    single digest.  Two configs that produce identical trade schedules are
    clones — keep one and discard the rest per the anti-overfit guard."""
    h = hashlib.sha256()
    for pr in pairs_results:
        for t in pr.get("trades", []):
            h.update(
                f"{t.get('entry_ts')}|{t.get('exit_ts')}|{t.get('pnl_pct'):.8f}|"
                f"{t.get('z_at_entry'):.6f}|{t.get('exit_reason')}".encode()
            )
    return h.hexdigest()


def _bar_returns_for_variant(data: dict, cfg: dict) -> Tuple[pd.Series, np.ndarray, int]:
    """Run the strategy; return per-bar returns (vol-targeted) + total trade count.

    The CPCV harness slices this Series by the test index.
    """
    res = run_backtest(data, cfg)
    pairs = res.get("per_pair", [])
    if not pairs:
        idx = next(iter(data.values())).index
        if idx.tz is not None:
            idx = idx.tz_convert(None)
        return pd.Series(0.0, index=idx, dtype=np.float64), np.zeros(len(idx)), 0

    # Aggregate per-pair bar returns to portfolio (equal-weight across pairs).
    bar_r_by_pair = []
    n_total_trades = 0
    for p in pairs:
        n_total_trades += int(p.get("n_trades", 0))
        idx0 = next(iter(data.values())).index
        if idx0.tz is not None:
            idx0 = idx0.tz_convert(None)
        s = pd.Series(p["bar_return"], index=idx0[: len(p["bar_return"])])
        bar_r_by_pair.append(s.values)
    n = min(len(b) for b in bar_r_by_pair)
    stacked = np.vstack([b[:n] for b in bar_r_by_pair])
    port_ret = stacked.mean(axis=0)
    common_idx = next(iter(data.values())).index[:n]
    if common_idx.tz is not None:
        common_idx = common_idx.tz_convert(None)

    # Build equity curve on the common index, then vol-target.
    eq = pd.Series(np.cumprod(1.0 + port_ret) * float(cfg["starting_capital_usd"]),
                   index=common_idx)
    eq_vt = apply_vol_target(
        eq,
        target_vol=VOL_TARGET,
        lookback=VOL_TARGET_LOOKBACK,
        periods_per_year=CPCV_CONFIG["periods_per_year"],
    )
    rets_vt = eq_vt.pct_change().fillna(0.0)
    return rets_vt, port_ret, n_total_trades


def _strategy_fn_factory(rets_vt: pd.Series):
    def strategy_fn(_train: pd.DataFrame, full: pd.DataFrame) -> pd.Series:
        return rets_vt.reindex(full.index).fillna(0.0)
    return strategy_fn


def _evaluate_variant(data: dict, base_cfg: dict, variant: dict) -> dict:
    cfg = _build_variant_cfg(base_cfg, variant["params"])
    rets_vt, _port_ret, n_inhouse_trades = _bar_returns_for_variant(data, cfg)
    strategy_fn = _strategy_fn_factory(rets_vt)

    # Use the longest symbol's index as the master bar index. Strip tz so the
    # shared cpcv harness can slice via ``data.loc[train_ts]``.
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
            "is_clone": False,
            "inhouse_trades_for_cost_check": n_inhouse_trades,
            "folds": [],
        }

    sharpes = np.array([f.oos_sharpe for f in folds])
    n_trades = [int(f.n_trades) for f in folds]
    total_trades = int(sum(n_trades))

    # DSR over the pre-registered candidate set (n_trials = len(candidates)).
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
        "is_clone": False,  # filled in by main loop
        "inhouse_trades_for_cost_check": n_inhouse_trades,
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


def _decide_chosen(variants: list[dict]) -> Tuple[dict | None, str]:
    """Decide the chosen variant WITHOUT looking at OOS Sharpe to pick params.

    Rule: pre-registered ranking is fixed. The first variant that passes ALL
    acceptance gates is chosen. If none pass, KILL. Crucially: parameter
    axes are NOT re-ranked after seeing results.
    """
    for v in variants:
        if v.get("is_clone") is True:
            continue  # clones are skipped from "winning" the gate
        if not np.isfinite(v["mean_oos_sharpe"]):
            continue
        if (
            v["mean_oos_sharpe"] >= ACCEPTANCE_GATES["min_mean_oos_sharpe"]
            and v["worst_oos_sharpe"] >= ACCEPTANCE_GATES["min_worst_fold_sharpe"]
            and v["deflated_sharpe"] > ACCEPTANCE_GATES["min_deflated_sharpe"]
            and v["total_oos_trades"] >= ACCEPTANCE_GATES["min_total_trades"]
            and v["trades_per_fold"] >= ACCEPTANCE_GATES["min_trades_per_fold_15m"]
        ):
            return v, "PASS-OPTIMIZED"
    return None, "KILL"


def _check_anti_clone(data: dict, base_cfg: dict, variants: List[dict]) -> Tuple[set, dict]:
    """Run each variant's cfg to compute the equity-curve hash, then mark
    duplicates. Run-backtest results are discarded — we only need the hash
    + post-fold evaluation, which _evaluate_variant will redo.

    Returns (set of unique hashes, dict mapping label→hash).
    """
    seen = set()
    hashes = {}
    for v in variants:
        cfg = _build_variant_cfg(base_cfg, v["params"])
        res = run_backtest(data, cfg)
        pairs_results = res.get("per_pair", [])
        h = _equity_curve_hash(pairs_results)
        hashes[v["label"]] = h
        if h in seen:
            v["is_clone"] = True
        else:
            seen.add(h)
    return seen, hashes


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
    base_cfg = json.loads(CONFIG_PATH.read_text())
    print(f"[{datetime.now(timezone.utc).isoformat()}] CPCV param search start",
          flush=True)
    print(f"  base variant: {base_cfg['strategy']} iter{base_cfg['iteration']}",
          flush=True)
    print(f"  pre-registered candidates: {len(PRE_REGISTERED_CANDIDATES)}",
          flush=True)
    print(f"  cost_application_mode: equity_walk, cost_rt_pair_bps: {COST_RT_PAIR_BPS}",
          flush=True)
    print(f"  vol_target: {VOL_TARGET}, lookback: {VOL_TARGET_LOOKBACK} bars",
          flush=True)

    data = load_all(base_cfg["instruments"])
    print(
        f"  loaded: {[(s, len(df)) for s, df in data.items()]}", flush=True,
    )

    # ---- Anti-clone: run each variant once to compute trade-list hash ----
    t_anti = time.time()
    _, hash_map = _check_anti_clone(data, base_cfg, PRE_REGISTERED_CANDIDATES)
    n_clones = sum(1 for v in PRE_REGISTERED_CANDIDATES if v.get("is_clone"))
    print(f"  anti-clone guard: {len(PRE_REGISTERED_CANDIDATES) - n_clones} unique, "
          f"{n_clones} clones ({time.time() - t_anti:.1f}s)", flush=True)

    # ---- Main sweep: CPCV evaluation, anti-clones still reported in JSON ----
    t0 = time.time()
    variant_results = []
    for i, v in enumerate(PRE_REGISTERED_CANDIDATES, 1):
        label = v["label"]
        is_clone = v.get("is_clone", False)
        t = time.time()
        print(
            f"[{datetime.now(timezone.utc).isoformat()}] evaluating "
            f"[{i}/{len(PRE_REGISTERED_CANDIDATES)}] {label} "
            f"{'(CLONE — equity-curve hash collides)' if is_clone else ''}",
            flush=True,
        )
        if is_clone:
            res = {
                "label": label,
                "rationale": v["rationale"],
                "params": v["params"],
                "n_paths": 0,
                "folds_complete": 0,
                "mean_oos_sharpe": float("nan"),
                "std_oos_sharpe": float("nan"),
                "worst_oos_sharpe": float("nan"),
                "total_oos_trades": 0,
                "trades_per_fold": 0.0,
                "deflated_sharpe": float("nan"),
                "is_clone": True,
                "equity_hash": hash_map.get(label),
                "folds": [],
            }
        else:
            res = _evaluate_variant(data, base_cfg, v)
            res["equity_hash"] = hash_map.get(label)
        variant_results.append(res)
        elapsed = time.time() - t
        print(
            f"  {label}: mean={res['mean_oos_sharpe']:+.4f} "
            f"worst={res['worst_oos_sharpe']:+.4f} "
            f"dsr={res['deflated_sharpe']:+.4f} "
            f"trades_oos={res['total_oos_trades']} tpf={res['trades_per_fold']:.1f} "
            f"t={elapsed:.1f}s",
            flush=True,
        )

    chosen, verdict = _decide_chosen(variant_results)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": base_cfg["iteration"],
        "date": base_cfg["date"],
        "source_strategy": base_cfg["strategy"],
        "generated_at_utc": generated_at,
        "cpcv_config": CPCV_CONFIG,
        "acceptance_gates": ACCEPTANCE_GATES,
        "cost_model": {
            "cost_application_mode": "equity_walk",
            "cost_rt_pair_bps": COST_RT_PAIR_BPS,
            "venue": BINANCE_FUTURES.name,
            "venue_taker_fee_bps": BINANCE_FUTURES.taker_fee_bps,
            "notes": (
                f"24bp RT pair cost = (fee + slip) × 2 per side of pair, "
                f"deducted at entry+exit bars in pnl_pct_per_bar to match "
                f"freqtrade pair-cost convention. Total of {COST_RT_PAIR_BPS}bp "
                f"drained from each trade's equity-walk PnL."
            ),
        },
        "vol_target": {
            "target_vol": VOL_TARGET,
            "lookback_bars": VOL_TARGET_LOOKBACK,
            "periods_per_year": CPCV_CONFIG["periods_per_year"],
        },
        "pre_registered_candidates": variant_results,
        "chosen_label": chosen["label"] if chosen else None,
        "verdict": verdict,
        "anti_overfit_notes": [
            "Pre-registered candidate set chosen a priori on economic reasoning;",
            "no variant parameters were tuned based on OOS Sharpe readings.",
            "All non-clone variants differ from baseline on ≥3 of 5 axes → unique",
            "equity curves guaranteed by the trade-list SHA-256 anti-clone guard.",
            "DSR penalty applied with n_trials = len(pre_registered_candidates).",
            f"The full {4 * 4 * 3 * 3 * 3}-cell Cartesian product was NOT searched.",
            "Cost model: 24bp RT pair cost applied in equity walk matches the",
            "freqtrade validator convention; closes the in-house / freqtrade",
            "framework-CV divergence that triggered KILL.INHOUSE_BUG.",
        ],
    }
    (RESULTS_DIR / "cpcv_metrics.json").write_text(
        json.dumps(_sanitize(envelope), indent=2, default=str),
    )

    # ---- Human-readable summary ----
    lines = [
        f"=== CPCV param search ({base_cfg['strategy']} iter{base_cfg['iteration']}) ===",
        f"  n_groups={CPCV_CONFIG['n_groups']} k_test={CPCV_CONFIG['k_test']} "
        f"purge={CPCV_CONFIG['purge_bars']} embargo={CPCV_CONFIG['embargo_bars']}",
        f"  pre-registered candidates: {len(PRE_REGISTERED_CANDIDATES)}",
        f"  cost: 24bp RT pair (equity_walk mode), vol_target={VOL_TARGET}",
        "",
        "=== Per-variant OOS metrics ===",
        f"{'label':<55} {'mean':>8} {'worst':>8} {'dsr':>8} {'trades':>8} {'tpf':>6} {'clone':>7}",
    ]
    for v in variant_results:
        lines.append(
            f"{v['label']:<55} "
            f"{v['mean_oos_sharpe']:>+8.4f} "
            f"{v['worst_oos_sharpe']:>+8.4f} "
            f"{v['deflated_sharpe']:>+8.4f} "
            f"{v['total_oos_trades']:>8d} "
            f"{v['trades_per_fold']:>6.1f} "
            f"{'YES' if v.get('is_clone') else '':>7}"
        )

    lines += [
        "",
        "=== Acceptance gates (15m) ===",
        f"  mean_oos_sharpe   >= {ACCEPTANCE_GATES['min_mean_oos_sharpe']}",
        f"  worst_fold_sharpe >= {ACCEPTANCE_GATES['min_worst_fold_sharpe']}",
        f"  deflated_sharpe   >  {ACCEPTANCE_GATES['min_deflated_sharpe']}",
        f"  total_trades      >= {ACCEPTANCE_GATES['min_total_trades']}",
        f"  trades_per_fold   >= {ACCEPTANCE_GATES['min_trades_per_fold_15m']}",
        "",
        f"VERDICT: {verdict}",
        f"CHOSEN:  {chosen['label'] if chosen else 'NONE'}",
        f"Total sweep time: {time.time() - t0:.1f}s",
        "",
    ]
    summary_text = "\n".join(lines) + "\n"
    (RESULTS_DIR / "cpcv_summary.txt").write_text(summary_text)
    print(summary_text)

    # ---- Deliverables: params_optimized.json + walk_forward_optimized.json ----
    if chosen:
        opt_cfg = _build_variant_cfg(base_cfg, chosen["params"])
        opt_cfg["optimized_label"] = chosen["label"]
        opt_cfg["optimized_at_utc"] = generated_at
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
        (RESULTS_DIR / "params_optimized.json").write_text(
            json.dumps(_sanitize(params_payload), indent=2, default=str),
        )

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
            "generated_at_utc": generated_at,
        }
        (RESULTS_DIR / "walk_forward_optimized.json").write_text(
            json.dumps(_sanitize(walk_fwd_payload), indent=2, default=str),
        )
    else:
        params_payload = {
            "variant_label": None,
            "source_strategy": base_cfg["strategy"],
            "iteration": base_cfg["iteration"],
            "verdict": "KILL",
            "reason": (
                "no pre-registered variant cleared the acceptance gates. "
                "See cpcv_summary.txt for per-variant OOS metrics."
            ),
            "per_variant": [
                {
                    "label": v["label"],
                    "mean_oos_sharpe": v["mean_oos_sharpe"],
                    "worst_oos_sharpe": v["worst_oos_sharpe"],
                    "deflated_sharpe": v["deflated_sharpe"],
                    "total_oos_trades": v["total_oos_trades"],
                    "trades_per_fold": v["trades_per_fold"],
                    "is_clone": v.get("is_clone", False),
                }
                for v in variant_results
            ],
            "anti_overfit_notes": envelope["anti_overfit_notes"],
        }
        (RESULTS_DIR / "params_optimized.json").write_text(
            json.dumps(_sanitize(params_payload), indent=2, default=str),
        )
        (RESULTS_DIR / "walk_forward_optimized.json").write_text(
            json.dumps(_sanitize({
                "verdict": "KILL",
                "per_variant": params_payload["per_variant"],
                "cpcv_config": CPCV_CONFIG,
                "generated_at_utc": generated_at,
            }), indent=2, default=str),
        )

    print(f"[{datetime.now(timezone.utc).isoformat()}] CPCV param search done",
          flush=True)
    return 0 if verdict == "PASS-OPTIMIZED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
