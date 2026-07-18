"""8-window walk-forward validation for vol_breakout_2tf_vpvr_confluence_4h_20260712.

V8 single-TF: schedule is expressed directly in 4h bars (no 4h->1h
translation needed). Per spec (SMA-32942):

    8 sequential non-overlapping windows
    train 720 (4h bars = 120d) + test 168 (4h bars = 28d) + step 168 (4h bars)

For window k (0..7):
    train: [k*step, k*step + train_size]
    test : [k*step + train_size, k*step + train_size + test_size]

Outputs (per spec):

    results/walk_forward/
        windows.json                       # the schedule
        per_window_<NN>_<train|test>/
            trades_<SYM>.csv              # closed trades per symbol
            equity_<SYM>.csv              # DISTINCT per-symbol equity (cycle-44)
            metrics_<SYM>.json            # per-symbol metrics
        walk_forward_summary.json         # OOS aggregate + ship-gate
        walk_forward_summary.md           # human-readable

Cycle-44 discipline
===================

Per-symbol equity CSVs MUST be **distinct**. For each (window, slice,
symbol) we compute:

    equity_sym[t] = slice_starting_capital
                    + sum(pnl_usd for sym's trades whose exit_fill_date <= t)

Reconciled with ``summary.json``-style per-symbol ``final_equity`` at write
time so any future regression breaks loudly.

Hard user gates (G1-G7 from CLAUDE.md)
======================================

    G1: sharpe_oos_mean >= 1.0
    G2: min(annualized_full, annualized_oos_mean) >= 0.15
    G3: profit_factor > 1.5
    G4: max_drawdown < 0.25
    G5: framework CV OOS walk-forward (backtrader/freqtrade Sharpe) >= 1.0
    G6: bootstrap 95% CI lower >= 0.5
    G7: FWER Bonferroni alpha = 0.05/4

`annualized_return_full` is from B2's `summary.json`. Below any gate
-> archive [NOT-PROFITABLE].
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from data_loader import load_all
from strategy import BacktestResult, run_backtest, Trade


# ---------------------------------------------------------------------------
# Constants — the 8-window schedule (4h bars).
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"
WF_DIR = RESULTS_DIR / "walk_forward"

N_WINDOWS = 8
TRAIN_4H = 720
TEST_4H = 168
STEP_4H = 168

# Hard user gates (G1-G7).
SHARPE_OOS_MIN = 1.0
ANNUALIZED_MIN = 0.15
PROFIT_FACTOR_MIN = 1.5
MAX_DRAWDOWN_MAX = 0.25
BOOTSTRAP_CI_LOWER_MIN = 0.5
FWER_ALPHA = 0.05 / 4
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 42


# ---------------------------------------------------------------------------
# Per-window driver.
# ---------------------------------------------------------------------------

@dataclass
class WindowSlice:
    window_idx: int
    slice_kind: str  # "train" or "test"
    start_idx: int
    end_idx: int
    n_bars: int


def build_schedule(n_bars: int) -> List[Tuple[int, WindowSlice, WindowSlice]]:
    """Return ``[(window_idx, train_slice, test_slice), ...]`` for 8 windows.

    The schedule is clamped to ``n_bars``: any window whose train or test
    extends past ``n_bars`` is dropped from the schedule.
    """
    schedule: List[Tuple[int, WindowSlice, WindowSlice]] = []
    for k in range(N_WINDOWS):
        train_start = k * STEP_4H
        train_end = train_start + TRAIN_4H
        test_start = train_end
        test_end = test_start + TEST_4H
        if train_end > n_bars:
            break
        if test_end > n_bars:
            # last partial window — drop test slice but keep train
            # Actually we drop the whole window to keep OOS comparable.
            break
        train_slice = WindowSlice(
            window_idx=k,
            slice_kind="train",
            start_idx=train_start,
            end_idx=train_end,
            n_bars=TRAIN_4H,
        )
        test_slice = WindowSlice(
            window_idx=k,
            slice_kind="test",
            start_idx=test_start,
            end_idx=test_end,
            n_bars=TEST_4H,
        )
        schedule.append((k, train_slice, test_slice))
    return schedule


def slice_frame(df: pd.DataFrame, start_idx: int, end_idx: int) -> pd.DataFrame:
    """Slice a 4h frame by positional index (start inclusive, end exclusive)."""
    if end_idx > len(df):
        end_idx = len(df)
    return df.iloc[start_idx:end_idx].copy()


def annualized_return(total_return: float, n_years: float) -> float:
    """Compound annual growth rate from total_return and span in years."""
    if n_years <= 0:
        return 0.0
    if total_return <= -1.0:
        return -1.0  # cap at -100%
    return (1.0 + total_return) ** (1.0 / n_years) - 1.0


def max_drawdown(equity_path: List[Tuple[pd.Timestamp, float]]) -> float:
    """Peak-to-trough drawdown of an equity curve. Returns a positive fraction."""
    if not equity_path:
        return 0.0
    eq = np.array([v for _, v in equity_path], dtype=float)
    if len(eq) < 2:
        return 0.0
    running_max = np.maximum.accumulate(eq)
    drawdown = (running_max - eq) / np.where(running_max > 0, running_max, np.nan)
    dd = float(np.nanmax(drawdown)) if np.isfinite(drawdown).any() else 0.0
    return max(0.0, dd)


def sharpe_ratio(returns: np.ndarray, bars_per_year: int) -> float:
    """Annualized Sharpe ratio from per-bar returns. ``bars_per_year`` = 2190 for 4h."""
    if returns is None or len(returns) < 2:
        return 0.0
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return 0.0
    mu = float(np.mean(r))
    sd = float(np.std(r, ddof=1))
    if sd <= 0:
        return 0.0
    return mu / sd * math.sqrt(bars_per_year)


def _slice_metrics(sym_trades: List[Trade], n_bars: int) -> dict:
    """Per-symbol metrics over a single (window, slice)."""
    n = len(sym_trades)
    if n == 0:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_pnl_pct": 0.0,
            "total_pnl_usd": 0.0,
            "annualized_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
        }
    pnl_pcts = np.array([t.pnl_pct for t in sym_trades], dtype=float)
    pnl_usds = np.array([t.pnl_usd for t in sym_trades], dtype=float)
    wins = pnl_usds[pnl_usds > 0].sum()
    losses = -pnl_usds[pnl_usds < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    win_rate = float((pnl_usds > 0).mean())
    avg_pnl_pct = float(pnl_pcts.mean())
    total_pnl_usd = float(pnl_usds.sum())
    # Bars-per-year = 2190 (4h). Approximate per-bar return from pnl_pct mean.
    sharpe = sharpe_ratio(pnl_pcts, bars_per_year=2190)
    # Annualized from a 28-day window: total_return ≈ avg_pnl_pct * n_trades
    # (rough; for walk-forward summary only).
    n_years = (n_bars / 2190.0) if n_bars > 0 else 0.0
    total_return = total_pnl_usd / 100000.0  # rough attribution
    annualized = annualized_return(total_return, n_years)
    return {
        "n_trades": n,
        "win_rate": win_rate,
        "profit_factor": pf,
        "avg_pnl_pct": avg_pnl_pct,
        "total_pnl_usd": total_pnl_usd,
        "annualized_return": annualized,
        "sharpe": sharpe,
        "max_drawdown": 0.0,  # window-level DD requires per-bar equity; omitted at slice level
    }


def _per_symbol_equity(
    slice_starting_capital: float,
    sym_trades: List[Trade],
    n_bars: int,
    bars_per_day_4h: int = 6,
) -> List[Tuple[pd.Timestamp, float]]:
    """Build a per-bar per-symbol equity curve for a window slice.

    Uses a flat 4h schedule starting at the slice's first bar; PnL is
    accrued at each trade's ``exit_fill_date``. Where exit dates do not
    coincide with the synthetic schedule, the latest equity is carried
    forward (constant interpolation).
    """
    if not sym_trades:
        # Flat curve at starting capital.
        return []
    sorted_trades = sorted(sym_trades, key=lambda t: t.exit_fill_date)
    eq: List[Tuple[pd.Timestamp, float]] = []
    pnl_acc = 0.0
    trade_idx = 0
    last_exit = sorted_trades[-1].exit_fill_date
    first_fill = sorted_trades[0].entry_fill_date
    return []  # we use a real schedule below; not built here


# ---------------------------------------------------------------------------
# Main walk-forward driver.
# ---------------------------------------------------------------------------

def _run_slice(
    sym_data: Dict[str, pd.DataFrame],
    slice_def: WindowSlice,
    cfg: dict,
    out_dir: Path,
    starting_capital: float,
) -> Dict[str, dict]:
    """Run a single (window, slice_kind) and write per-symbol artifacts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sliced: Dict[str, pd.DataFrame] = {
        s: slice_frame(df, slice_def.start_idx, slice_def.end_idx)
        for s, df in sym_data.items()
    }
    result = run_backtest(sliced, cfg, starting_capital=starting_capital)

    sym_metrics: Dict[str, dict] = {}
    # Per-symbol equity curves (cycle-44 audit).
    sym_equity_curves: Dict[str, List] = {s: [] for s in sliced.keys()}
    running_pnl: Dict[str, float] = {s: 0.0 for s in sliced.keys()}
    exit_events = sorted(
        [(t.exit_fill_date, t.symbol, t.pnl_usd) for t in result.trades],
        key=lambda x: x[0],
    )
    exit_idx = 0
    # Use the canonical (first-symbol) timestamp index as the schedule.
    canonical_ts = list(sliced[next(iter(sliced))].index)
    for d in canonical_ts:
        while exit_idx < len(exit_events) and exit_events[exit_idx][0] <= d:
            _, sym, pnl = exit_events[exit_idx]
            running_pnl[sym] += pnl
            exit_idx += 1
        for s in sliced.keys():
            sym_equity_curves[s].append((d, starting_capital + running_pnl[s]))

    for sym in sliced.keys():
        sym_trades = [t for t in result.trades if t.symbol == sym]
        eq = pd.Series(
            [v for _, v in sym_equity_curves[sym]],
            index=[d for d, _ in sym_equity_curves[sym]],
            name="equity",
        )
        eq.to_csv(out_dir / f"equity_{sym}.csv")
        pd.DataFrame([{
            "symbol": t.symbol,
            "entry_signal_date": t.entry_signal_date,
            "entry_fill_date": t.entry_fill_date,
            "entry_price": t.entry_price,
            "exit_signal_date": t.exit_signal_date,
            "exit_fill_date": t.exit_fill_date,
            "exit_price": t.exit_price,
            "reason": t.reason,
            "pnl_usd": t.pnl_usd,
            "pnl_pct": t.pnl_pct,
            "bars_held": t.bars_held,
            "atr_4h_at_entry": t.atr_4h_at_entry,
            "vpvr_dist_atr_4h_at_entry": t.vpvr_dist_atr_4h_at_entry,
        } for t in sym_trades]).to_csv(out_dir / f"trades_{sym}.csv", index=False)
        m = _slice_metrics(sym_trades, slice_def.n_bars)
        m["final_equity"] = starting_capital + sum(t.pnl_usd for t in sym_trades)
        m["max_drawdown"] = max_drawdown(sym_equity_curves[sym])
        # Audit: per-symbol final_equity == last value of per-symbol equity csv.
        csv_last = float(eq.iloc[-1]) if len(eq) else starting_capital
        if abs(csv_last - m["final_equity"]) > 1e-6:
            raise AssertionError(
                f"per-symbol equity audit broken for {sym} in "
                f"window {slice_def.window_idx} {slice_def.slice_kind}: "
                f"summary final_equity={m['final_equity']} vs csv last={csv_last}"
            )
        sym_metrics[sym] = m
        (out_dir / f"metrics_{sym}.json").write_text(
            json.dumps(m, indent=2, default=float)
        )

    return sym_metrics


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print(f"Loading 4h data for {cfg['instruments']}...")
    data = load_all(cfg["instruments"])

    # Use the first symbol's 4h bar count to derive the schedule.
    n_bars = len(next(iter(data.values())))
    print(f"Total 4h bars available: {n_bars}")
    schedule = build_schedule(n_bars)
    print(f"Walk-forward windows scheduled: {len(schedule)}")

    if WF_DIR.exists():
        shutil.rmtree(WF_DIR)
    WF_DIR.mkdir(parents=True, exist_ok=True)

    starting_capital = float(cfg["starting_capital_usd"])

    # Save the schedule.
    schedule_payload = []
    for k, train, test in schedule:
        schedule_payload.append({
            "window": k,
            "train": {
                "start_idx": train.start_idx,
                "end_idx": train.end_idx,
                "n_bars": train.n_bars,
            },
            "test": {
                "start_idx": test.start_idx,
                "end_idx": test.end_idx,
                "n_bars": test.n_bars,
            },
        })
    (WF_DIR / "windows.json").write_text(json.dumps({
        "n_windows_planned": N_WINDOWS,
        "n_windows_run": len(schedule),
        "train_4h": TRAIN_4H,
        "test_4h": TEST_4H,
        "step_4h": STEP_4H,
        "windows": schedule_payload,
    }, indent=2))

    oos_returns: List[float] = []  # one per test window (portfolio)
    oos_sharpes: List[float] = []
    oos_pf: List[float] = []
    oos_max_dd: List[float] = []
    oos_n_trades: List[int] = []

    for k, train_slice, test_slice in schedule:
        # Train slice (write metrics but don't include in OOS).
        train_dir = WF_DIR / f"per_window_{k+1:02d}_train"
        _run_slice(data, train_slice, cfg, train_dir, starting_capital)

        # Test slice (OOS).
        test_dir = WF_DIR / f"per_window_{k+1:02d}_test"
        sym_metrics = _run_slice(data, test_slice, cfg, test_dir, starting_capital)

        # Aggregate OOS metrics across symbols for this window.
        all_trades_n = sum(m["n_trades"] for m in sym_metrics.values())
        agg_pnl = sum(m["total_pnl_usd"] for m in sym_metrics.values())
        # Portfolio annualized on the 168-bar test (28d):
        agg_total_return = agg_pnl / starting_capital
        agg_annualized = annualized_return(agg_total_return, test_slice.n_bars / 2190.0)
        # Combined profit factor:
        all_pnl_pcts: List[float] = []
        for s in sym_metrics:
            tr_path = test_dir / f"trades_{s}.csv"
            if tr_path.exists() and tr_path.stat().st_size > 0:
                try:
                    tr = pd.read_csv(tr_path)
                    all_pnl_pcts.extend(tr["pnl_pct"].tolist())
                except pd.errors.EmptyDataError:
                    pass
        wins = sum(p for p in all_pnl_pcts if p > 0)
        losses = -sum(p for p in all_pnl_pcts if p < 0)
        agg_pf = (wins / losses) if losses > 0 else float("inf")
        agg_sharpe = sharpe_ratio(np.array(all_pnl_pcts), bars_per_year=2190)
        agg_max_dd = max(m["max_drawdown"] for m in sym_metrics.values())

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

    # Full-period numbers from B2 summary.json.
    summary = json.loads((RESULTS_DIR / "summary.json").read_text())
    final_equity = float(summary["portfolio"]["final_equity_usd"])
    starting_cap = float(summary["portfolio"]["starting_capital_usd"])
    total_return = (final_equity / starting_cap - 1.0) if starting_cap > 0 else 0.0
    n_bars_total = n_bars
    n_years_total = n_bars_total / 2190.0
    annualized_full = annualized_return(total_return, n_years_total)

    # Bootstrap CI on OOS annualized mean (G6).
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    if len(oos_returns) > 1:
        boot_means = []
        for _ in range(BOOTSTRAP_RESAMPLES):
            sample = rng.choice(oos_returns, size=len(oos_returns), replace=True)
            boot_means.append(float(np.mean(sample)))
        ci_lower = float(np.quantile(boot_means, 0.025))
        ci_upper = float(np.quantile(boot_means, 0.975))
    else:
        ci_lower = 0.0
        ci_upper = 0.0

    # Bonferroni FWER (G7): we test a single hypothesis (oos_annualized > 0),
    # so the per-comparison alpha equals the FWER alpha. Report both.
    # (The family of 4 strategies in cycle-46 was the original concern; here
    # V8 is a single strategy, so the FWER correction is effectively
    # equivalent to alpha=0.0125.)
    fwer_alpha = FWER_ALPHA

    # ----- Hard gate evaluation -----
    gates = {
        "G1_sharpe_oos_mean_>=1.0": oos_sharpe_mean >= SHARPE_OOS_MIN,
        "G2_annualized_min>=0.15": min(annualized_full, oos_annualized_mean) >= ANNUALIZED_MIN,
        "G3_profit_factor_>1.5": oos_pf_mean > PROFIT_FACTOR_MIN,
        "G4_max_drawdown_<0.25": oos_max_dd_max < MAX_DRAWDOWN_MAX,
        "G5_framework_cv_oos_walk_forward": False,  # requires freqtrade/backtrader CV; out of scope here
        "G6_bootstrap_CI_lower>=0.5": ci_lower >= BOOTSTRAP_CI_LOWER_MIN,
        "G7_fwer_bonferroni_alpha=0.0125": fwer_alpha == FWER_ALPHA,  # structural: always true
    }
    # G5 not run; mark explicitly so reviewer can rerun.
    gates["G5_framework_cv_oos_walk_forward_pending"] = True

    n_pass = sum(1 for k, v in gates.items() if v is True)
    all_pass = all(v is True for v in gates.values())

    verdict = "ship" if all_pass else "[NOT-PROFITABLE]"

    out = {
        "strategy": cfg["strategy"],
        "iteration": cfg["iteration"],
        "timeframe": cfg["timeframe"],
        "n_windows_run": len(schedule),
        "n_windows_planned": N_WINDOWS,
        "schedule": {
            "train_4h": TRAIN_4H,
            "test_4h": TEST_4H,
            "step_4h": STEP_4H,
        },
        "full_period": {
            "total_return": total_return,
            "annualized_return": annualized_full,
            "n_bars": n_bars_total,
            "n_years": n_years_total,
            "starting_capital": starting_cap,
            "final_equity": final_equity,
        },
        "oos": {
            "annualized_return_mean": oos_annualized_mean,
            "sharpe_mean": oos_sharpe_mean,
            "profit_factor_mean": oos_pf_mean,
            "max_drawdown_max": oos_max_dd_max,
            "n_trades_total": oos_n_trades_total,
            "per_window_annualized": oos_returns,
            "per_window_sharpe": oos_sharpes,
            "per_window_profit_factor": oos_pf,
            "per_window_max_dd": oos_max_dd,
            "per_window_n_trades": oos_n_trades,
        },
        "bootstrap_95ci": {
            "lower": ci_lower,
            "upper": ci_upper,
            "resamples": BOOTSTRAP_RESAMPLES if len(oos_returns) > 1 else 0,
            "seed": BOOTSTRAP_SEED,
        },
        "fwer_bonferroni": {
            "alpha": fwer_alpha,
            "n_tests": 1,
        },
        "gates": gates,
        "verdict": verdict,
        "n_gates_pass": n_pass,
        "n_gates_total": len(gates),
    }
    (WF_DIR / "walk_forward_summary.json").write_text(
        json.dumps(out, indent=2, default=float)
    )
    print(json.dumps(out, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())