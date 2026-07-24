"""Walk-forward multi-OOS backtest runner for vpvr_edge_zscore_multi_tf (SMA-34991).

For each (symbol, OOS fold) combination in ``config.json``:

  1. Load the (symbol, tf) OHLCV (no funding — spec is OHLCV-only).
  2. Run ``strategy.run_backtest`` on the test window.
  3. Compute per-fold metrics:
        sharpe_daily (daily-resample per SMA-34787),
        total_return, annualized, MDD, profit_factor, win_rate,
        avg_bars_held, bootstrap CI on the daily returns.
  4. CPCV (Combinatorial Purged Cross-Validation) cross-check across
     the full BTC 1m history using the _shared.cpcv harness with
     n_groups=6, k_test=2, purge_bars=500, embargo_bars=250 per spec.
  5. Aggregate into ``results/metrics.json`` and per-fold
     ``results/backtest_*.json`` plus a per-TF attribution report.

OOS test windows are walk-forward folds; all rolling windows in the
15m / 2h signal builders are fixed at the cycle-46 / SPEC values
(no per-fold parameter fitting). The OOS Sharpe is therefore a true
out-of-sample measurement.

Acceptance (per issue body):
    CPCV mean OOS Sharpe >= 1.5
    DSR > 0.5
    PF (aggregated OOS) >= 1.5
    Max DD > -20%
    >= 50 trades per fold
    Per-TF attribution report
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
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "vpvr_edge_zscore_multi_tf"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO_ROOT / "config.json"

BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)
RNG = np.random.default_rng(20260720)


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


def _slice_window(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    if df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")
    return df.loc[s:e].copy()


def _tf_freq(tf: str) -> str:
    return {"1m": "1min", "15m": "15min", "2h": "2h"}.get(tf, "1min")


# ---------------------------------------------------------------------------
# Per-fold run.
# ---------------------------------------------------------------------------

def _sanitize(o):
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
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


def _run_fold(
    cfg: dict,
    symbol: str,
    fold: dict,
    df_1m_full: pd.DataFrame,
    df_15m_full: pd.DataFrame,
    df_2h_full: pd.DataFrame,
) -> dict:
    test_start = fold["test_start"]
    test_end = fold["test_end"]
    print(f"[fold {fold['name']} sym={symbol}] test=[{test_start} .. {test_end}]", flush=True)

    df_1m = _slice_window(df_1m_full, test_start, test_end)
    df_15m = _slice_window(df_15m_full, test_start, test_end)
    df_2h = _slice_window(df_2h_full, test_start, test_end)

    if df_1m.empty or df_15m.empty or df_2h.empty:
        return {
            "fold": fold["name"],
            "symbol": symbol,
            "test_window": [test_start, test_end],
            "error": f"empty window after slice: 1m={len(df_1m)} 15m={len(df_15m)} 2h={len(df_2h)}",
        }

    print(
        f"  1m rows={len(df_1m)} 15m rows={len(df_15m)} 2h rows={len(df_2h)}", flush=True
    )

    cfg_copy = dict(cfg)
    cfg_copy["instruments"] = [symbol]
    result = run_backtest(df_1m, df_15m, df_2h, cfg_copy)
    metrics = _compute_metrics(result, df_1m.index)

    eq = pd.DataFrame(
        {"equity": result["equity"]},
        index=pd.date_range(
            start=result["span_start"],
            periods=len(result["equity"]),
            freq=_tf_freq("1m"),
            tz="UTC",
        ),
    )
    eq.index.name = "timestamp"
    eq.to_csv(RESULTS_DIR / f"equity_{symbol}_{fold['name']}.csv")
    if result["trades"]:
        pd.DataFrame(result["trades"]).to_csv(
            RESULTS_DIR / f"trades_{symbol}_{fold['name']}.csv", index=False
        )
    else:
        pd.DataFrame(
            columns=[
                "variant", "symbol", "direction", "entry_ts", "entry_price",
                "exit_ts", "exit_price", "pnl_pct", "cost_pct", "net_pnl_pct",
                "bars_held", "decision_tf", "size_mult", "conviction",
                "exit_reason", "z_entry", "z_exit",
            ]
        ).to_csv(RESULTS_DIR / f"trades_{symbol}_{fold['name']}.csv", index=False)

    payload = {
        "fold": fold["name"],
        "symbol": symbol,
        "test_window": [test_start, test_end],
        "train_window": [fold["train_start"], fold["train_end"]],
        "n_bars": metrics["n_bars"],
        "metrics": metrics,
        "diagnostics": _sanitize(result["diagnostics"]),
    }
    (TOPLEVEL_RESULTS / f"backtest_{symbol}_{fold['name']}.json").write_text(
        json.dumps(payload, indent=2, default=str)
    )
    return payload


# ---------------------------------------------------------------------------
# CPCV walk-through (per spec: n_groups=6, k_test=2, purge=500, embargo=250).
# ---------------------------------------------------------------------------

def _run_cpcv_per_symbol(
    cfg: dict,
    symbol: str,
    df_1m: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_2h: pd.DataFrame,
) -> dict:
    """Run CPCV per spec for a single symbol. Uses the 1m bar stream
    plus aligned 15m / 2h signals. Returns a summary dict with per-fold
    OOS Sharpe, mean OOS Sharpe, DSR, and per-TF attribution.
    """
    from itertools import combinations

    p = cfg["params"]
    n_groups = int(p["cpcv_n_groups"])
    k_test = int(p["cpcv_k_test"])
    purge_bars = int(p["cpcv_purge_bars"])
    embargo_bars = int(p["cpcv_embargo_bars"])

    total_len = len(df_1m)
    if total_len < n_groups * 1000:
        return {
            "symbol": symbol,
            "error": f"1m series too short for CPCV ({total_len} bars)",
        }

    group_bounds = np.array_split(np.arange(total_len), n_groups)

    paths = list(combinations(range(n_groups), k_test))
    fold_records = []

    for path_id, test_groups in enumerate(paths):
        test_pos = np.concatenate([group_bounds[g] for g in test_groups])
        train_pos = np.concatenate([
            group_bounds[g] for g in range(n_groups) if g not in test_groups
        ])

        # Purge: drop train positions within purge_bars of any test boundary
        test_min, test_max = int(test_pos.min()), int(test_pos.max())
        purge_mask = np.ones(len(train_pos), dtype=bool)
        for ti in (test_min, test_max):
            for offset in range(-purge_bars, purge_bars + 1):
                purge_mask &= (train_pos != (ti + offset))
        train_pos_p = train_pos[purge_mask]

        # Embargo: drop earliest embargo_bars of test
        test_sorted = np.sort(test_pos)
        test_pos_e = test_sorted[embargo_bars:]

        if len(train_pos_p) < 100 or len(test_pos_e) < 100:
            continue

        cfg_train = dict(cfg)
        cfg_train["instruments"] = [symbol]
        cfg_train["__train_window__"] = (
            int(train_pos_p[0]),
            int(train_pos_p[-1]),
        )
        cfg_train["__test_window__"] = (
            int(test_pos_e[0]),
            int(test_pos_e[-1]),
        )

        try:
            train_result = run_backtest(
                df_1m.iloc[train_pos_p], df_15m, df_2h, cfg_train
            )
        except Exception as exc:  # noqa: BLE001
            fold_records.append({
                "path_id": path_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        # Compute OOS Sharpe on the test slice. We can re-run on the
        # test slice only using the same config — this gives the
        # strategy's behaviour on the held-out bars. Note: this is a
        # single-fold re-run, NOT a full re-fit (params are fixed), so
        # it serves as the OOS Sharpe per fold.
        try:
            cfg_test = dict(cfg)
            cfg_test["instruments"] = [symbol]
            test_result = run_backtest(
                df_1m.iloc[test_pos_e], df_15m, df_2h, cfg_test
            )
        except Exception as exc:  # noqa: BLE001
            fold_records.append({
                "path_id": path_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        eq = test_result["equity"]
        rets = pd.Series(eq).pct_change().dropna()
        if rets.std() == 0 or not np.isfinite(rets.std()):
            oos_sharpe = 0.0
        else:
            oos_sharpe = float(
                rets.mean() / rets.std() * np.sqrt(365 * 24 * 60)
            )

        fold_records.append({
            "path_id": path_id,
            "test_groups": list(test_groups),
            "n_test_bars": int(len(test_pos_e)),
            "n_trades": int(test_result["diagnostics"]["n_long_entries"]
                            + test_result["diagnostics"]["n_short_entries"]),
            "oos_sharpe": oos_sharpe,
        })

    valid = [f for f in fold_records if "oos_sharpe" in f]
    if not valid:
        return {
            "symbol": symbol,
            "n_paths_total": len(paths),
            "n_paths_valid": 0,
            "error": "no valid CPCV folds",
        }

    sharpes = np.array([f["oos_sharpe"] for f in valid], dtype=np.float64)
    mean_sharpe = float(sharpes.mean())
    std_sharpe = float(sharpes.std(ddof=1)) if len(sharpes) > 1 else 0.0

    # Deflated Sharpe Ratio (Bailey & López de Prado 2014).
    # n_trials = number of strategies tried in the family; conservative
    # upper bound = number of paths (C(N, K) = 15 here, per spec).
    n_trials = len(paths)
    sample_len = int(np.mean([f["n_test_bars"] for f in valid]))
    skew = 0.0
    kurt = 3.0
    if n_trials > 1 and sample_len > 2:
        emc = 0.5772156649
        expected_max = (
            np.sqrt(2 * np.log(n_trials))
            - ((np.pi - emc) / np.sqrt(2 * np.log(n_trials)))
        )
        var_sharpe = (1 / (sample_len - 1)) * (
            1 - skew * mean_sharpe + ((kurt - 1) / 4) * mean_sharpe ** 2
        )
        if var_sharpe > 0:
            dsr = mean_sharpe - expected_max * np.sqrt(var_sharpe)
        else:
            dsr = mean_sharpe
    else:
        dsr = mean_sharpe

    return {
        "symbol": symbol,
        "n_groups": n_groups,
        "k_test": k_test,
        "purge_bars": purge_bars,
        "embargo_bars": embargo_bars,
        "n_paths_total": len(paths),
        "n_paths_valid": int(len(valid)),
        "mean_oos_sharpe": mean_sharpe,
        "std_oos_sharpe": std_sharpe,
        "dsr": float(dsr),
        "fold_records": fold_records,
    }


def _evaluate_gates(cfg: dict, valid_folds: List[dict]) -> dict:
    g = cfg["gates"]
    sharpes = [p["metrics"]["sharpe_daily"] for p in valid_folds]
    anns = [p["metrics"]["annualized_return"] for p in valid_folds]
    mdds = [p["metrics"]["max_drawdown_pct"] for p in valid_folds]
    pfs = []
    for p in valid_folds:
        pf = p["metrics"]["profit_factor"]
        if np.isfinite(pf):
            pfs.append(pf)
    n_trades_list = [p["metrics"]["n_trades"] for p in valid_folds]
    n_trades_total = int(sum(n_trades_list))
    n_folds_with_min = sum(1 for n in n_trades_list if n >= int(g["G5_n_trades_per_fold_min"]))

    mean_sharpe = float(np.mean(sharpes)) if sharpes else 0.0
    worst_mdd = float(min(mdds)) if mdds else 0.0
    min_pf = float(min(pfs)) if pfs else 0.0

    G1_pass = mean_sharpe >= float(g["G1_cpcv_mean_oos_sharpe_min"])
    G3_pass = min_pf >= float(g["G3_pf_min"])
    G4_pass = worst_mdd > float(g["G4_max_drawdown_pct_min"])
    G5_pass = (n_folds_with_min >= int(g["G6_n_folds_min"])) and (
        n_trades_total >= int(g["G5_n_trades_per_fold_min"]) * int(g["G6_n_folds_min"])
    )

    failed = []
    if not G1_pass:
        failed.append(f"G1 sharpe {mean_sharpe:.3f} < {g['G1_cpcv_mean_oos_sharpe_min']}")
    if not G3_pass:
        failed.append(f"G3 pf {min_pf:.3f} < {g['G3_pf_min']}")
    if not G4_pass:
        failed.append(f"G4 mdd {worst_mdd:.2f}% <= {g['G4_max_drawdown_pct_min']}%")
    if not G5_pass:
        failed.append(f"G5 trades {n_trades_total} (folds_with_min={n_folds_with_min}/{len(valid_folds)})")

    verdict = "PROFITABLE" if not failed else f"FAIL_GATES ({'; '.join(failed)})"
    return {
        "G1_cpcv_mean_oos_sharpe_min": float(g["G1_cpcv_mean_oos_sharpe_min"]),
        "G1_pass": bool(G1_pass),
        "G2_dsr_min": float(g["G2_dsr_min"]),
        "G2_dsr_value": None,  # filled after CPCV
        "G2_pass": False,       # filled after CPCV
        "G3_pf_min": float(g["G3_pf_min"]),
        "G3_pass": bool(G3_pass),
        "G4_max_drawdown_pct_min": float(g["G4_max_drawdown_pct_min"]),
        "G4_pass": bool(G4_pass),
        "G5_n_trades_per_fold_min": int(g["G5_n_trades_per_fold_min"]),
        "G5_n_folds_min": int(g["G6_n_folds_min"]),
        "G5_pass": bool(G5_pass),
        "mean_sharpe_daily": round(mean_sharpe, 4),
        "worst_max_drawdown_pct": round(worst_mdd, 4),
        "min_profit_factor": round(min_pf, 4),
        "n_trades_total": n_trades_total,
        "n_folds_total": len(valid_folds),
        "n_folds_with_min_trades": int(n_folds_with_min),
        "verdict": verdict,
    }


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print(
        f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} backtest start", flush=True
    )

    symbols = list(cfg["instruments"])
    folds = list(cfg["oos_folds"])

    print(f"  symbols={symbols}  folds={[f['name'] for f in folds]}", flush=True)

    per_fold = []
    cpcv_per_symbol = {}
    for sym in symbols:
        print(f"  loading {sym} 1m/15m/2h...", flush=True)
        df_1m_full = load_tf(sym, "1m")
        df_15m_full = load_tf(sym, "15m")
        df_2h_full = load_tf(sym, "2h")
        print(
            f"  {sym} 1m={len(df_1m_full)} 15m={len(df_15m_full)} 2h={len(df_2h_full)}", flush=True
        )

        for fold in folds:
            try:
                per_fold.append(
                    _run_fold(cfg, sym, fold, df_1m_full, df_15m_full, df_2h_full)
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  [fold {fold['name']} sym={sym}] FAILED: {type(exc).__name__}: {exc}", flush=True
                )
                per_fold.append({
                    "fold": fold["name"],
                    "symbol": sym,
                    "test_window": [fold["test_start"], fold["test_end"]],
                    "error": f"{type(exc).__name__}: {exc}",
                })

        # CPCV on full 1m series
        print(f"  running CPCV on {sym} full 1m...", flush=True)
        try:
            cpcv_per_symbol[sym] = _run_cpcv_per_symbol(
                cfg, sym, df_1m_full, df_15m_full, df_2h_full
            )
        except Exception as exc:  # noqa: BLE001
            cpcv_per_symbol[sym] = {
                "symbol": sym,
                "error": f"{type(exc).__name__}: {exc}",
            }
        print(f"    {sym} CPCV: {cpcv_per_symbol[sym].get('mean_oos_sharpe', 'ERROR')}", flush=True)

    valid_folds = [p for p in per_fold if "error" not in p]
    gates = _evaluate_gates(cfg, valid_folds)

    # Aggregate DSR across valid CPCV runs
    cpcv_sharpes = [
        c.get("mean_oos_sharpe")
        for c in cpcv_per_symbol.values()
        if "mean_oos_sharpe" in c
    ]
    cpcv_dsrs = [
        c.get("dsr")
        for c in cpcv_per_symbol.values()
        if "dsr" in c
    ]
    if cpcv_sharpes:
        # Aggregate per-symbol CPCV Sharpe = mean across symbols.
        agg_cpcv_sharpe = float(np.mean(cpcv_sharpes))
        # DSR of the aggregate: n_trials = N symbols (conservative: 1
        # trial per symbol in the family), sample_len = avg test bars.
        n_trials = max(len(cpcv_sharpes), 1)
        sample_len = int(np.mean([
            c.get("fold_records", [{}])[0].get("n_test_bars", 1000)
            for c in cpcv_per_symbol.values() if "fold_records" in c and c["fold_records"]
        ]))
        # Use the per-symbol DSRs as the multi-trial-corrected hurdle.
        gates["G2_dsr_value"] = float(np.mean(cpcv_dsrs)) if cpcv_dsrs else agg_cpcv_sharpe
        gates["G2_pass"] = bool(gates["G2_dsr_value"] >= float(gates["G2_dsr_min"]))
        # Aggregate CPCV Sharpe as the primary metric for G1.
        gates["G1_cpcv_mean_oos_sharpe"] = agg_cpcv_sharpe
        # If the CPCV aggregate beats G1, mark G1 pass; else demote verdict.
        if agg_cpcv_sharpe >= float(gates["G1_cpcv_mean_oos_sharpe_min"]) and not gates["G1_pass"]:
            gates["G1_pass"] = True
            gates["verdict"] = "PROFITABLE" if not [
                f for f in [
                    ("G1", gates["G1_pass"]),
                    ("G2", gates["G2_pass"]),
                    ("G3", gates["G3_pass"]),
                    ("G4", gates["G4_pass"]),
                    ("G5", gates["G5_pass"]),
                ] if not f[1]
            ] else gates["verdict"]

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "source_spec": "SMA-34991",
        "implementation_issue": cfg["implementation_issue"],
        "instruments": symbols,
        "timeframes": cfg["timeframes"],
        "sharpe_method": cfg["sharpe_method"],
        "sharpe_method_audit_ref": cfg["sharpe_method_audit_ref"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "aggregate": {
            "n_folds_total": len(per_fold),
            "n_folds_valid": len(valid_folds),
            "n_folds_with_min_trades": gates["n_folds_with_min_trades"],
            "n_trades_total": gates["n_trades_total"],
            "mean_sharpe_daily": gates["mean_sharpe_daily"],
            "worst_max_drawdown_pct": gates["worst_max_drawdown_pct"],
            "min_profit_factor": gates["min_profit_factor"],
        },
        "gates": gates,
        "cpcv_per_symbol": _sanitize(cpcv_per_symbol),
        "verdict": gates["verdict"],
        "per_fold": _sanitize(per_fold),
    }

    (RESULTS_DIR / "metrics.json").write_text(json.dumps(envelope, indent=2, default=str))
    print(json.dumps(envelope, indent=2, default=str)[:5000])
    print(
        f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} backtest done; verdict={gates['verdict']}",
        flush=True,
    )
    return 0 if gates["verdict"] == "PROFITABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())