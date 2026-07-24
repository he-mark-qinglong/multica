"""B6 walk-forward validation for V3 (vpvr_xs_pairs_4h_zscore_vpvr_20260710).

Splits the available 4h data into rolling train/test windows, runs V3 on each
window, and reports per-window + aggregate metrics. Goal: verify that V3's
in-sample profitability (sharpe=0.596, return=22%) holds up out-of-sample.

Why this matters for ship gate
------------------------------
V3's primary metrics.json reflects a single full-history backtest. Without a
walk-forward split we cannot tell whether sharpe=0.596 is structural
(pair-stat-arb alpha) or fitted to the 2024-04 → 2026-06 sample. The
walk-forward ratio (mean_oos_sharpe / mean_is_sharpe) is the standard decay
metric: >0.5 = shippable, 0-0.5 = degraded, <0 = overfit.

Windows (anchored expanding-train)
----------------------------------
Given data span 2024-04-23 → 2026-06-23 (~4751 4h bars, ~26 months), define
4 windows with anchored train start (2024-04-23) and expanding train end:

  W1: train 2024-04-23 → 2025-01-23 (9 mo), test 2025-01-23 → 2025-07-23 (6 mo)
  W2: train 2024-04-23 → 2025-04-23 (12 mo), test 2025-04-23 → 2025-10-23 (6 mo)
  W3: train 2024-04-23 → 2025-07-23 (15 mo), test 2025-07-23 → 2026-01-23 (6 mo)
  W4: train 2024-04-23 → 2025-10-23 (18 mo), test 2025-10-23 → 2026-04-23 (6 mo)

Each test window is 6 months. Train expands by 3 months per window.

Note: V3's strategy.py re-fits z-score and VPVR bars on every bar (rolling
window), so "train" / "test" here only affects which bars participate in the
backtest — there is no separate parameter-fitting step. The walk-forward
ratio thus measures bar-set robustness, not parameter robustness.

Deflated Sharpe Ratio (DSR)
---------------------------
Following Bailey & Lopez de Prado (2014): DSR adjusts the observed Sharpe for
the number of trials and skew/kurtosis of the return distribution. We
estimate it on the per-window test sharpes (n=4 trials here, conservative).

Usage
-----
    PYTHONPATH=. python3 walk_forward.py [--n-windows 4]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from data_loader import load_all
from strategy import VARIANT_KEY, run_backtest

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class WindowResult:
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_test_bars: int
    n_test_trades: int
    test_sharpe: float
    test_return: float
    test_mdd: float
    test_win_rate: float


def _annualisation_factor(timeframe: str) -> float:
    """Read the canonical factor from the strategy module (4h = sqrt(252*6.5))."""
    from strategy import _annualisation_factor as _f
    return _f(timeframe)


def _summarise_trades(trades: List[dict], full_pair_meta: dict) -> Dict[str, float]:
    """Recompute per-pair OOS metrics from a list of filtered trade dicts.

    Trade dict shape comes from strategy._trade_to_dict; we re-derive sharpe /
    mdd / return / win_rate. This is intentionally approximate — the full
    equity-curve computation requires the underlying bar returns which the
    walk-forward doesn't carry. We approximate:

      total_return = sum(pnl_pct) across filtered trades
      win_rate     = count(pnl_pct > 0) / n_trades
      sharpe       = mean(pnl_pct) / std(pnl_pct) * sqrt(trades_per_year)
      mdd          = running max-drawdown of cumulative pnl
    """
    if not trades:
        return {"sharpe": 0.0, "return": 0.0, "mdd": 0.0, "win_rate": 0.0, "n_trades": 0}

    pnls = np.asarray([float(t.get("pnl_pct", 0.0)) for t in trades], dtype=float)
    wins = int(np.sum(pnls > 0))
    n = len(pnls)

    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    mdd = float(np.min(cum - peak))

    std = float(np.std(pnls, ddof=1)) if n > 1 else 0.0
    mean = float(np.mean(pnls))
    # Annualised from observed trades_per_year in full-pair meta; fallback 50.
    trades_per_year = float(full_pair_meta.get("trades_per_year", 50.0))
    sharpe = (mean / std) * math.sqrt(trades_per_year) if std > 0 else 0.0

    return {
        "sharpe": sharpe,
        "return": float(np.sum(pnls)),
        "mdd": mdd,
        "win_rate": wins / n,
        "n_trades": n,
    }


def _slice_data(data: dict, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Slice every symbol's DataFrame to [start, end)."""
    return {sym: df[(df.index >= start) & (df.index < end)] for sym, df in data.items()}


