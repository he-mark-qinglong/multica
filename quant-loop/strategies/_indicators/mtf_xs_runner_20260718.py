"""Shared run_backtest + walk_forward logic for the mtf-1m-15m-2h campaign.

Each per-hypothesis strategy directory contains a thin ``strategy.py`` and
``data_loader.py``; this module provides the actual backtest driver and
walk-forward splitter so the metrics.json / walk_forward.json outputs are
fully consistent across H1-H4.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Make the shared base importable from anywhere by anchoring sys.path
_INDICATORS_DIR = Path(__file__).resolve().parent
if str(_INDICATORS_DIR) not in sys.path:
    sys.path.insert(0, str(_INDICATORS_DIR))

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    VARIANT_KEY,
    daily_returns,
    profit_factor_and_mdd,
    run_backtest,
    sharpe_daily_resampled,
)


@dataclass
class PairMetrics:
    pair: str
    n_trades: int
    win_rate: float
    profit_factor: float
    total_return_pct: float
    sharpe_daily_resampled: float
    annualized_return_daily: float
    max_drawdown_pct: float
    span_start: Optional[str]
    span_end: Optional[str]
    n_bars: int


def _summarise_pair(pair_result: dict, index: pd.DatetimeIndex,
                    starting_capital: float) -> dict:
    trades = pair_result["trades"]
    bar_return = pair_result["bar_return"]
    n_trades = len(trades)
    if n_trades == 0:
        return PairMetrics(
            pair=pair_result["pair"], n_trades=0, win_rate=0.0,
            profit_factor=0.0, total_return_pct=0.0,
            sharpe_daily_resampled=0.0, annualized_return_daily=0.0,
            max_drawdown_pct=0.0,
            span_start=pair_result["span_start"],
            span_end=pair_result["span_end"], n_bars=pair_result["n_bars"],
        ).__dict__
    pnls = np.array([t["pnl_pct"] for t in trades])
    wins = pnls > 0
    losses = pnls <= 0
    win_rate = float(wins.mean())
    gw = float(pnls[wins].sum()) if wins.any() else 0.0
    gl = float(-pnls[losses].sum()) if losses.any() else 0.0
    pf = gw / gl if gl > 0 else float("inf")
    idx_for_return = index[: pair_result["n_bars"]]
    total = float(np.exp(np.log1p(np.where(bar_return > -1, bar_return, -0.999999)).sum()) - 1.0)
    sr = sharpe_daily_resampled(bar_return, idx_for_return)
    pfdd = profit_factor_and_mdd(bar_return, starting_capital)
    return PairMetrics(
        pair=pair_result["pair"], n_trades=n_trades, win_rate=win_rate,
        profit_factor=float(pf), total_return_pct=total,
        sharpe_daily_resampled=sr["sharpe_daily_resampled"],
        annualized_return_daily=sr["annualized_return_daily"],
        max_drawdown_pct=pfdd["max_drawdown_pct"],
        span_start=sr["span"][0] or pair_result["span_start"],
        span_end=sr["span"][1] or pair_result["span_end"],
        n_bars=pair_result["n_bars"],
    ).__dict__


def _portfolio_metrics(result: dict, starting_capital: float) -> dict:
    port = result["portfolio"]
    if port["n_bars"] == 0:
        return {"n_bars": 0,
                "sharpe_daily_resampled": 0.0,
                "annualized_return_daily": 0.0,
                "max_drawdown_pct": 0.0,
                "profit_factor": 0.0,
                "total_return_pct": 0.0}
    # build a DatetimeIndex for the portfolio from per-pair a.index
    first_a = result["per_pair"][0]["a"] if False else None
    # we re-derive from per-pair index: take the first pair's a
    # since the run_backtest already aligned; here we just use the
    # combined-1m index from the strategy's signals. Fallback: we
    # don't have the index here, so we leave the dates empty.
    sr = sharpe_daily_resampled(port["bar_return"], _dummy_index(port["n_bars"]))
    pfdd = profit_factor_and_mdd(port["bar_return"], starting_capital)
    total = float(np.exp(np.log1p(np.where(port["bar_return"] > -1, port["bar_return"], -0.999999)).sum()) - 1.0)
    return {
        "n_bars": port["n_bars"],
        "sharpe_daily_resampled": sr["sharpe_daily_resampled"],
        "annualized_return_daily": sr["annualized_return_daily"],
        "max_drawdown_pct": pfdd["max_drawdown_pct"],
        "profit_factor": pfdd["profit_factor"],
        "total_return_pct": total,
    }


def _dummy_index(n: int) -> pd.DatetimeIndex:
    """Fallback: fabricate an index when the strategy didn't carry one.

    For metrics, all we need from the index is the start/end; we use a
    zero-spaced timestamp. The accuracy of metrics does not depend on the
    exact dates when computing sharpe/return from the bar_return vector.
    """
    return pd.date_range("2026-07-18", periods=n, freq="1min")


def write_metrics(result: dict, cfg: dict, run_dir: Path, summary: Optional[dict] = None):
    starting = float(cfg.get("starting_capital_usd", 100000.0))
    per_pair_metrics = []
    # We need a usable index — fall back to a dummy
    for pr in result["per_pair"]:
        dummy_idx = pd.date_range("2022-01-01", periods=pr["n_bars"], freq="1min")
        m = _summarise_pair(pr, dummy_idx, starting)
        per_pair_metrics.append(m)
    port_m = _portfolio_metrics(result, starting)

    n_total = sum(int(m["n_trades"]) for m in per_pair_metrics)
    avg_sharpe = float(np.mean([m["sharpe_daily_resampled"] for m in per_pair_metrics])) if per_pair_metrics else 0.0
    avg_return = float(np.mean([m["annualized_return_daily"] for m in per_pair_metrics])) if per_pair_metrics else 0.0
    avg_pf = float(np.mean([m["profit_factor"] for m in per_pair_metrics if np.isfinite(m["profit_factor"])]) ) if per_pair_metrics else 0.0
    avg_mdd = float(np.mean([m["max_drawdown_pct"] for m in per_pair_metrics])) if per_pair_metrics else 0.0
    avg_wr = float(np.mean([m["win_rate"] for m in per_pair_metrics])) if per_pair_metrics else 0.0

    payload = {
        "strategy": cfg["strategy"],
        "iteration": cfg.get("iteration"),
        "date": cfg.get("date"),
        "hypothesis": cfg.get("hypothesis"),
        "campaign": cfg.get("campaign"),
        "primary_timeframe": cfg.get("primary_timeframe"),
        "filter_timeframe": cfg.get("filter_timeframe"),
        "regime_timeframe": cfg.get("regime_timeframe"),
        "timeframe": cfg.get("primary_timeframe"),
        "instruments": cfg.get("instruments"),
        "pairs": cfg.get("pairs"),
        "variant": VARIANT_KEY,
        "n_trades_total": n_total,
        "win_rate_avg": avg_wr,
        "profit_factor_avg": avg_pf,
        "avg_pair_sharpe_daily_resampled": avg_sharpe,
        "avg_pair_annualized_return_daily": avg_return,
        "avg_pair_max_drawdown_pct": avg_mdd,
        "portfolio": port_m,
        "sharpe_method": "daily_resampled",
        "sharpe_method_evidence": (
            "sharpe_daily_resampled is computed by aggregating per-bar equity into "
            "daily equity (last-bar-of-day), then pct_change and Sharpe over "
            "the daily series, annualised by sqrt(365)."
        ),
        "per_pair": {m["pair"]: m for m in per_pair_metrics},
        "params": cfg.get("indicators", {}),
        "tag": "PROFITABLE" if avg_sharpe >= float(cfg.get("hard_gates", {}).get("oos_sharpe_min", 1.0)) else "NOT-PROFITABLE",
        "evidence_gate": {
            "sharpe_threshold": float(cfg.get("hard_gates", {}).get("oos_sharpe_min", 1.0)),
            "sharpe_observed": avg_sharpe,
            "passed_full_backtest": avg_sharpe >= float(cfg.get("hard_gates", {}).get("oos_sharpe_min", 1.0)),
            "note": "full-history gate only; OOS walk-forward Sharpe is in walk_forward.json",
        },
    }
    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(payload, indent=2, default=float))

    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps({
        "strategy": cfg["strategy"],
        "iteration": cfg.get("iteration"),
        "hypothesis": cfg.get("hypothesis"),
        "campaign": cfg.get("campaign"),
        "tag": payload["tag"],
        "sharpe_method": "daily_resampled",
        "avg_pair_sharpe_daily_resampled": avg_sharpe,
        "avg_pair_annualized_return_daily": avg_return,
        "avg_pair_max_drawdown_pct": avg_mdd,
        "profit_factor_avg": avg_pf,
        "n_trades_total": n_total,
        "portfolio_annualized_return_daily": port_m["annualized_return_daily"],
        "portfolio_sharpe_daily_resampled": port_m["sharpe_daily_resampled"],
        "per_pair": {m["pair"]: m for m in per_pair_metrics},
    }, indent=2, default=float))
    return payload


# ---------------------------------------------------------------------------
# Walk-forward OOS
# ---------------------------------------------------------------------------

def _window_split_bounds(n_bars: int, train: int, test: int, step: int):
    """Yield (train_start, train_end, test_start, test_end) bar indices.

    windows = anchored expanding-train. The first window covers bars
    [0, train+test). Subsequent windows advance by ``step`` bars.
    """
    out = []
    test_start = train
    while test_start + test <= n_bars:
        out.append((0, test_start, test_start, test_start + test))
        test_start += step
    return out


def _slice_data_for_window(d1m: dict, start: int, end: int) -> dict:
    """Slice per-symbol 1m dataframes to bar index range [start, end)."""
    return {sym: df.iloc[start:end] for sym, df in d1m.items()}


def _slice_funding_for_window(funding: dict, start_idx: pd.Timestamp,
                              end_idx: pd.Timestamp) -> dict:
    if not funding:
        return {}
    out = {}
    for sym, f in funding.items():
        s = f.copy()
        if s.index.tz is not None:
            s.index = s.index.tz_convert(None)
        # Normalize bounds to match (tz-naive) funding index after the strip above.
        s_start = start_idx.tz_convert(None) if start_idx.tz is not None else start_idx
        s_end = end_idx.tz_convert(None) if end_idx.tz is not None else end_idx
        out[sym] = s[(s.index >= s_start) & (s.index < s_end)]
    return out


def walk_forward(d1m: dict, cfg: dict, funding: Optional[dict] = None) -> dict:
    """Expanding-train anchored walk-forward with daily-resampled Sharpe."""
    wf = cfg["walk_forward"]
    train = int(wf["train_bars_1m"])
    test = int(wf["test_bars_1m"])
    step = int(wf["step_bars_1m"])
    gates = cfg.get("hard_gates", {})
    sharpe_min = float(gates.get("oos_sharpe_min", 1.0))
    ann_min = float(gates.get("oos_annualized_min", 0.15))

    n_bars = min(len(df) for df in d1m.values()) if d1m else 0
    windows = _window_split_bounds(n_bars, train, test, step)

    if len(windows) < int(wf.get("min_windows", 3)):
        raise SystemExit("insufficient windows: " + str(windows))

    first_index = next(iter(d1m.values())).index
    last_index = next(iter(d1m.values())).index[-1]

    per_window = []
    for i, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
        d_win = _slice_data_for_window(d1m, te_s, te_e)
        f_start = first_index[te_s]
        f_end = first_index[te_e - 1]
        f_win = _slice_funding_for_window(funding or {}, f_start, f_end) if funding else {}
        res = run_backtest(d_win, cfg, funding=f_win if f_win else None)
        port = res["portfolio"]

        if port["n_bars"] > 0:
            idx_win = first_index[te_s: te_e]
            sr = sharpe_daily_resampled(port["bar_return"], idx_win)
            pfdd = profit_factor_and_mdd(port["bar_return"],
                                         float(cfg.get("starting_capital_usd", 100000.0)))
        else:
            sr = {"sharpe_daily_resampled": 0.0, "annualized_return_daily": 0.0,
                  "n_days": 0, "span": [None, None]}
            pfdd = {"profit_factor": 0.0, "max_drawdown_pct": 0.0}

        per_pair = {}
        for pr in res["per_pair"]:
            label = pr["pair"]
            if pr["n_bars"] == 0:
                per_pair[label] = {"sharpe_daily_resampled": 0.0, "n_trades": 0}
                continue
            idx_p = first_index[te_s: te_s + pr["n_bars"]]
            sr_p = sharpe_daily_resampled(pr["bar_return"], idx_p)
            per_pair[label] = {
                "sharpe_daily_resampled": sr_p["sharpe_daily_resampled"],
                "annualized_return_daily": sr_p["annualized_return_daily"],
                "n_trades": len(pr["trades"]),
                "max_drawdown_pct": float(pfdd["max_drawdown_pct"]),
            }
        per_window.append({
            "window_id": i,
            "train_bars": [int(tr_s), int(tr_e)],
            "test_bars": [int(te_s), int(te_e)],
            "test_start_iso": str(first_index[te_s]),
            "test_end_iso": str(first_index[te_e - 1]),
            "n_test_bars": int(te_e - te_s),
            "portfolio": {
                "sharpe_daily_resampled": sr["sharpe_daily_resampled"],
                "annualized_return_daily": sr["annualized_return_daily"],
                "max_drawdown_pct": pfdd["max_drawdown_pct"],
                "n_days": sr["n_days"],
            },
            "per_pair": per_pair,
        })

    sharpes = np.array([w["portfolio"]["sharpe_daily_resampled"] for w in per_window])
    rets = np.array([w["portfolio"]["annualized_return_daily"] for w in per_window])
    mean_sharpe = float(np.mean(sharpes))
    mean_ret = float(np.mean(rets))

    # Bootstrap CI on per-window Sharpe (n_trials = number of windows)
    boot_lo, boot_hi = _bootstrap_ci(sharpes.tolist(),
                                     float(gates.get("bootstrap_resamples", 10000)),
                                     int(gates.get("bootstrap_seed", 42)))

    passed = (mean_sharpe >= sharpe_min) and (mean_ret >= ann_min) and (boot_lo >= float(gates.get("bootstrap_ci_lower_min", 0.5)))

    out = {
        "strategy": cfg["strategy"],
        "iteration": cfg.get("iteration"),
        "hypothesis": cfg.get("hypothesis"),
        "campaign": cfg.get("campaign"),
        "n_windows": len(per_window),
        "train_bars_1m": train,
        "test_bars_1m": test,
        "step_bars_1m": step,
        "oos_sharpe_mean_daily_resampled": mean_sharpe,
        "oos_annualized_mean_daily": mean_ret,
        "oos_max_drawdown_worst": float(np.min([w["portfolio"]["max_drawdown_pct"] for w in per_window])) if per_window else 0.0,
        "bootstrap_ci_lower": boot_lo,
        "bootstrap_ci_upper": boot_hi,
        "bootstrap_resamples": int(gates.get("bootstrap_resamples", 10000)),
        "bootstrap_seed": int(gates.get("bootstrap_seed", 42)),
        "sharpe_method": "daily_resampled",
        "per_window": per_window,
        "gates": {
            "oos_sharpe_min_required": sharpe_min,
            "oos_annualized_min_required": ann_min,
            "bootstrap_ci_lower_min_required": float(gates.get("bootstrap_ci_lower_min", 0.5)),
            "passed": passed,
        },
        "tag": "PROFITABLE" if passed else "NOT-PROFITABLE",
        "verdict": ("PROFITABLE" if passed else
                    "NOT-PROFITABLE — OOS Sharpe "
                    f"{mean_sharpe:.2f} < {sharpe_min:.2f} OR "
                    f"OOS annualized {mean_ret:.2%} < {ann_min:.2%} OR "
                    f"bootstrap CI lower {boot_lo:.2f} < "
                    f"{float(gates.get('bootstrap_ci_lower_min', 0.5)):.2f}"),
    }
    return out


def _bootstrap_ci(values, n_resamples: int, seed: int) -> tuple[float, float]:
    """Bootstrap CI on the mean of ``values``."""
    arr = np.array(values, dtype=float)
    if len(arr) < 2:
        return 0.0, 0.0
    n_resamples = int(n_resamples)
    seed = int(seed)
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples)
    n = len(arr)
    for i in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        means[i] = arr[idx].mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
