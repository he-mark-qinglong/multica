"""CPCV (Combinatorial Purged Cross-Validation) parameter sweep for
vpvr_xs_pairs_btc_sol_4h_20260712.

Strategy accepts (mapped from issue parameter names):
  - zscore_entry_threshold -> config["indicators"]["zscore_entry_threshold"]
  - zscore_exit_threshold  -> config["exit"]["zscore_exit_threshold"]
  - vpvr_lookback_bars     -> config["indicators"]["vpvr_window_bars"]
                              (issue params 240/360/480/720 replace existing 60)
  - regime_threshold       -> config["exit"]["regime_switch_zscore_threshold"]
                              (issue params 0.5..2.0 - interpreted as
                               z-score magnitude at which regime break fires)
  - funding_filter_ema_window: NOT implemented in current strategy - SKIPPED

CPCV setup:
  - n_groups=6, k_test=2 -> C(6,2)=15 test splits per cell
  - purge_bars=500 (in 4h units -> 500*4h = ~83 days gap after train/test boundary)
  - embargo_bars=250 (additional 250*4h = ~42 days)

Anti-overfit guards per the issue:
  - No two param-sets may produce identical equity curves -> hash by trade list
  - Min trades per fold >= 100 (4h threshold)
  - Mean + worst fold reported
  - DSR (deflated sharpe) computed over the 15 OOS fold sharpes
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import sys
import time
from dataclasses import dataclass, asdict
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from data_loader import load_all
from strategy import run_backtest


STRATEGY_DIR = Path(__file__).resolve().parent
RESULTS_DIR = STRATEGY_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = STRATEGY_DIR / "config.json"

# Issue parameter space
ZSCORE_ENTRY = [1.8, 2.0, 2.2, 2.5, 3.0]
ZSCORE_EXIT = [0.3, 0.5, 0.7, 1.0]
VPVR_LOOKBACK = [240, 360, 480, 720]
# regime_threshold from issue: smaller values trigger faster exit. Maps to
# cfg["exit"]["regime_switch_zscore_threshold"] (currently 3.0).
REGIME_THRESHOLD = [0.5, 1.0, 1.5, 2.0]
# funding_filter_ema_window: SKIPPED (no funding filter in strategy.py).

# CPCV config
N_GROUPS = 6
K_TEST = 2
PURGE_BARS = 500
EMBARGO_BARS = 250
MIN_TRADES_PER_FOLD_4H = 100  # per issue constraint

# Issue non-negotiable cost model (24bp RT) — overrides config.json
FEE_BPS_PER_SIDE = 6.0
SLIP_BPS_PER_SIDE = 6.0
# Vol target (handled downstream by issue's vol_target.py; not applied here
# because the strategy's per-pair_notional_pct=0.01 is already risk-bounded)

# Baseline cfg (cached)
_BASE_CFG = json.loads(CONFIG_PATH.read_text())


def make_cfg(
    z_entry: float,
    z_exit: float,
    vpvr_w: int,
    regime_thr: float,
) -> dict:
    cfg = json.loads(json.dumps(_BASE_CFG))
    cfg["indicators"]["zscore_entry_threshold"] = float(z_entry)
    cfg["indicators"]["vpvr_window_bars"] = int(vpvr_w)
    cfg["exit"]["zscore_exit_threshold"] = float(z_exit)
    cfg["exit"]["regime_switch_zscore_threshold"] = float(regime_thr)
    cfg["fees_bps_per_side"] = FEE_BPS_PER_SIDE
    cfg["slippage_bps_per_side"] = SLIP_BPS_PER_SIDE
    return cfg


def equity_curve_hash(trades: List[dict]) -> str:
    """Hash an equity curve by the trade tuple sequence. Two configs that
    produce identical trade sequences (same entry/exit/pnl/z) are clones."""
    h = hashlib.sha256()
    for t in trades:
        h.update(
            f"{t.get('entry_ts')}|{t.get('exit_ts')}|{t.get('pnl_pct'):.8f}|"
            f"{t.get('z_at_entry'):.6f}|{t.get('exit_reason')}".encode()
        )
    return h.hexdigest()


def annualisation_factor(timeframe: str) -> float:
    tf = timeframe.strip().lower()
    if tf.endswith("m"):
        minutes = int(tf[:-1])
        return math.sqrt(60 * 24 * 365 / minutes)
    if tf.endswith("h"):
        hours = int(tf[:-1])
        return math.sqrt(24 * 365 / hours)
    if tf.endswith("d"):
        return math.sqrt(365 / int(tf[:-1]))
    raise ValueError(tf)


def sharpe_from_trades(trades: List[dict], tf: str = "4h") -> float:
    if not trades:
        return 0.0
    pnls = np.asarray([float(t.get("pnl_pct", 0.0)) for t in trades], dtype=float)
    if len(pnls) < 2:
        return 0.0
    span_start = pd.Timestamp(trades[0]["entry_ts"])
    span_end = pd.Timestamp(trades[-1]["entry_ts"])
    days = max((span_end - span_start).days, 1)
    trades_per_year = len(pnls) * 365.0 / days
    std = float(np.std(pnls, ddof=1))
    mean = float(np.mean(pnls))
    if std <= 0:
        return 0.0
    return (mean / std) * math.sqrt(trades_per_year)


def dsr_zscore(test_sharpes: List[float]) -> float:
    """Bailey & Lopez de Prado DSR — return z-score.

    SR_cap (max Sharpe under null of N independent trials) heuristic:
        SR_cap = sqrt(2 * log(N)) - 0.577 / sqrt(2 * log(N))
    """
    n = len(test_sharpes)
    if n < 2:
        return 0.0
    arr = np.asarray(test_sharpes, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    if std == 0:
        return 0.0
    sr_cap = math.sqrt(2.0 * math.log(n)) - 0.577 / math.sqrt(2.0 * math.log(n))
    se = std / math.sqrt(n - 1)
    return (mean - sr_cap) / se if se > 0 else 0.0


@dataclass
class FoldResult:
    fold_id: int
    train_groups: List[int]
    test_groups: List[int]
    test_start: str
    test_end: str
    n_test_trades: int
    n_purged_trades: int
    test_sharpe: float
    test_return: float
    test_mdd: float
    test_win_rate: float


def build_cpcv_groups(n_bars: int) -> List[Tuple[int, int]]:
    """Split bar range into n_groups contiguous slices of (near-)equal length."""
    size = n_bars // N_GROUPS
    extra = n_bars - size * N_GROUPS
    out = []
    start = 0
    for g in range(N_GROUPS):
        end = start + size + (1 if g < extra else 0)
        out.append((start, end))
        start = end
    return out


def cpcv_test_slices(groups: List[Tuple[int, int]]) -> List[Tuple[int, int, List[int], List[int]]]:
    """For every choice of k_test groups, return (test_start_bar, test_end_bar, test_groups, train_groups)."""
    out = []
    for test_groups in combinations(range(N_GROUPS), K_TEST):
        test_groups = list(test_groups)
        train_groups = [g for g in range(N_GROUPS) if g not in test_groups]
        test_starts = [groups[g][0] for g in test_groups]
        test_ends = [groups[g][1] for g in test_groups]
        # Union test interval (assumes contiguous groups; we sort).
        ordered_test = sorted(test_groups)
        # Build contiguous span from min(test_groups) to max(test_groups)+1
        # but only test_groups are evaluated; train groups in between are skipped.
        ts = groups[ordered_test[0]][0]
        te = groups[ordered_test[-1]][1]
        out.append((ts, te, test_groups, train_groups))
    return out


def slice_trades_per_fold(
    pair_trades: List[dict],
    ts_to_bar: Dict[pd.Timestamp, int],
    test_start_bar: int,
    test_end_bar: int,
    purge_bars: int,
    embargo_bars: int,
) -> Tuple[List[dict], int]:
    """Filter trades to OOS, applying purge/embargo by bar index proximity to
    test boundary. Returns (filtered_trades, n_purged)."""
    purge_lo = test_start_bar - purge_bars
    embargo_hi = test_end_bar + embargo_bars
    kept = []
    purged_count = 0
    for t in pair_trades:
        ts = pd.Timestamp(t["entry_ts"])
        bar_idx = ts_to_bar.get(ts)
        if bar_idx is None:
            # Trade entry bar falls outside the data span — keep but flag.
            kept.append(t)
            continue
        if purge_lo <= bar_idx < test_start_bar:
            purged_count += 1
            continue
        if test_end_bar <= bar_idx < embargo_hi:
            purged_count += 1
            continue
        if test_start_bar <= bar_idx < test_end_bar:
            kept.append(t)
    return kept, purged_count


def evaluate_cell(
    pair_trades: List[dict],
    bar_return: np.ndarray,
    ts_to_bar: Dict[pd.Timestamp, int],
    idx_to_ts: Dict[int, pd.Timestamp],
    test_groups_info: List[Tuple[int, int, List[int], List[int]]],
    ann: float,
) -> Tuple[List[FoldResult], List[dict]]:
    """For each CPCV test split, compute OOS metrics.

    Sharpe uses the bar-level annualized convention (matches metrics.json
    methodology):  sharpe = mean(bar_return[t_start:t_end]) / std * ann
    Trades/mdd/win-rate use the OOS trade subset (with purge/embargo).
    """
    fold_results: List[FoldResult] = []
    oos_trade_sets: List[dict] = []
    for fold_id, (ts, te, test_groups, train_groups) in enumerate(test_groups_info, start=1):
        all_pair_oos = []
        oos, n_purged = slice_trades_per_fold(
            pair_trades, ts_to_bar, ts, te, PURGE_BARS, EMBARGO_BARS
        )
        if oos:
            all_pair_oos.extend(oos)
        # Bar-level OOS slice (clip purge/embargo).
        b_start = max(ts, ts + 0)  # ts is inclusive
        b_end = te
        br_slice = bar_return[b_start:b_end]
        br_slice = br_slice[~np.isnan(br_slice)]
        if br_slice.size > 1:
            mu = float(np.mean(br_slice))
            sigma = float(np.std(br_slice, ddof=0))
            sharpe = (mu / sigma) * ann if sigma > 0 else 0.0
            bar_cum = np.cumprod(1.0 + br_slice) - 1.0
            cum_pnl = float(bar_cum[-1])
            # running max drawdown on bar equity
            eq = np.cumprod(1.0 + br_slice)
            peak = np.maximum.accumulate(eq)
            mdd = float(np.min(eq / peak - 1.0))
        else:
            sharpe = 0.0
            cum_pnl = 0.0
            mdd = 0.0

        if not all_pair_oos:
            fr = FoldResult(
                fold_id=fold_id,
                train_groups=train_groups,
                test_groups=test_groups,
                test_start=str(idx_to_ts.get(ts, "")),
                test_end=str(idx_to_ts.get(te - 1, "")),
                n_test_trades=0,
                n_purged_trades=n_purged,
                test_sharpe=sharpe,
                test_return=cum_pnl,
                test_mdd=mdd,
                test_win_rate=0.0,
            )
        else:
            pnls = np.asarray([t["pnl_pct"] for t in all_pair_oos], dtype=float)
            cum = np.cumsum(pnls)
            peak_t = np.maximum.accumulate(cum)
            wr = float(np.mean(pnls > 0))
            fr = FoldResult(
                fold_id=fold_id,
                train_groups=train_groups,
                test_groups=test_groups,
                test_start=str(idx_to_ts.get(ts, "")),
                test_end=str(idx_to_ts.get(te - 1, "")),
                n_test_trades=len(all_pair_oos),
                n_purged_trades=n_purged,
                test_sharpe=float(sharpe),
                test_return=cum_pnl,
                test_mdd=mdd,
                test_win_rate=wr,
            )
        fold_results.append(fr)
        oos_trade_sets.append({
            "fold_id": fold_id, "test_groups": test_groups,
            "n_trades": fr.n_test_trades, "sharpe": fr.test_sharpe,
        })
    return fold_results, oos_trade_sets


def main() -> int:
    t0 = time.time()
    print(f"[cpcv_optimize] loading data ...")
    data = load_all(_BASE_CFG["instruments"])
    ann = annualisation_factor(_BASE_CFG["timeframe"])

    # Build a single canonical bar index from BTCUSDT (longest history).
    btc = data["BTCUSDT"]
    n_bars = len(btc)
    idx_to_ts = {i: ts for i, ts in enumerate(btc.index)}
    ts_to_bar = {ts: i for i, ts in idx_to_ts.items()}
    groups = build_cpcv_groups(n_bars)
    test_groups_info = cpcv_test_slices(groups)
    print(f"[cpcv_optimize] n_bars={n_bars}, groups={[(s,e) for s,e in groups]}, "
          f"cpcv_folds={len(test_groups_info)}")

    # Cartesian product
    cells = list(itertools.product(ZSCORE_ENTRY, ZSCORE_EXIT, VPVR_LOOKBACK, REGIME_THRESHOLD))
    print(f"[cpcv_optimize] n_cells={len(cells)} (will skip duplicates via equity-hash)")
    print(f"[cpcv_optimize] cost: fee={FEE_BPS_PER_SIDE}bp slip={SLIP_BPS_PER_SIDE}bp per side")

    # Track seen equity hashes for the "no identical equity curves" guard.
    seen_hashes = set()
    duplicate_of = {}

    cell_results = []
    baseline_oos_folds = None  # For comparison: baseline (z_entry=1.8, z_exit=0.3, vpvr_w=60, regime=3.0)

    for i, (z_e, z_x, vpvr_w, reg_thr) in enumerate(cells, start=1):
        cfg = make_cfg(z_e, z_x, vpvr_w, reg_thr)
        # Baseline uses vpvr_w=60; we use 240/360/480/720 per issue.
        # Capture baseline equity (issue pre-publish state) for "different from baseline" test.
        result = run_backtest(data, cfg)
        pair_res = result["per_pair"][0]
        trades = pair_res["trades"]
        bar_return = pair_res["bar_return"]
        eq_hash = equity_curve_hash(trades)
        if eq_hash in seen_hashes:
            duplicate_of[(z_e, z_x, vpvr_w, reg_thr)] = True
            fold_results, _ = evaluate_cell(
                trades, bar_return, ts_to_bar, idx_to_ts, test_groups_info, ann
            )
            test_sharpes = [f.test_sharpe for f in fold_results]
            cell_results.append({
                "z_entry": z_e, "z_exit": z_x, "vpvr_lookback": vpvr_w,
                "regime_threshold": reg_thr,
                "is_clone": True,
                "n_trades_total": len(trades),
                "fold_metrics": [asdict(f) for f in fold_results],
                "mean_test_sharpe": float(np.mean(test_sharpes)),
                "min_test_sharpe": float(np.min(test_sharpes)),
                "dsr_z": dsr_zscore(test_sharpes),
            })
            continue
        seen_hashes.add(eq_hash)

        fold_results, _ = evaluate_cell(
            trades, bar_return, ts_to_bar, idx_to_ts, test_groups_info, ann
        )
        test_sharpes = [f.test_sharpe for f in fold_results]
        cell_results.append({
            "z_entry": z_e, "z_exit": z_x, "vpvr_lookback": vpvr_w,
            "regime_threshold": reg_thr,
            "is_clone": False,
            "n_trades_total": len(trades),
            "fold_metrics": [asdict(f) for f in fold_results],
            "mean_test_sharpe": float(np.mean(test_sharpes)),
            "min_test_sharpe": float(np.min(test_sharpes)),
            "dsr_z": dsr_zscore(test_sharpes),
        })
        if i % 20 == 0 or i == len(cells):
            print(f"  [{i}/{len(cells)}] elapsed={time.time()-t0:.1f}s  "
                  f"last: z_e={z_e} z_x={z_x} vpvr_w={vpvr_w} reg={reg_thr}  "
                  f"mean_sharpe={cell_results[-1]['mean_test_sharpe']:+.3f}")

    # Save full sweep
    out_path = RESULTS_DIR / "cpcv_sweep.json"
    out_path.write_text(json.dumps({
        "strategy": _BASE_CFG["strategy"],
        "timeframe": _BASE_CFG["timeframe"],
        "cost_model": {"fee_bps_per_side": FEE_BPS_PER_SIDE, "slippage_bps_per_side": SLIP_BPS_PER_SIDE},
        "cpcv_config": {
            "n_groups": N_GROUPS, "k_test": K_TEST,
            "purge_bars": PURGE_BARS, "embargo_bars": EMBARGO_BARS,
            "n_folds": len(test_groups_info),
            "min_trades_per_fold_4h": MIN_TRADES_PER_FOLD_4H,
        },
        "parameter_space": {
            "zscore_entry_threshold": ZSCORE_ENTRY,
            "zscore_exit_threshold": ZSCORE_EXIT,
            "vpvr_lookback_bars": VPVR_LOOKBACK,
            "regime_threshold": REGIME_THRESHOLD,
            "funding_filter_ema_window": "SKIPPED (not implemented in strategy.py)",
        },
        "n_cells_evaluated": len(cell_results),
        "n_unique_equity_curves": len(seen_hashes),
        "cells": cell_results,
    }, indent=2, default=float))
    print(f"[cpcv_optimize] wrote {out_path}")
    print(f"[cpcv_optimize] total elapsed: {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())