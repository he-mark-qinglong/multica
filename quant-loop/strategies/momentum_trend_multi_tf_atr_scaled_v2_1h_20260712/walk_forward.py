"""Walk-forward validation for momentum_trend_multi_tf_atr_scaled_1h_20260712.

Schedule (per spec):

    4 sequential non-overlapping windows
    train 1y (8760 1h bars) + test 6m (4380 1h bars) + step 6m (4380 1h bars)

For window k (0..3):
    train: [k*step, k*step + 8760]
    test : [k*step + 8760, k*step + 13140]

The schedule is **anchored to the 1h grid**. The 4h frame for each window
is sliced the same way (1y of 4h = 2190 bars, 6m of 4h = 1095 bars) so
the 4h trend filter is consistent within each window.

Outputs:
    results/walk_forward/
        windows.json
        per_window_<NN>_<train|test>/
            trades_<SYM>.csv
            equity_<SYM>.csv
            metrics_<SYM>.json
        walk_forward_summary.json

Issue-scoped evidence gate (from issue description):
    in-sample sharpe >= 0.5
    wf_ratio >= 0.5
    min_oos_sharpe >= 0 (no negative OOS windows)

Cycle-46 lessons apply (this is one cycle-46 family iteration):
    1. in-sample != OOS Sharpe (full-period overfit). wf_ratio is the
       primary acceptance metric, not in-sample Sharpe alone.
    2. trend filters destroy carry. OOS Sharpe often drops below 0 even
       when the in-sample looks OK. Be ready to archive.
    3. framework CV must walk-forward. Deferred (out of scope for this
       iteration).

Below any gate -> archive path, status done with [NOT-PROFITABLE] verdict.
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from data_loader import load_all
from strategy import annotate, run_backtest


# ---------------------------------------------------------------------------
# Schedule constants.
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"
WF_DIR = RESULTS_DIR / "walk_forward"


# ---------------------------------------------------------------------------
# Window definition.
# ---------------------------------------------------------------------------

@dataclass
class WindowSlice:
    window_idx: int
    slice_kind: str  # "train" or "test"
    start_idx: int
    end_idx: int
    n_bars: int


def build_schedule(n_bars: int, cfg: dict) -> List[Tuple[int, WindowSlice, WindowSlice]]:
    """Build the 4-window schedule. The schedule is clamped to n_bars; any
    window whose train or test extends past n_bars is dropped."""
    wf = cfg["walk_forward"]
    train_n = wf["train_bars_1h"]
    test_n = wf["test_bars_1h"]
    step_n = wf["step_bars_1h"]
    n_windows = wf["n_windows"]

    schedule: List[Tuple[int, WindowSlice, WindowSlice]] = []
    for k in range(n_windows):
        train_start = k * step_n
        train_end = train_start + train_n
        test_start = train_end
        test_end = test_start + test_n
        if train_end > n_bars:
            break
        if test_end > n_bars:
            # partial test window — drop to keep OOS comparable
            break
        train_slice = WindowSlice(
            window_idx=k, slice_kind="train",
            start_idx=train_start, end_idx=train_end, n_bars=train_n,
        )
        test_slice = WindowSlice(
            window_idx=k, slice_kind="test",
            start_idx=test_start, end_idx=test_end, n_bars=test_n,
        )
        schedule.append((k, train_slice, test_slice))
    return schedule


def slice_1h_4h(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    start_idx: int,
    end_idx: int,
    bars_per_4h: int = 4,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Slice the 1h + 4h frames by 1h positional index. The 4h slice is
    derived from the 1h start/end (1 4h bar = 4 1h bars)."""
    s4 = start_idx // bars_per_4h
    e4 = max(end_idx // bars_per_4h, s4 + 1)
    return df_1h.iloc[start_idx:end_idx].copy(), df_4h.iloc[s4:e4].copy()


# ---------------------------------------------------------------------------
# Per-bar per-symbol equity reconstruction.
# ---------------------------------------------------------------------------

def _per_symbol_equity_curve(
    slice_starting_capital: float,
    trades,
    df_1h: pd.DataFrame,
) -> List[Tuple[pd.Timestamp, float]]:
    """Build a per-bar per-symbol equity curve for a window slice.

    PnL accrues at each trade's exit_date. Between exits, equity is carried
    forward at constant value.
    """
    schedule = list(df_1h.index)
    if not trades:
        return [(d, slice_starting_capital) for d in schedule]

    exit_events = sorted([(t.exit_date, t.pnl_usd) for t in trades], key=lambda x: x[0])
    pnl_acc = 0.0
    exit_idx = 0
    curve: List[Tuple[pd.Timestamp, float]] = []
    for d in schedule:
        while exit_idx < len(exit_events) and exit_events[exit_idx][0] <= d:
            _, pnl = exit_events[exit_idx]
            pnl_acc += pnl
            exit_idx += 1
        curve.append((d, slice_starting_capital + pnl_acc))
    return curve


def _metrics(
    slice_starting_capital: float,
    trades,
    equity_curve: List[Tuple[pd.Timestamp, float]],
    n_bars: int,
    bars_per_year: int,
) -> dict:
    """Compute metrics for a (window, slice, symbol) tuple."""
    n = len(trades)
    if n == 0:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_pnl_usd": 0.0,
            "annualized_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "final_equity": slice_starting_capital,
        }
    pnl_usds = np.array([t.pnl_usd for t in trades])
    wins = pnl_usds[pnl_usds > 0].sum()
    losses = -pnl_usds[pnl_usds < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    win_rate = float((pnl_usds > 0).mean())
    total_pnl = float(pnl_usds.sum())

    eq = np.array([v for _, v in equity_curve], dtype=float)
    if len(eq) >= 2 and eq[0] > 0:
        rets = np.diff(eq) / eq[:-1]
        rets = np.nan_to_num(rets, nan=0.0)
        if rets.std() > 0:
            sharpe = float(rets.mean() / rets.std() * math.sqrt(bars_per_year))
        else:
            sharpe = 0.0
        running_max = np.maximum.accumulate(eq)
        dd = (running_max - eq) / np.where(running_max > 0, running_max, np.nan)
        max_dd = float(np.nanmax(dd)) if np.isfinite(dd).any() else 0.0
    else:
        sharpe = 0.0
        max_dd = 0.0

    total_ret = total_pnl / slice_starting_capital if slice_starting_capital > 0 else 0.0
    n_years = (n_bars / bars_per_year) if bars_per_year > 0 else 0.0
    if total_ret <= -1.0:
        annualized = -1.0
    else:
        annualized = (1.0 + total_ret) ** (1.0 / n_years) - 1.0 if n_years > 0 else 0.0

    return {
        "n_trades": n,
        "win_rate": win_rate,
        "profit_factor": pf,
        "total_pnl_usd": total_pnl,
        "annualized_return": annualized,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "final_equity": slice_starting_capital + total_pnl,
    }


# ---------------------------------------------------------------------------
# Per-window driver.
# ---------------------------------------------------------------------------

def _run_slice(
    data: Dict[str, Dict[str, pd.DataFrame]],
    slice_def: WindowSlice,
    cfg: dict,
    out_dir: Path,
    starting_capital: float,
    bars_per_year: int,
) -> Dict[str, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sym_metrics: Dict[str, dict] = {}

    for sym, frames in data.items():
        df_1h, df_4h = slice_1h_4h(
            frames["1h"], frames["4h"], slice_def.start_idx, slice_def.end_idx,
        )
        cfg_t = dict(cfg)
        cfg_t["_symbol"] = sym
        annotated = annotate(df_1h, df_4h, cfg_t)
        result = run_backtest(annotated, cfg_t)

        eq_curve = _per_symbol_equity_curve(starting_capital, result.trades, df_1h)
        eq_df = pd.DataFrame(eq_curve, columns=["date", "equity"]).set_index("date")
        eq_df.to_csv(out_dir / f"equity_{sym}.csv")

        # Trades CSV (empty if no trades)
        if result.trades:
            trades_df = pd.DataFrame([{
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_date": t.entry_date.date().isoformat() if t.entry_date else None,
                "entry_price": t.entry_price,
                "exit_date": t.exit_date.date().isoformat() if t.exit_date else None,
                "exit_price": t.exit_price,
                "reason": t.reason,
                "pnl_usd": t.pnl_usd,
                "pnl_pct": t.pnl_pct,
                "bars_held": t.bars_held,
                "atr_1h_at_entry": t.atr_1h_at_entry,
                "ema50_4h_at_entry": t.ema50_4h_at_entry,
                "ema50_4h_slope_at_entry": t.ema50_4h_slope_at_entry,
            } for t in result.trades])
        else:
            trades_df = pd.DataFrame()
        trades_df.to_csv(out_dir / f"trades_{sym}.csv", index=False)

        m = _metrics(starting_capital, result.trades, eq_curve, slice_def.n_bars, bars_per_year)
        sym_metrics[sym] = m
        (out_dir / f"metrics_{sym}.json").write_text(json.dumps(m, indent=2, default=float))

    return sym_metrics


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print(f"Loading data for {cfg['instruments']}...")
    data = load_all(cfg["instruments"])

    # Use the first symbol's 1h bar count to derive the schedule.
    n_bars = len(next(iter(data.values()))["1h"])
    print(f"Total 1h bars available: {n_bars}")
    schedule = build_schedule(n_bars, cfg)
    print(f"Walk-forward windows scheduled: {len(schedule)}")
    if not schedule:
        print("ERROR: not enough bars for any walk-forward window", flush=True)
        return 1

    if WF_DIR.exists():
        shutil.rmtree(WF_DIR)
    WF_DIR.mkdir(parents=True, exist_ok=True)

    starting_capital = float(cfg["starting_capital_usd"])
    bars_per_year = 8760

    # Persist the schedule.
    schedule_payload = []
    for k, train, test in schedule:
        schedule_payload.append({
            "window": k,
            "train": {"start_idx": train.start_idx, "end_idx": train.end_idx, "n_bars": train.n_bars},
            "test": {"start_idx": test.start_idx, "end_idx": test.end_idx, "n_bars": test.n_bars},
        })
    (WF_DIR / "windows.json").write_text(json.dumps({
        "n_windows_planned": cfg["walk_forward"]["n_windows"],
        "n_windows_run": len(schedule),
        "train_bars_1h": cfg["walk_forward"]["train_bars_1h"],
        "test_bars_1h": cfg["walk_forward"]["test_bars_1h"],
        "step_bars_1h": cfg["walk_forward"]["step_bars_1h"],
        "windows": schedule_payload,
    }, indent=2))

    oos_returns: List[float] = []
    oos_sharpes: List[float] = []
    oos_pf: List[float] = []
    oos_max_dd: List[float] = []
    oos_n_trades: List[int] = []

    in_sample_sharpes: List[float] = []  # full per-symbol train Sharpe (1y)

    for k, train_slice, test_slice in schedule:
        train_dir = WF_DIR / f"per_window_{k+1:02d}_train"
        train_metrics = _run_slice(
            data, train_slice, cfg, train_dir, starting_capital, bars_per_year,
        )
        in_sample_sharpes.extend([m["sharpe"] for m in train_metrics.values()])

        test_dir = WF_DIR / f"per_window_{k+1:02d}_test"
        test_metrics = _run_slice(
            data, test_slice, cfg, test_dir, starting_capital, bars_per_year,
        )

        all_trades_n = sum(m["n_trades"] for m in test_metrics.values())
        agg_pnl = sum(m["total_pnl_usd"] for m in test_metrics.values())
        agg_total_return = agg_pnl / starting_capital
        agg_annualized = ((1.0 + agg_total_return) ** (bars_per_year / test_slice.n_bars) - 1.0) \
            if test_slice.n_bars > 0 else 0.0
        all_pnl_pcts: List[float] = []
        for s in test_metrics:
            tr_path = test_dir / f"trades_{s}.csv"
            if tr_path.exists() and tr_path.stat().st_size > 0:
                tr = pd.read_csv(tr_path)
                all_pnl_pcts.extend(tr["pnl_pct"].tolist())
        wins_p = sum(p for p in all_pnl_pcts if p > 0)
        losses_p = -sum(p for p in all_pnl_pcts if p < 0)
        agg_pf = (wins_p / losses_p) if losses_p > 0 else float("inf")
        if len(all_pnl_pcts) >= 2 and np.std(all_pnl_pcts) > 0:
            agg_sharpe = float(np.mean(all_pnl_pcts) / np.std(all_pnl_pcts, ddof=1) * math.sqrt(bars_per_year))
        else:
            agg_sharpe = 0.0
        agg_max_dd = max((m["max_drawdown"] for m in test_metrics.values()), default=0.0)

        oos_returns.append(agg_annualized)
        oos_sharpes.append(agg_sharpe)
        oos_pf.append(agg_pf)
        oos_max_dd.append(agg_max_dd)
        oos_n_trades.append(all_trades_n)

    # ----- Aggregate OOS metrics -----
    oos_annualized_mean = float(np.mean(oos_returns)) if oos_returns else 0.0
    oos_sharpe_mean = float(np.nanmean([s for s in oos_sharpes if np.isfinite(s)])) if oos_sharpes else 0.0
    oos_pf_mean = float(np.nanmean([p for p in oos_pf if np.isfinite(p)])) if oos_pf else 0.0
    oos_max_dd_max = float(max(oos_max_dd)) if oos_max_dd else 0.0
    oos_n_trades_total = int(sum(oos_n_trades))
    min_oos_sharpe = float(min(oos_sharpes)) if oos_sharpes else 0.0

    # ----- in-sample Sharpe from B2 summary.json (full-period backtest) -----
    summary = json.loads((RESULTS_DIR / "summary.json").read_text())
    in_sample_sharpe = float(summary["portfolio"]["sharpe"])
    in_sample_total_return = float(summary["portfolio"]["total_return"])
    in_sample_n_years = float(summary["portfolio"]["n_years"])

    # ----- wf_ratio -----
    if in_sample_sharpe > 0:
        wf_ratio = float(oos_sharpe_mean / in_sample_sharpe)
    else:
        wf_ratio = 0.0 if oos_sharpe_mean <= 0 else float("inf")

    # ----- Issue-scoped evidence gate -----
    gate_cfg = cfg["evidence_gate"]
    in_sample_sharpe_ok = in_sample_sharpe >= gate_cfg["in_sample_sharpe_min"]
    wf_ratio_ok = wf_ratio >= gate_cfg["wf_ratio_min"]
    min_oos_ok = min_oos_sharpe >= gate_cfg["min_oos_sharpe_min"]
    all_gates_pass = in_sample_sharpe_ok and wf_ratio_ok and min_oos_ok

    verdict = "ship" if all_gates_pass else "[NOT-PROFITABLE]"

    out = {
        "strategy": cfg["strategy"],
        "iteration": cfg["iteration"],
        "axis": cfg.get("axis", ""),
        "n_windows_run": len(schedule),
        "n_windows_planned": cfg["walk_forward"]["n_windows"],
        "schedule": {
            "train_bars_1h": cfg["walk_forward"]["train_bars_1h"],
            "test_bars_1h": cfg["walk_forward"]["test_bars_1h"],
            "step_bars_1h": cfg["walk_forward"]["step_bars_1h"],
        },
        "in_sample": {
            "sharpe": in_sample_sharpe,
            "total_return": in_sample_total_return,
            "n_years": in_sample_n_years,
        },
        "oos": {
            "annualized_return_mean": oos_annualized_mean,
            "sharpe_mean": oos_sharpe_mean,
            "profit_factor_mean": oos_pf_mean,
            "max_drawdown_max": oos_max_dd_max,
            "n_trades_total": oos_n_trades_total,
            "min_sharpe": min_oos_sharpe,
            "per_window_annualized": oos_returns,
            "per_window_sharpe": oos_sharpes,
            "per_window_profit_factor": oos_pf,
            "per_window_max_dd": oos_max_dd,
            "per_window_n_trades": oos_n_trades,
        },
        "wf_ratio": wf_ratio,
        "gates": {
            "in_sample_sharpe_>=0.5": in_sample_sharpe_ok,
            "wf_ratio_>=0.5": wf_ratio_ok,
            "min_oos_sharpe_>=0": min_oos_ok,
        },
        "all_gates_pass": all_gates_pass,
        "verdict": verdict,
    }
    (WF_DIR / "walk_forward_summary.json").write_text(json.dumps(out, indent=2, default=float))
    print(json.dumps(out, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())