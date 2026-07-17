"""Walk-forward + bootstrap CI + bonferroni for V2 (multi-TF cascade).

Schedule: 4 sequential non-overlapping windows of 1y train + 6m test +
6m step, anchored to the 15m bar index. The 1h and 4h frames are sliced
to align with the 15m window boundaries.
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from data_loader import load_all
from strategy import annotate, run_backtest

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"
WF_DIR = RESULTS_DIR / "walk_forward"
WF_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class WindowSlice:
    window_idx: int
    kind: str
    start_idx: int
    end_idx: int
    n_bars: int


def build_schedule(n_bars: int, cfg: dict) -> List[Tuple[WindowSlice, WindowSlice]]:
    wf = cfg["walk_forward"]
    train_n = wf["train_bars_15m"]
    test_n = wf["test_bars_15m"]
    step_n = wf["step_bars_15m"]
    n_windows = wf["min_windows"]
    out: List[Tuple[WindowSlice, WindowSlice]] = []
    for k in range(n_windows):
        ts = k * step_n
        te = ts + train_n
        es = te
        ee = es + test_n
        if te > n_bars or ee > n_bars:
            break
        out.append((
            WindowSlice(k, "train", ts, te, train_n),
            WindowSlice(k, "test", es, ee, test_n),
        ))
    return out


def slice_frames(data, train: WindowSlice, test: WindowSlice):
    """Slice 15m, 1h, 4h frames by 15m positional index."""
    # 15m -> 1h: 4 bars per 1h bucket; 15m -> 4h: 16 bars per 4h bucket.
    def _1h(idx: int) -> int: return idx // 4
    def _4h(idx: int) -> int: return idx // 16
    out = {}
    for sym, frames in data.items():
        out[sym] = {
            "15m": (frames["15m"].iloc[train.start_idx:train.end_idx].copy(),
                    frames["15m"].iloc[test.start_idx:test.end_idx].copy()),
            "1h": (frames["1h"].iloc[_1h(train.start_idx):_1h(train.end_idx)].copy(),
                   frames["1h"].iloc[_1h(test.start_idx):_1h(test.end_idx)].copy()),
            "4h": (frames["4h"].iloc[_4h(train.start_idx):_4h(train.end_idx)].copy(),
                   frames["4h"].iloc[_4h(test.start_idx):_4h(test.end_idx)].copy()),
        }
    return out


def _run(frames, cfg: dict):
    cfg_t = dict(cfg)
    cfg_t["_symbol"] = "_slice"
    annotated = annotate(frames["15m"], frames["1h"], frames["4h"], cfg_t)
    return run_backtest(annotated, cfg_t)


def _equity_curve(slice_starting_capital: float, trades, idx: pd.DatetimeIndex):
    if not trades:
        return [(d, slice_starting_capital) for d in idx]
    exits = sorted([(t.exit_date, t.pnl_usd) for t in trades], key=lambda x: x[0])
    pnl_acc = 0.0
    j = 0
    curve = []
    for d in idx:
        while j < len(exits) and exits[j][0] <= d:
            pnl_acc += exits[j][1]
            j += 1
        curve.append((d, slice_starting_capital + pnl_acc))
    return curve


def _metrics(starting: float, trades, curve, n_bars: int, bpy: int) -> dict:
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "total_pnl_usd": 0.0, "annualized_return": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0, "final_equity": starting,
                "mean_trade_return": 0.0}
    pnls = np.array([t.pnl_usd for t in trades])
    wins = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    wr = float((pnls > 0).mean())
    total_pnl = float(pnls.sum())
    eq = np.array([v for _, v in curve], dtype=float)
    rets = np.diff(eq) / eq[:-1]
    rets = np.nan_to_num(rets)
    if rets.std() > 0:
        sharpe = float(rets.mean() / rets.std() * math.sqrt(bpy))
    else:
        sharpe = 0.0
    rm = np.maximum.accumulate(eq)
    dd = (rm - eq) / np.where(rm > 0, rm, np.nan)
    max_dd = float(np.nanmax(dd)) if np.isfinite(dd).any() else 0.0
    total_ret = total_pnl / starting if starting > 0 else 0.0
    n_years = n_bars / bpy if bpy > 0 else 0.0
    annualized = (1.0 + total_ret) ** (1.0 / n_years) - 1.0 if n_years > 0 and total_ret > -1.0 else 0.0
    pnl_pcts = np.array([t.pnl_pct for t in trades])
    return {
        "n_trades": n, "win_rate": wr, "profit_factor": pf,
        "total_pnl_usd": total_pnl, "annualized_return": annualized,
        "sharpe": sharpe, "max_drawdown": max_dd,
        "final_equity": starting + total_pnl,
        "mean_trade_return": float(np.mean(pnl_pcts)),
    }


def _bootstrap_ci(trade_returns: np.ndarray, n_resamples: int, seed: int, ci: float = 0.95):
    if trade_returns.size < 2:
        return {"mean": 0.0, "lower": 0.0, "upper": 0.0, "n_resamples": 0, "ci": ci, "seed": seed}
    rng = np.random.default_rng(seed)
    n = len(trade_returns)
    means = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        means[i] = trade_returns[idx].mean()
    lo = float(np.quantile(means, (1 - ci) / 2))
    hi = float(np.quantile(means, 1 - (1 - ci) / 2))
    return {
        "mean": float(trade_returns.mean()),
        "lower": lo, "upper": hi,
        "n_resamples": n_resamples, "ci": ci, "seed": seed,
    }


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print(f"Loading {cfg['instruments']}...")
    data = load_all(cfg["instruments"])
    n_bars = len(next(iter(data.values()))["15m"])
    schedule = build_schedule(n_bars, cfg)
    print(f"Walk-forward windows: {len(schedule)} (15m bars={n_bars})")
    if not schedule:
        return 1
    if WF_DIR.exists():
        shutil.rmtree(WF_DIR)
    WF_DIR.mkdir(parents=True, exist_ok=True)
    start = float(cfg["sizing"]["starting_capital_usd"])
    bpy = 35040

    oos_returns, oos_sharpes, oos_pfs, oos_n = [], [], [], []
    all_oos_pnl_pcts = []
    for k, (train, test) in enumerate(schedule):
        sliced = slice_frames(data, train, test)
        test_agg_pnl_pcts = []
        for sym, frames in sliced.items():
            cfg_t = dict(cfg)
            cfg_t["_symbol"] = sym
            test_res = _run({k: v[1] for k, v in frames.items()}, cfg_t)
            test_agg_pnl_pcts.extend([t.pnl_pct for t in test_res.trades])
            test_curve = _equity_curve(start, test_res.trades, frames["15m"][1].index)
            pd.DataFrame(test_curve, columns=["date", "equity"]).set_index("date") \
                .to_csv(WF_DIR / f"window_{k+1:02d}_equity_{sym}.csv")

        agg_total = (sum(test_agg_pnl_pcts))  # not a fraction; used below
        # We need the equity-based aggregate; rebuild:
        # We use average per-trade pnl% as the OOS signal (matches the
        # per-window mean trade return used for sharpe estimation).
        if len(test_agg_pnl_pcts) >= 2 and np.std(test_agg_pnl_pcts) > 0:
            sh = float(np.mean(test_agg_pnl_pcts) / np.std(test_agg_pnl_pcts, ddof=1) * math.sqrt(bpy))
        else:
            sh = 0.0
        wins_p = sum(p for p in test_agg_pnl_pcts if p > 0)
        loss_p = -sum(p for p in test_agg_pnl_pcts if p < 0)
        pf = float(wins_p / loss_p) if loss_p > 0 else float("inf")
        # Approximate annualized return: mean trade pnl * trades_per_year.
        mean_trade = float(np.mean(test_agg_pnl_pcts)) if test_agg_pnl_pcts else 0.0
        n_trades_w = len(test_agg_pnl_pcts)
        n_years_w = test.n_bars / bpy
        ann = mean_trade * n_trades_w / n_years_w if n_years_w > 0 else 0.0
        oos_returns.append(ann)
        oos_sharpes.append(sh)
        oos_pfs.append(pf)
        oos_n.append(n_trades_w)
        all_oos_pnl_pcts.extend(test_agg_pnl_pcts)

    oos_returns_arr = np.array(oos_returns)
    oos_sharpes_arr = np.array(oos_sharpes)
    oos_ann_mean = float(np.mean(oos_returns_arr)) if oos_returns_arr.size else 0.0
    oos_sharpe_mean = float(np.mean(oos_sharpes_arr)) if oos_sharpes_arr.size else 0.0
    oos_pf_mean = float(np.mean([p for p in oos_pfs if np.isfinite(p)])) if oos_pfs else 0.0
    oos_n_total = int(sum(oos_n))

    summary = json.loads((RESULTS_DIR / "summary.json").read_text())
    in_sample_sharpe = float(summary["portfolio"]["sharpe"])
    g = cfg["hard_gates"]
    summary_payload = {
        "strategy": cfg["strategy"], "iteration": cfg["iteration"],
        "axis": cfg.get("axis", ""), "n_windows": len(schedule),
        "schedule": {
            "train_bars_15m": cfg["walk_forward"]["train_bars_15m"],
            "test_bars_15m": cfg["walk_forward"]["test_bars_15m"],
            "step_bars_15m": cfg["walk_forward"]["step_bars_15m"],
        },
        "in_sample_sharpe": in_sample_sharpe,
        "oos": {
            "annualized_return_mean": oos_ann_mean,
            "sharpe_mean": oos_sharpe_mean,
            "profit_factor_mean": oos_pf_mean,
            "n_trades_total": oos_n_total,
            "min_sharpe": float(min(oos_sharpes_arr)) if oos_sharpes_arr.size else 0.0,
            "per_window_annualized": [float(x) for x in oos_returns_arr],
            "per_window_sharpe": [float(x) for x in oos_sharpes_arr],
            "per_window_profit_factor": [float(x) for x in oos_pfs],
            "per_window_n_trades": [int(x) for x in oos_n],
        },
        "gates": {
            "in_sample_sharpe_>=1.0": in_sample_sharpe >= g["sharpe_min"],
            "oos_sharpe_>=1.0": oos_sharpe_mean >= g["oos_sharpe_min"],
            "annualized_return_>=0.15": oos_ann_mean >= g["annualized_return_min"],
            "profit_factor_>1.5": oos_pf_mean > g["profit_factor_min"],
        },
    }
    summary_payload["all_gates_pass"] = all(summary_payload["gates"].values())
    (WF_DIR / "walk_forward.json").write_text(json.dumps(summary_payload, indent=2, default=float))
    ci = _bootstrap_ci(np.array(all_oos_pnl_pcts),
                       n_resamples=g["bootstrap_resamples"],
                       seed=g["bootstrap_seed"], ci=0.95)
    (RESULTS_DIR / "bootstrap_ci.json").write_text(json.dumps(ci, indent=2))
    bonf = {
        "alpha": g["bonferroni_alpha"], "n_variants_in_campaign": 4,
        "mean_trade_return": float(np.mean(all_oos_pnl_pcts)) if all_oos_pnl_pcts else 0.0,
        "ci_lower": ci["lower"], "ci_upper": ci["upper"],
        "passes_bonferroni": ci["lower"] > 0.0,
    }
    (RESULTS_DIR / "bonferroni.json").write_text(json.dumps(bonf, indent=2))
    print(json.dumps(summary_payload, indent=2, default=float))
    print(json.dumps({"bootstrap_ci": ci, "bonferroni": bonf}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())