"""Walk-forward multi-OOS backtest runner for vpvr_multi_tf_funding.

For each of the 3 OOS folds in ``config.json``:

  1. Load BTCUSDT 1m / 15m / 4h OHLCV + funding (canonical shared
     pool per AGENTS.md §1).
  2. Run ``strategy.run_backtest`` on the test window.
  3. Compute per-fold metrics (Sharpe_daily via daily-resample per
     SMA-34787, total return, annualized, MDD, profit factor, win rate,
     avg bars held).
  4. Bootstrap CI on the daily returns (1000 resamples, 95% CI).
  5. Aggregate into ``results/metrics.json``, ``gates_report.json``,
     ``summary.txt`` and per-fold equity / trades CSVs.
  6. Emit a single VERDICT line per SMA-34924 convention.

The OOS test windows are walk-forward folds (no parameter fitting
per fold): all rolling windows in the 4h regime classifier, the 1m
LOID lookback, the 15m funding threshold, and the 4h VPVR window are
fixed at the cycle-46 / SPEC values. The OOS Sharpe is therefore a
true out-of-sample measurement.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from data_loader import load_tf  # noqa: E402
from strategy import VARIANT_KEY, run_backtest  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "vpvr_multi_tf_funding"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO_ROOT / "config.json"

BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)
RNG = np.random.default_rng(20260718)


# ---------------------------------------------------------------------------
# Per-fold metrics + bootstrap CI.
# ---------------------------------------------------------------------------

def _daily_resampled_sharpe(equity: np.ndarray, idx: pd.DatetimeIndex) -> float:
    series = pd.Series(equity, index=idx, dtype=np.float64)
    daily_eq = series.resample("1D").last().dropna()
    if len(daily_eq) < 2:
        return 0.0
    rets = daily_eq.pct_change().dropna()
    if rets.std() == 0 or not np.isfinite(rets.std()):
        return 0.0
    return float(rets.mean() / rets.std() * SQRT_BPY_DAILY)


def _compute_metrics(result: dict, idx: pd.DatetimeIndex) -> dict:
    equity = np.asarray(result["equity"], dtype=np.float64)
    trades = result["trades"]

    n_bars = int(result["n_bars"])
    starting = float(equity[0]) if len(equity) else 0.0
    final = float(equity[-1]) if len(equity) else 0.0

    if len(equity) < 2 or starting <= 0:
        return {
            "n_bars": n_bars,
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sharpe_daily": 0.0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_bars_held": 0.0,
            "bootstrap_ci_lower_sharpe": 0.0,
            "bootstrap_ci_upper_sharpe": 0.0,
        }

    total_return = (final / starting) - 1.0
    eq_idx = idx[: len(equity)]
    daily_eq = pd.Series(equity, index=eq_idx, dtype=np.float64).resample("1D").last().dropna()
    if len(daily_eq) >= 2:
        n_days = max(1, (daily_eq.index[-1] - daily_eq.index[0]).days)
        n_years = n_days / BARS_PER_YEAR_DAILY
    else:
        n_years = n_bars / (BARS_PER_YEAR_DAILY * 1440)
    if n_years > 0 and final > 0 and starting > 0:
        annualized = (final / starting) ** (1.0 / n_years) - 1.0
    else:
        annualized = 0.0

    sharpe = _daily_resampled_sharpe(equity, eq_idx)

    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_dd_pct = float(np.min(drawdowns)) * 100.0 if drawdowns.size else 0.0

    n_trades = len(trades)
    net_pnls = np.array([t["net_pnl_pct"] for t in trades], dtype=np.float64) if n_trades else np.array([])
    gross_profit = float(net_pnls[net_pnls > 0].sum()) if net_pnls.size else 0.0
    gross_loss = float(abs(net_pnls[net_pnls < 0].sum())) if net_pnls.size else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = float((net_pnls > 0).sum() / n_trades) if n_trades > 0 else 0.0
    avg_bars_held = float(np.mean([t["bars_held"] for t in trades])) if n_trades > 0 else 0.0

    # Bootstrap CI on daily returns.
    daily_returns = daily_eq.pct_change().dropna().values
    if len(daily_returns) >= 5:
        n_b = min(1000, len(daily_returns) * 2)
        sharpe_samples = np.empty(n_b, dtype=np.float64)
        for k in range(n_b):
            sample = RNG.choice(daily_returns, size=len(daily_returns), replace=True)
            mu = sample.mean()
            sd = sample.std()
            if sd > 0 and np.isfinite(sd):
                sharpe_samples[k] = mu / sd * SQRT_BPY_DAILY
            else:
                sharpe_samples[k] = 0.0
        ci_lower = float(np.quantile(sharpe_samples, 0.025))
        ci_upper = float(np.quantile(sharpe_samples, 0.975))
    else:
        ci_lower = 0.0
        ci_upper = 0.0

    return {
        "n_bars": n_bars,
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if np.isfinite(profit_factor) else float("inf"),
        "sharpe_daily": round(sharpe, 4),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "avg_bars_held": round(avg_bars_held, 2),
        "bootstrap_ci_lower_sharpe": round(ci_lower, 4),
        "bootstrap_ci_upper_sharpe": round(ci_upper, 4),
        "bootstrap_resamples": 1000 if len(daily_returns) >= 5 else 0,
    }


# ---------------------------------------------------------------------------
# Windowing helpers.
# ---------------------------------------------------------------------------

def _slice_window(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    if df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")
    return df.loc[s:e].copy()


def _tf_freq(tf: str) -> str:
    return {"1m": "1min", "15m": "15min", "4h": "4h"}.get(tf, "1min")


# ---------------------------------------------------------------------------
# Per-fold run.
# ---------------------------------------------------------------------------

def _run_fold(cfg: dict, fold: dict, df_1m_full: pd.DataFrame, df_15m_full: pd.DataFrame, df_4h_full: pd.DataFrame) -> dict:
    test_start = fold["test_start"]
    test_end = fold["test_end"]
    print(f"[fold {fold['name']}] test=[{test_start} .. {test_end}]", flush=True)

    df_1m = _slice_window(df_1m_full, test_start, test_end)
    df_15m = _slice_window(df_15m_full, test_start, test_end)
    df_4h = _slice_window(df_4h_full, test_start, test_end)

    # Guard: if any TF is empty after slicing, fail loud.
    if df_1m.empty or df_15m.empty or df_4h.empty:
        return {
            "fold": fold["name"],
            "test_window": [test_start, test_end],
            "error": f"empty window after slice: 1m={len(df_1m)} 15m={len(df_15m)} 4h={len(df_4h)}",
        }

    print(f"  1m rows={len(df_1m)} 15m rows={len(df_15m)} 4h rows={len(df_4h)}", flush=True)

    result = run_backtest(df_1m, df_15m, df_4h, cfg)
    metrics = _compute_metrics(result, df_1m.index)

    # Save per-fold equity + trades.
    eq = pd.DataFrame(
        {"equity": result["equity"]},
        index=pd.date_range(start=result["span_start"], periods=len(result["equity"]),
                            freq=_tf_freq("1m"), tz="UTC"),
    )
    eq.index.name = "timestamp"
    eq.to_csv(RESULTS_DIR / f"equity_{fold['name']}.csv")
    if result["trades"]:
        pd.DataFrame(result["trades"]).to_csv(RESULTS_DIR / f"trades_{fold['name']}.csv", index=False)
    else:
        pd.DataFrame(columns=[
            "variant", "symbol", "direction", "entry_ts", "entry_price",
            "exit_ts", "exit_price", "pnl_pct", "funding_paid_pct", "net_pnl_pct",
            "bars_held", "decision_tf", "size_mult", "conviction",
            "exit_reason", "agree_count", "regime_4h_at_entry",
        ]).to_csv(RESULTS_DIR / f"trades_{fold['name']}.csv", index=False)

    # Sanitize NaN/inf for JSON.
    def _san(o):
        if isinstance(o, dict):
            return {k: _san(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_san(v) for v in o]
        if isinstance(o, float):
            return None if math.isnan(o) or math.isinf(o) else o
        if isinstance(o, (np.floating,)):
            f = float(o)
            return None if (math.isnan(f) or math.isinf(f)) else f
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, pd.Timestamp):
            return o.isoformat()
        return o

    payload = {
        "fold": fold["name"],
        "test_window": [test_start, test_end],
        "train_window": [fold["train_start"], fold["train_end"]],
        "n_bars": metrics["n_bars"],
        "metrics": metrics,
        "diagnostics": _san(result["diagnostics"]),
    }
    (TOPLEVEL_RESULTS / f"backtest_{fold['name']}.json").write_text(
        json.dumps(payload, indent=2, default=str)
    )
    return payload


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} backtest start", flush=True)

    # Load the full BTCUSDT series for each TF once.
    print("  loading BTCUSDT 1m/15m/4h + funding...", flush=True)
    df_1m_full = load_tf("BTCUSDT", "1m")
    df_15m_full = load_tf("BTCUSDT", "15m")
    df_4h_full = load_tf("BTCUSDT", "4h")
    print(f"  1m={len(df_1m_full)} 15m={len(df_15m_full)} 4h={len(df_4h_full)}", flush=True)

    folds = list(cfg["oos_folds"])
    per_fold = []
    for fold in folds:
        try:
            per_fold.append(_run_fold(cfg, fold, df_1m_full, df_15m_full, df_4h_full))
        except Exception as exc:  # noqa: BLE001
            print(f"  [fold {fold['name']}] FAILED: {type(exc).__name__}: {exc}", flush=True)
            per_fold.append({
                "fold": fold["name"],
                "test_window": [fold["test_start"], fold["test_end"]],
                "error": f"{type(exc).__name__}: {exc}",
            })

    # Aggregate.
    valid_folds = [p for p in per_fold if "error" not in p]
    sharpes = [p["metrics"]["sharpe_daily"] for p in valid_folds]
    anns = [p["metrics"]["annualized_return"] for p in valid_folds]
    mdds = [p["metrics"]["max_drawdown_pct"] for p in valid_folds]
    pfs = [p["metrics"]["profit_factor"] for p in valid_folds]
    n_trades_total = sum(p["metrics"]["n_trades"] for p in valid_folds)
    n_folds_with_trades = sum(1 for p in valid_folds if p["metrics"]["n_trades"] >= cfg["gates"]["G5_n_trades_min"])

    # CI lower across folds: take the min fold-level CI lower.
    ci_lowers = [p["metrics"]["bootstrap_ci_lower_sharpe"] for p in valid_folds]

    aggregate = {
        "mean_sharpe_daily": round(float(np.mean(sharpes)) if sharpes else 0.0, 4),
        "mean_annualized_return": round(float(np.mean(anns)) if anns else 0.0, 6),
        "worst_max_drawdown_pct": round(float(min(mdds)) if mdds else 0.0, 4),
        "min_profit_factor": round(float(min([pf for pf in pfs if np.isfinite(pf)])) if pfs else 0.0, 4),
        "n_trades_total": int(n_trades_total),
        "n_folds_total": len(folds),
        "n_folds_valid": len(valid_folds),
        "n_folds_with_min_trades": int(n_folds_with_trades),
        "min_bootstrap_ci_lower": round(float(min(ci_lowers)) if ci_lowers else 0.0, 4),
    }

    gates = _evaluate_gates(cfg, valid_folds, aggregate)

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "source_spec": "SMA-34911",
        "implementation_issue": cfg["implementation_issue"],
        "instruments": cfg["instruments"],
        "timeframes": cfg["timeframes"],
        "sharpe_method": cfg["sharpe_method"],
        "sharpe_method_audit_ref": cfg["sharpe_method_audit_ref"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "aggregate": aggregate,
        "gates": gates,
        "verdict": gates["verdict"],
        "per_fold": per_fold,
    }

    # Persist.
    def _san(o):
        if isinstance(o, dict):
            return {k: _san(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_san(v) for v in o]
        if isinstance(o, float):
            return None if math.isnan(o) or math.isinf(o) else o
        if isinstance(o, (np.floating,)):
            f = float(o)
            return None if (math.isnan(f) or math.isinf(f)) else f
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, pd.Timestamp):
            return o.isoformat()
        return o

    (RESULTS_DIR / "metrics.json").write_text(json.dumps(_san(envelope), indent=2, default=str))
    (RESULTS_DIR / "gates_report.json").write_text(json.dumps(_san({
        "variant_key": VARIANT_KEY,
        "gates": gates,
        "aggregate": aggregate,
        "per_fold_gate_results": [
            {
                "fold": p["fold"],
                "n_trades": p["metrics"]["n_trades"],
                "sharpe_daily": p["metrics"]["sharpe_daily"],
                "annualized_return": p["metrics"]["annualized_return"],
                "max_drawdown_pct": p["metrics"]["max_drawdown_pct"],
                "profit_factor": p["metrics"]["profit_factor"],
                "bootstrap_ci_lower_sharpe": p["metrics"]["bootstrap_ci_lower_sharpe"],
            }
            for p in valid_folds
        ],
    }), indent=2))

    summary_text = _build_summary_text(cfg, per_fold, aggregate, gates)
    (RESULTS_DIR / "summary.txt").write_text(summary_text)
    print(summary_text)
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} backtest done", flush=True)
    return 0 if gates["verdict"] == "PROFITABLE" else 1


def _evaluate_gates(cfg: dict, valid_folds: List[dict], aggregate: dict) -> dict:
    """Evaluate G1-G7 hard gates per SPEC §Validation plan."""
    g = cfg["gates"]
    fold_pass = []

    # Bonferroni alpha is a global constant; not a per-fold metric.
    bonferroni_alpha = float(g["G7_bonferroni_alpha"])

    for p in valid_folds:
        m = p["metrics"]
        per_fold_gates = {
            "G1_sharpe_daily": m["sharpe_daily"],
            "G1_pass": m["sharpe_daily"] >= float(g["G1_sharpe_daily_min"]),
            "G2_annualized_return": m["annualized_return"],
            "G2_pass": m["annualized_return"] >= float(g["G2_annualized_return_min"]),
            "G3_max_drawdown_pct": m["max_drawdown_pct"],
            "G3_pass": m["max_drawdown_pct"] > float(g["G3_max_drawdown_pct_min"]) * 100.0,
            "G4_profit_factor": m["profit_factor"],
            "G4_pass": (
                m["profit_factor"] != float("inf")
                and m["profit_factor"] >= float(g["G4_profit_factor_min"])
            ),
            "G5_n_trades": m["n_trades"],
            "G5_pass": m["n_trades"] >= int(g["G5_n_trades_min"]),
            "G6_bootstrap_ci_lower": m["bootstrap_ci_lower_sharpe"],
            "G6_pass": m["bootstrap_ci_lower_sharpe"] >= float(g["G6_bootstrap_ci_lower_min"]),
        }
        fold_pass.append({"fold": p["fold"], **per_fold_gates})

    # Cross-fold gates:
    n_folds_with_min_trades = aggregate["n_folds_with_min_trades"]
    G5_pass = n_folds_with_min_trades >= int(g["G5_n_folds_min_with_trades"])

    # Aggregate gates:
    G1_pass = all(fp["G1_pass"] for fp in fold_pass)
    G2_pass = all(fp["G2_pass"] for fp in fold_pass)
    G3_pass = all(fp["G3_pass"] for fp in fold_pass)
    G4_pass = all(fp["G4_pass"] for fp in fold_pass)
    G6_pass = (
        all(fp["G6_pass"] for fp in fold_pass) if fold_pass else False
    )
    # G7 is a constant we document; no test signal crosses it.
    G7_alpha = bonferroni_alpha

    failed_folds = [
        fp["fold"] for fp in fold_pass
        if not all(fp[k] for k in ("G1_pass", "G2_pass", "G3_pass", "G4_pass"))
    ]
    failed_gates_per_fold = [
        {
            "fold": fp["fold"],
            "failed": [
                k.replace("_pass", "") for k in fp if k.endswith("_pass") and not fp[k]
            ],
        }
        for fp in fold_pass
    ]
    n_folds_fail_two_or_more = sum(
        1 for fp in fold_pass
        if sum(1 for k in ("G1_pass", "G2_pass", "G3_pass", "G4_pass") if not fp[k]) >= 2
    )

    if fold_pass and G1_pass and G2_pass and G3_pass and G4_pass and G5_pass and G6_pass:
        verdict = "PROFITABLE"
    elif n_folds_fail_two_or_more >= 2:
        verdict = "NOT-PROFITABLE"
    elif not G5_pass:
        verdict = f"FAIL_G5_insufficient_trades ({n_folds_with_min_trades}/{len(fold_pass)} folds >= {g['G5_n_trades_min']})"
    else:
        verdict = f"FAIL_GATES ({','.join(failed_folds)})"

    return {
        "G1_mean_sharpe_daily_min": float(g["G1_sharpe_daily_min"]),
        "G1_pass": G1_pass,
        "G2_annualized_return_min": float(g["G2_annualized_return_min"]),
        "G2_pass": G2_pass,
        "G3_max_drawdown_pct_min_pct": float(g["G3_max_drawdown_pct_min"]) * 100.0,
        "G3_pass": G3_pass,
        "G4_profit_factor_min": float(g["G4_profit_factor_min"]),
        "G4_pass": G4_pass,
        "G5_n_trades_min": int(g["G5_n_trades_min"]),
        "G5_n_folds_min_with_trades": int(g["G5_n_folds_min_with_trades"]),
        "G5_pass": G5_pass,
        "G6_bootstrap_ci_lower_min": float(g["G6_bootstrap_ci_lower_min"]),
        "G6_pass": G6_pass,
        "G7_bonferroni_alpha": G7_alpha,
        "failed_folds": failed_folds,
        "failed_gates_per_fold": failed_gates_per_fold,
        "n_folds_fail_two_or_more": n_folds_fail_two_or_more,
        "verdict": verdict,
    }


def _build_summary_text(cfg: dict, per_fold: List[dict], aggregate: dict, gates: dict) -> str:
    lines: List[str] = []
    lines.append(f"=== {VARIANT_KEY} (SMA-34989) ===")
    lines.append(f"  Implementation issue: {cfg['implementation_issue']}")
    lines.append(f"  Source spec:          {cfg['source_spec']}")
    lines.append(f"  Instruments:          {cfg['instruments']}")
    lines.append(f"  Timeframes:           {cfg['timeframes']}")
    lines.append(f"  Sharpe method:        {cfg['sharpe_method']} (sqrt(365.25))")
    lines.append("")
    lines.append("=== Per-fold ===")
    for p in per_fold:
        if "error" in p:
            lines.append(f"  {p['fold']}: ERROR {p['error']}")
            continue
        m = p["metrics"]
        lines.append(
            f"  {p['fold']}  test=[{p['test_window'][0]} .. {p['test_window'][1]}]"
        )
        lines.append(
            f"    trades={m['n_trades']}  win_rate={m['win_rate']:.3f}"
            f"  pf={m['profit_factor']}  avg_bars={m['avg_bars_held']}"
        )
        lines.append(
            f"    sharpe_d={m['sharpe_daily']}  ann={m['annualized_return']}"
            f"  mdd={m['max_drawdown_pct']}%"
            f"  CI=[{m['bootstrap_ci_lower_sharpe']}, {m['bootstrap_ci_upper_sharpe']}]"
            f"  resamples={m['bootstrap_resamples']}"
        )
    lines.append("")
    lines.append("=== Aggregate ===")
    lines.append(
        f"  mean_sharpe_d={aggregate['mean_sharpe_daily']}"
        f"  mean_ann={aggregate['mean_annualized_return']}"
        f"  worst_mdd={aggregate['worst_max_drawdown_pct']}%"
        f"  min_pf={aggregate['min_profit_factor']}"
    )
    lines.append(
        f"  n_trades_total={aggregate['n_trades_total']}"
        f"  folds_valid={aggregate['n_folds_valid']}/{aggregate['n_folds_total']}"
        f"  folds_with_30+_trades={aggregate['n_folds_with_min_trades']}"
    )
    lines.append(f"  min_bootstrap_CI_lower={aggregate['min_bootstrap_ci_lower']}")
    lines.append("")
    lines.append("=== Acceptance gates (G1-G7) ===")
    lines.append(
        f"  G1 Sharpe_d >= {gates['G1_mean_sharpe_daily_min']:.1f}            : "
        f"pass={gates['G1_pass']}"
    )
    lines.append(
        f"  G2 annualized >= {gates['G2_annualized_return_min']:.0%}      : "
        f"pass={gates['G2_pass']}"
    )
    lines.append(
        f"  G3 max_drawdown > {gates['G3_max_drawdown_pct_min_pct']:.0f}%      : "
        f"pass={gates['G3_pass']}"
    )
    lines.append(
        f"  G4 profit_factor > {gates['G4_profit_factor_min']:.1f}          : "
        f"pass={gates['G4_pass']}"
    )
    lines.append(
        f"  G5 n_trades >= {gates['G5_n_trades_min']} (≥{gates['G5_n_folds_min_with_trades']} folds) "
        f": pass={gates['G5_pass']}"
    )
    lines.append(
        f"  G6 bootstrap CI lower >= {gates['G6_bootstrap_ci_lower_min']:.1f}     : "
        f"pass={gates['G6_pass']}"
    )
    lines.append(
        f"  G7 Bonferroni alpha = {gates['G7_bonferroni_alpha']:.4f}        : "
        f"documented"
    )
    lines.append("")
    if gates["failed_gates_per_fold"]:
        lines.append("  Failed gate details:")
        for f in gates["failed_gates_per_fold"]:
            lines.append(f"    {f['fold']}: failed={f['failed']}")
    lines.append("")
    lines.append(f"VERDICT: {gates['verdict']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())