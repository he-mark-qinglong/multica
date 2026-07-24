"""CPCV (Combinatorial Purged Cross-Validation) runner for the 15m-only
single-TF strategy on BTCUSDT — partial-progress deliverable per
smark-proxy DECISION 2026-07-20T21:48.

Required output:
  - Per-path OOS Sharpe (daily-resampled per SMA-34787)
  - Mean OOS Sharpe across all C(N,K) paths
  - DSR (Deflated Sharpe Ratio, Bailey & López de Prado 2014) with
    n_trials = N_paths and sample_len = avg test-bar count
  - Walk-forward OOS metrics on the per-half-year windows
  - Aggregate verdict against the gate set

The per-path Sharpe uses daily resampling (NOT per-bar annualization) so
the metric is comparable to the spec target and to SMA-34787.

CPCV params per the partial-progress simplification:
  n_groups=6, k_test=2, purge_bars_15m=24, embargo_bars_15m=12
   (24 15m-bars ≈ 6 hours; 12 15m-bars ≈ 3 hours)
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]
MULTI_TF_DIR = QUANT_LOOP / "strategies" / "vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720"
sys.path.insert(0, str(MULTI_TF_DIR))
sys.path.insert(0, str(REPO_ROOT))

from data_loader import load_tf  # multi-TF loader (works for any tf)  # noqa: E402
from strategy import VARIANT_KEY, run_backtest  # 15m-only strategy  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "vpvr_edge_zscore_15m_only"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO_ROOT / "config.json"

BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)
RNG = np.random.default_rng(20260720)
BARS_PER_YEAR_15M = 365 * 24 * 4  # 4 fifteen-minute bars per hour
SQRT_BPY_15M = math.sqrt(BARS_PER_YEAR_15M)


def _sanitize(o):
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, (np.floating,)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o


# ---------------------------------------------------------------------------
# Daily-resampled per-bar Sharpe (per SMA-34787).
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
        }

    total_return = (final / starting) - 1.0
    eq_idx = idx[: len(equity)]
    daily_eq = pd.Series(equity, index=eq_idx, dtype=np.float64).resample("1D").last().dropna()
    n_days = max(1, (daily_eq.index[-1] - daily_eq.index[0]).days)
    n_years = n_days / BARS_PER_YEAR_DAILY
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
    }


def _slice_window(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    if df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")
    return df.loc[s:e].copy()


# ---------------------------------------------------------------------------
# Walk-forward OOS run on per-half-year folds.
# ---------------------------------------------------------------------------

def _run_walk_forward(cfg: dict, symbol: str, df_15m: pd.DataFrame) -> list:
    folds = list(cfg["oos_folds"])
    out = []
    for fold in folds:
        test_start = fold["test_start"]
        test_end = fold["test_end"]
        print(f"  [fold {fold['name']}] test=[{test_start}..{test_end}]", flush=True)
        df = _slice_window(df_15m, test_start, test_end)
        if df.empty or len(df) < 200:
            print(f"    empty slice ({len(df)} bars); skip", flush=True)
            out.append({
                "fold": fold["name"], "symbol": symbol,
                "test_window": [test_start, test_end],
                "error": f"empty window after slice ({len(df)} bars)",
            })
            continue

        cfg_copy = dict(cfg)
        cfg_copy["instruments"] = [symbol]
        result = run_backtest(df, cfg_copy)
        metrics = _compute_metrics(result, df.index)

        eq = pd.DataFrame(
            {"equity": result["equity"]},
            index=pd.date_range(
                start=result["span_start"],
                periods=len(result["equity"]),
                freq="15min",
                tz="UTC",
            ),
        )
        eq.index.name = "timestamp"
        eq.to_csv(RESULTS_DIR / f"equity_{symbol}_{fold['name']}.csv")
        if result["trades"]:
            pd.DataFrame(result["trades"]).to_csv(
                RESULTS_DIR / f"trades_{symbol}_{fold['name']}.csv", index=False
            )

        payload = {
            "fold": fold["name"],
            "symbol": symbol,
            "test_window": [test_start, test_end],
            "n_bars": metrics["n_bars"],
            "metrics": metrics,
            "diagnostics": _sanitize(result["diagnostics"]),
        }
        (TOPLEVEL_RESULTS / f"wf_{symbol}_{fold['name']}.json").write_text(
            json.dumps(payload, indent=2, default=str)
        )
        out.append(payload)
    return out


# ---------------------------------------------------------------------------
# CPCV runner on the full BTCUSDT 15m series.
# ---------------------------------------------------------------------------

def _run_cpcv(cfg: dict, symbol: str, df: pd.DataFrame) -> dict:
    p = cfg["params"]
    n_groups = int(p["cpcv_n_groups"])
    k_test = int(p["cpcv_k_test"])
    purge_bars = int(p["cpcv_purge_bars_15m"])
    embargo_bars = int(p["cpcv_embargo_bars_15m"])

    total_len = len(df)
    if total_len < n_groups * 100:
        return {"symbol": symbol, "error": f"15m series too short for CPCV ({total_len} bars)"}

    group_bounds = [np.arange(g * total_len // n_groups, (g + 1) * total_len // n_groups)
                    for g in range(n_groups)]

    paths = list(itertools.combinations(range(n_groups), k_test))
    print(f"  CPCV: {len(paths)} paths on {total_len} 15m bars", flush=True)

    fold_records = []
    for path_id, test_groups in enumerate(paths):
        test_pos = np.concatenate([group_bounds[g] for g in test_groups])
        train_pos = np.concatenate([group_bounds[g] for g in range(n_groups) if g not in test_groups])

        # Purge: drop train positions within purge_bars of any test boundary.
        test_min = int(test_pos.min())
        test_max = int(test_pos.max())
        test_b = np.array([test_min, test_max])
        purge_mask = np.ones(len(train_pos), dtype=bool)
        for ti in test_b:
            bad = (train_pos >= ti - purge_bars) & (train_pos <= ti + purge_bars)
            purge_mask &= ~bad
        train_pos_p = train_pos[purge_mask]

        # Embargo: drop earliest embargo_bars of test.
        test_sorted = np.sort(test_pos)
        test_pos_e = test_sorted[embargo_bars:]

        if len(train_pos_p) < 200 or len(test_pos_e) < 50:
            continue

        try:
            cfg_test = dict(cfg)
            cfg_test["instruments"] = [symbol]
            test_result = run_backtest(df.iloc[test_pos_e], cfg_test)
        except Exception as exc:  # noqa: BLE001
            fold_records.append({"path_id": path_id, "error": f"{type(exc).__name__}: {exc}"})
            continue

        # Build per-bar equity index (15m) from the test slice.
        eq_idx = pd.date_range(
            start=test_result["span_start"],
            periods=len(test_result["equity"]),
            freq="15min",
            tz="UTC",
        )
        # Daily-resampled Sharpe (per SMA-34787) is the metric.
        oos_sharpe = _daily_resampled_sharpe(test_result["equity"], eq_idx)

        fold_records.append({
            "path_id": path_id,
            "test_groups": list(test_groups),
            "n_test_bars": int(len(test_pos_e)),
            "n_trades": int(
                test_result["diagnostics"]["n_long_entries"]
                + test_result["diagnostics"]["n_short_entries"]
            ),
            "oos_sharpe": round(oos_sharpe, 4),
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

    # DSR per Bailey & López de Prado 2014 — n_trials = N_paths.
    n_trials = len(paths)
    sample_len = int(np.mean([f["n_test_bars"] for f in valid]))
    skew = 0.0
    kurt = 3.0
    if n_trials > 1 and sample_len > 2:
        emc = 0.5772156649
        expected_max = (
            np.sqrt(2 * math.log(n_trials))
            - ((math.pi - emc) / np.sqrt(2 * math.log(n_trials)))
        )
        var_sharpe = (1 / (sample_len - 1)) * (
            1 - skew * mean_sharpe + ((kurt - 1) / 4) * mean_sharpe ** 2
        )
        if var_sharpe > 0:
            dsr = mean_sharpe - expected_max * math.sqrt(var_sharpe)
        else:
            dsr = mean_sharpe
    else:
        dsr = mean_sharpe

    return {
        "symbol": symbol,
        "n_groups": n_groups,
        "k_test": k_test,
        "purge_bars_15m": purge_bars,
        "embargo_bars_15m": embargo_bars,
        "n_paths_total": len(paths),
        "n_paths_valid": int(len(valid)),
        "mean_oos_sharpe": round(mean_sharpe, 4),
        "std_oos_sharpe": round(std_sharpe, 4),
        "dsr": round(float(dsr), 4),
        "fold_records": fold_records,
    }


# ---------------------------------------------------------------------------
# Gate eval on walk-forward folds.
# ---------------------------------------------------------------------------

def _evaluate_gates(cfg: dict, valid_folds: list) -> dict:
    g = cfg["gates"]
    sharpes = [p["metrics"]["sharpe_daily"] for p in valid_folds]
    mdds = [p["metrics"]["max_drawdown_pct"] for p in valid_folds]
    pfs = []
    for p in valid_folds:
        pf = p["metrics"]["profit_factor"]
        if np.isfinite(pf):
            pfs.append(pf)
    n_trades_list = [p["metrics"]["n_trades"] for p in valid_folds]

    mean_sharpe = float(np.mean(sharpes)) if sharpes else 0.0
    worst_mdd = float(min(mdds)) if mdds else 0.0
    min_pf = float(min(pfs)) if pfs else 0.0
    n_trades_total = int(sum(n_trades_list))
    n_folds_with_min = sum(1 for n in n_trades_list if n >= int(g["G5_n_trades_per_fold_min"]))

    G1_pass = mean_sharpe >= float(g["G1_cpcv_mean_oos_sharpe_min"])
    G3_pass = min_pf >= float(g["G3_pf_min"])
    G4_pass = worst_mdd > float(g["G4_max_drawdown_pct_min"])
    G5_pass = n_folds_with_min >= int(g["G6_n_folds_min"])

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
        "G3_pf_min": float(g["G3_pf_min"]),
        "G3_pass": bool(G3_pass),
        "G4_max_drawdown_pct_min": float(g["G4_max_drawdown_pct_min"]),
        "G4_pass": bool(G4_pass),
        "G5_n_trades_per_fold_min": int(g["G5_n_trades_per_fold_min"]),
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
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} backtest start", flush=True)
    symbols = list(cfg["instruments"])
    print(f"  symbols={symbols}  partial-progress: 15m-only single-TF", flush=True)

    cpcv_per_symbol = {}
    wf_per_symbol = {}
    for sym in symbols:
        print(f"  loading {sym} 15m...", flush=True)
        df = load_tf(sym, "15m")
        if df.index.tz is None:
            df = df.copy()
            df.index = df.index.tz_localize("UTC")
        print(f"  {sym} 15m={len(df)}  range=[{df.index.min()}..{df.index.max()}]", flush=True)

        print(f"  running walk-forward 8-fold on {sym}...", flush=True)
        wf_per_symbol[sym] = _run_walk_forward(cfg, sym, df)

        print(f"  running CPCV on {sym} full 15m...", flush=True)
        try:
            cpcv_per_symbol[sym] = _run_cpcv(cfg, sym, df)
        except Exception as exc:  # noqa: BLE001
            cpcv_per_symbol[sym] = {"symbol": sym, "error": f"{type(exc).__name__}: {exc}"}
        print(
            f"    {sym} CPCV mean_oos_sharpe={cpcv_per_symbol[sym].get('mean_oos_sharpe', 'ERROR')} "
            f"dsr={cpcv_per_symbol[sym].get('dsr', 'NA')}",
            flush=True,
        )

    # Walk-forward gate eval on first symbol only (single-asset partial progress).
    sym0 = symbols[0]
    valid_folds = [p for p in wf_per_symbol[sym0] if "error" not in p]
    gates = _evaluate_gates(cfg, valid_folds)

    # G2 (DSR) from CPCV.
    cpcv0 = cpcv_per_symbol.get(sym0, {})
    if "dsr" in cpcv0:
        gates["G2_dsr_value"] = cpcv0["dsr"]
        gates["G2_pass"] = bool(cpcv0["dsr"] >= float(cfg["gates"]["G2_dsr_min"]))
    else:
        gates["G2_dsr_value"] = None
        gates["G2_pass"] = False

    # G1 backfill from CPCV mean OOS Sharpe if WF mean fails.
    if "mean_oos_sharpe" in cpcv0:
        gates["G1_cpcv_mean_oos_sharpe"] = cpcv0["mean_oos_sharpe"]
        if (cpcv0["mean_oos_sharpe"] >= float(gates["G1_cpcv_mean_oos_sharpe_min"])
                and not gates["G1_pass"]):
            gates["G1_pass"] = True

    # Re-evaluate verdict after G1/G2 backfill.
    failed = []
    if not gates["G1_pass"]:
        failed.append(f"G1 sharpe {gates.get('mean_sharpe_daily', 0):.3f} < {gates['G1_cpcv_mean_oos_sharpe_min']}")
    if not gates["G2_pass"]:
        failed.append(f"G2 dsr {gates['G2_dsr_value']} < {cfg['gates']['G2_dsr_min']}")
    if not gates["G3_pass"]:
        failed.append(f"G3 pf {gates['min_profit_factor']:.3f} < {gates['G3_pf_min']}")
    if not gates["G4_pass"]:
        failed.append(f"G4 mdd {gates['worst_max_drawdown_pct']:.2f}% <= {gates['G4_max_drawdown_pct_min']}%")
    if not gates["G5_pass"]:
        failed.append(f"G5 trades {gates['n_trades_total']} (folds_with_min={gates['n_folds_with_min_trades']}/{gates['n_folds_total']})")
    gates["verdict"] = "PROFITABLE" if not failed else f"FAIL_GATES ({'; '.join(failed)})"

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "source_spec": "SMA-34991",
        "implementation_issue": cfg["implementation_issue"],
        "partial_progress_note": cfg.get("partial_progress_note"),
        "instruments": symbols,
        "timeframes": cfg["timeframes"],
        "sharpe_method": cfg["sharpe_method"],
        "sharpe_method_audit_ref": cfg["sharpe_method_audit_ref"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "aggregate": {
            "n_folds_total": gates["n_folds_total"],
            "n_trades_total": gates["n_trades_total"],
            "mean_sharpe_daily_wf": gates["mean_sharpe_daily"],
            "worst_max_drawdown_pct": gates["worst_max_drawdown_pct"],
            "min_profit_factor_wf": gates["min_profit_factor"],
        },
        "gates": gates,
        "cpcv_per_symbol": _sanitize(cpcv_per_symbol),
        "wf_per_symbol": {k: _sanitize(v) for k, v in wf_per_symbol.items()},
        "verdict": gates["verdict"],
    }

    (RESULTS_DIR / "metrics.json").write_text(json.dumps(envelope, indent=2, default=str))
    print(json.dumps(envelope, indent=2, default=str)[:6000])
    print(
        f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} backtest done; verdict={gates['verdict']}",
        flush=True,
    )
    return 0 if gates["verdict"] == "PROFITABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