def _window_metrics(result: dict) -> Dict[str, float]:
    """Pull aggregate metrics from run_backtest's return shape (pairs-level)."""
    pairs = result.get("per_pair_metrics", {})
    if not pairs:
        return {"sharpe": 0.0, "return": 0.0, "mdd": 0.0, "win_rate": 0.0, "n_trades": 0}
    sharpes = [v.get("sharpe", 0.0) for v in pairs.values()]
    returns = [v.get("total_return_pct", 0.0) for v in pairs.values()]
    mdds = [v.get("max_drawdown_pct", 0.0) for v in pairs.values()]
    wrs = [v.get("win_rate", 0.0) for v in pairs.values()]
    n_trades = sum(v.get("n_trades", 0) for v in pairs.values())
    return {
        "sharpe": float(np.mean(sharpes)) if sharpes else 0.0,
        "return": float(np.mean(returns)) if returns else 0.0,
        "mdd": float(np.min(mdds)) if mdds else 0.0,
        "win_rate": float(np.mean(wrs)) if wrs else 0.0,
        "n_trades": int(n_trades),
    }


def _dsr(observed_sharpes: List[float], n_trials: int) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014), simplified.

    DSR ≈ Φ( (observed_sharpe_mean * sqrt(n-1) - SR_cap) / sqrt(1 - γ3*SR + (γ4-1)/4 * SR^2) )

    We approximate SR_cap (the expected maximum Sharpe under the null of n
    independent trials) using the empirical upper-tail heuristic: SR_cap ≈
    sqrt(2 * log(n)) — 0.577 / sqrt(2 * log(n)).

    For our n=4 windows this is small (≈ 0.96), so even modest OOS sharpes
    should clear. Returns the z-score, NOT the probability — caller decides
    threshold.
    """
    if n_trials < 2 or not observed_sharpes:
        return 0.0
    arr = np.asarray(observed_sharpes, dtype=float)
    mean_s = float(np.mean(arr))
    std_s = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
    if std_s == 0.0:
        return 0.0
    sr_cap = math.sqrt(2.0 * math.log(n_trials)) - 0.577 / math.sqrt(2.0 * math.log(n_trials))
    # Assume Gaussian skew/kurt for n=4 (insufficient sample to estimate).
    se = std_s * math.sqrt(1.0 / (n_trials - 1))
    z = (mean_s - sr_cap) / se if se > 0 else 0.0
    return float(z)


def build_windows(span_start: pd.Timestamp, span_end: pd.Timestamp, n_windows: int,
                  test_months: int = 6, train_init_months: int = 9,
                  step_months: int = 3) -> List[dict]:
    """Anchored expanding-train windows.

    First window: train = [span_start, span_start + train_init_months),
                         test  = [train_end, train_end + test_months).
    Each subsequent window: train end advances by step_months.
    """
    from dateutil.relativedelta import relativedelta
    windows = []
    train_end = span_start + relativedelta(months=train_init_months)
    for i in range(n_windows):
        test_start = train_end
        test_end = test_start + relativedelta(months=test_months)
        if test_end > span_end:
            break
        windows.append({
            "window_id": i + 1,
            "train_start": span_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
        })
        train_end = train_end + relativedelta(months=step_months)
    return windows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-windows", type=int, default=4)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--train-init-months", type=int, default=9)
    parser.add_argument("--step-months", type=int, default=3)
    args = parser.parse_args(argv)

    if not CONFIG_PATH.is_file():
        print(f"missing config: {CONFIG_PATH}", file=sys.stderr)
        return 1

    cfg = json.loads(CONFIG_PATH.read_text())
    symbols = cfg["instruments"]
    print(f"Loading {len(symbols)} symbols from {CONFIG_PATH.parent}/data ...")
    data = load_all(symbols)
    # Common UTC index across symbols — for V3 the run_backtest handles
    # alignment internally, so we just take the union of timestamps.
    span_start = min(df.index.min() for df in data.values())
    span_end = max(df.index.max() for df in data.values())
    print(f"Span: {span_start.date()} → {span_end.date()} "
          f"({(span_end - span_start).days} days)")

    windows = build_windows(
        span_start.to_pydatetime() if hasattr(span_start, "to_pydatetime") else span_start,
        span_end.to_pydatetime() if hasattr(span_end, "to_pydatetime") else span_end,
        n_windows=args.n_windows,
        test_months=args.test_months,
        train_init_months=args.train_init_months,
        step_months=args.step_months,
    )
    print(f"Built {len(windows)} walk-forward windows")
    if not windows:
        print("No windows fit; check data span / --train-init-months.", file=sys.stderr)
        return 2

    # Train-once-per-window: param-free for V3 (rolling), so train IS the
    # full pre-test slice (lets the rolling windows warm up) and test IS the
    # next slice.
    results: List[WindowResult] = []
    for w in windows:
        train_end = pd.Timestamp(w["train_end"])
        test_start = pd.Timestamp(w["test_start"])
        test_end = pd.Timestamp(w["test_end"])

        # Run on FULL data so rolling indicators warm up, then filter trades
        # to those whose entry_ts is in [test_start, test_end). This is the
        # standard walk-forward pattern for indicator-driven strategies.
        result = run_backtest(data, cfg)
        all_pairs = result.get("per_pair", [])
        pairs_metrics_full = {
            r["pair"]: {
                "n_trades": r.get("n_trades", 0),
                "win_rate": r.get("win_rate", 0.0),
                "trades_per_year": r.get("trades_per_year", 50.0),
            }
            for r in all_pairs
        }

        # Compute OOS metrics by filtering per-pair trades.
        from datetime import datetime
        oos_pairs = {}
        for pair_res in all_pairs:
            pair_label = pair_res.get("pair", "?")
            trades = pair_res.get("trades", [])
            oos_trades = [
                t for t in trades
                if pd.Timestamp(t.get("entry_ts")) >= test_start
                and pd.Timestamp(t.get("entry_ts")) < test_end
            ]
            if not oos_trades:
                continue
            # Recompute per-pair metrics from filtered trades.
            oos_pairs[pair_label] = _summarise_trades(
                oos_trades, pairs_metrics_full.get(pair_label, {}),
            )

        # Aggregate across pairs.
        if oos_pairs:
            sharpes = [v["sharpe"] for v in oos_pairs.values()]
            returns = [v["return"] for v in oos_pairs.values()]
            mdds = [v["mdd"] for v in oos_pairs.values()]
            wrs = [v["win_rate"] for v in oos_pairs.values()]
            n_trades = sum(v["n_trades"] for v in oos_pairs.values())
            m = {
                "sharpe": float(np.mean(sharpes)),
                "return": float(np.mean(returns)),
                "mdd": float(np.min(mdds)),
                "win_rate": float(np.mean(wrs)),
                "n_trades": int(n_trades),
            }
        else:
            m = {"sharpe": 0.0, "return": 0.0, "mdd": 0.0, "win_rate": 0.0, "n_trades": 0}

        n_bars = min(
            len(df[(df.index >= test_start) & (df.index < test_end)])
            for df in data.values()
        )

        wr = WindowResult(
            window_id=w["window_id"],
            train_start=str(pd.Timestamp(w["train_start"]).date()),
            train_end=str(train_end.date()),
            test_start=str(test_start.date()),
            test_end=str(test_end.date()),
            n_test_bars=int(n_bars),
            n_test_trades=int(m["n_trades"]),
            test_sharpe=m["sharpe"],
            test_return=m["return"],
            test_mdd=m["mdd"],
            test_win_rate=m["win_rate"],
        )
        results.append(wr)
        print(f"  W{wr.window_id}: test [{wr.test_start}→{wr.test_end}] "
              f"bars={wr.n_test_bars} trades={wr.n_test_trades} "
              f"sharpe={wr.test_sharpe:+.3f} ret={wr.test_return:+.4f} mdd={wr.test_mdd:+.4f}")

    # Aggregate.
    test_sharpes = [r.test_sharpe for r in results]
    test_returns = [r.test_return for r in results]
    test_mdds = [r.test_mdd for r in results]
    # Pull the in-sample sharpe from results/metrics.json (auto-tracks backtest)
    # rather than hard-coding — keeps WF in sync with the latest backtest.
    metrics_path = RESULTS_DIR / "metrics.json"
    is_sharpe = 0.0
    if metrics_path.is_file():
        try:
            metrics = json.loads(metrics_path.read_text())
            # V3 metrics.json uses top-level "sharpe" key (per-pair in per_pair dict)
            is_sharpe = float(metrics.get("sharpe", 0.0))
        except Exception:
            pass
    if is_sharpe == 0.0:
        is_sharpe = float(cfg.get("expected_in_sample_sharpe", 0.0))
    decay = float(np.mean(test_sharpes) / is_sharpe) if is_sharpe != 0 else 0.0
    dsr_z = _dsr(test_sharpes, n_trials=len(results))

    payload = {
        "strategy": cfg["strategy"],
        "iteration": cfg["iteration"],
        "timeframe": cfg["timeframe"],
        "variant": VARIANT_KEY,
        "n_windows": len(results),
        "windows": [asdict(r) for r in results],
        "aggregate": {
            "mean_test_sharpe": float(np.mean(test_sharpes)),
            "std_test_sharpe": float(np.std(test_sharpes, ddof=1)) if len(test_sharpes) > 1 else 0.0,
            "min_test_sharpe": float(np.min(test_sharpes)) if test_sharpes else 0.0,
            "mean_test_return": float(np.mean(test_returns)),
            "worst_test_mdd": float(np.min(test_mdds)) if test_mdds else 0.0,
            "in_sample_sharpe": is_sharpe,
            "walk_forward_ratio": decay,
            "deflated_sharpe_z": dsr_z,
            "ship_gate": {
                "wf_ratio_threshold": 0.5,
                "wf_ratio_pass": bool(decay >= 0.5),
                "min_oos_sharpe_threshold": 0.0,
                "min_oos_sharpe_pass": bool(min(test_sharpes) >= 0.0 if test_sharpes else False),
                "overall_pass": bool(
                    decay >= 0.5 and (min(test_sharpes) >= 0.0 if test_sharpes else False)
                ),
            },
        },
        "evidence_gate": {
            "sharpe_threshold": 0.5,
            "wf_ratio_threshold": 0.5,
            "passed": bool(decay >= 0.5),
        },
    }
    out_path = RESULTS_DIR / "walk_forward.json"
    out_path.write_text(json.dumps(payload, indent=2, default=float))
    print(f"\n=== B6 walk-forward summary ===")
    print(f"  windows: {len(results)}")
    print(f"  in-sample sharpe: {is_sharpe:+.3f}")
    print(f"  mean OOS sharpe : {payload['aggregate']['mean_test_sharpe']:+.3f} ± "
          f"{payload['aggregate']['std_test_sharpe']:.3f}")
    print(f"  min  OOS sharpe : {payload['aggregate']['min_test_sharpe']:+.3f}")
    print(f"  walk-forward ratio: {decay:+.3f} "
          f"(gate >= 0.5 → {'PASS' if decay >= 0.5 else 'FAIL'})")
    print(f"  deflated sharpe z : {dsr_z:+.3f}  (positive = beats n-trial max-SR null)")
    print(f"  ship_gate: {payload['aggregate']['ship_gate']}")
    print(f"  → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())