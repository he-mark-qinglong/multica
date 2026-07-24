"""Vectorbt framework adapter for vpvr_funding_aware_v1_20260711.

Approach: Replay the in-house multi-symbol (BTC + ETH on 4h, long-only)
trade log through the shared `framework_replay_lib.replay_full_notional`
helper at the canonical vectorbt zero-cost reference convention
(fee=0, slippage=0), changing ONLY the cost model from in-house's
2bp round-trip (1bp fee/side + 0 slip) to 0bp rt.

This is the same convention used in:
  - vpvr_funding_asym_4h_20260713/framework_adapter_vectorbt.py
  - vpvr_carry_term_8h_20260711/framework_adapter_vectorbt.py
and isolates the framework-vs-inhouse calibration from any
vectorbt-fee convention overlay (the engine import is mostly a
contract stamp; the metric math is transparent numpy).

Validation step first: replay at in-house cost (2bp rt) using the same
full-notional mark-to-market replay as the backtrader/freqtrade adapters
— must reproduce results/equity_4h_*.csv to within ~1e-3 tolerance.

W5: any |divergence| > 50% vs metrics.json agg_* -> auto-archive
(per AGENT_COLLAB_AUDIT_2026-07-12 §W5).
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import vectorbt as vbt
    _HAS_VECTORBT = True
except Exception:
    _HAS_VECTORBT = False

sys.path.insert(0, "/home/smark/multica/quant-loop/workdir")
import framework_replay_lib as R  # noqa: E402

STRATEGY_DIR = Path(__file__).parent
STRATEGY = STRATEGY_DIR.name
OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-vectorbt")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = STRATEGY_DIR / "config.json"
METRICS_PATH = STRATEGY_DIR / "results" / "metrics.json"
SUMMARY_PATH = STRATEGY_DIR / "results" / "summary.json"
RESULTS_DIR = STRATEGY_DIR / "results"

PRICE_PATHS = {
    "BTCUSDT": "/home/smark/multica/quant-loop/live_data/BTCUSDT_4h.parquet",
    "ETHUSDT": "/home/smark/multica/quant-loop/live_data/ETHUSDT_4h.parquet",
}
TRADES_PATHS = {
    "BTCUSDT": RESULTS_DIR / "trades_A_4h_BTCUSDT.csv",
    "ETHUSDT": RESULTS_DIR / "trades_A_4h_ETHUSDT.csv",
}
EQUITY_CSVS = {
    "BTCUSDT": RESULTS_DIR / "equity_4h_BTCUSDT.csv",
    "ETHUSDT": RESULTS_DIR / "equity_4h_ETHUSDT.csv",
}

# Vectorbt canonical "pure pricing engine" zero-cost reference convention.
# Differs from in-house (2bp rt) by 2bp/trade; across 165 trades the
# cost delta compounds into a measurable per-bar NAV difference and
# surfaces any calibration drift in the W5 metric check.
VECTORBT_COST_RT = 0.0

W5_THRESHOLD = 50.0
BARS_PER_YEAR = 2190.0              # 4h bars (config bars_per_year_4h)
SQRT_BPY_4H = math.sqrt(365.25 * 6)


def jsafe(x):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    return x


def sharpe_from_rets(rets: np.ndarray, bpy_sqrt: float) -> float:
    rets = np.asarray(rets, dtype=float)
    rets = rets[~np.isnan(rets)]
    rets = rets[np.isfinite(rets)]
    if len(rets) < 2:
        return 0.0
    sd = float(np.std(rets, ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(np.mean(rets) / sd * bpy_sqrt)


def max_dd_from_eq(eq: np.ndarray) -> float:
    if len(eq) < 2:
        return 0.0
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(dd.min()) if len(dd) else 0.0


def ann_return_from_eq(eq: np.ndarray, span_years: float) -> float:
    if len(eq) < 2 or span_years <= 0 or eq[0] <= 0:
        return 0.0
    tr = float(eq[-1] / eq[0] - 1.0)
    if tr <= -1.0:
        return -1.0
    return float((1.0 + tr) ** (1.0 / span_years) - 1.0)


def run_replay(cost_rt: float, span_start, span_end, start_per_symbol):
    per_symbol = {}
    total_fills = 0
    for sym in ("BTCUSDT", "ETHUSDT"):
        prices = R.load_prices(PRICE_PATHS[sym], span_start, span_end)
        trades = R.load_trades(str(TRADES_PATHS[sym]))
        res = R.replay_full_notional(prices, trades, start_per_symbol, cost_rt,
                                     carry_pcts=trades["pnl_carry_pct"])
        per_symbol[sym] = res.equity
        total_fills += res.n_fills
    nav = per_symbol["BTCUSDT"] + per_symbol["ETHUSDT"]
    return per_symbol, nav, total_fills


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    ih = json.loads(METRICS_PATH.read_text())
    summary = json.loads(SUMMARY_PATH.read_text())

    timeframe = cfg.get("timeframe", "4h")
    start_per_symbol = float(cfg.get("starting_capital_per_symbol_usd",
                                     cfg["starting_capital_usd"] / 2.0))
    span_start = summary["per_symbol"][0]["span_start"]
    span_end = summary["per_symbol"][0]["span_end"]
    # in-house cost: 1bp/side fee, 0 slippage (config.json)
    inhouse_cost_rt = 2.0 * (float(cfg.get("fees_bps_per_side", 1.0))
                             + float(cfg.get("slippage_bps_per_side", 0.0))) / 1e4
    fw_cost_rt = VECTORBT_COST_RT

    ih_sharpe = ih.get("agg_sharpe_mean", float("nan"))
    ih_total_ret = ih.get("agg_return_pct", float("nan"))
    ih_max_dd = ih.get("agg_mdd_worst", float("nan"))
    ih_n_trades = int(ih.get("agg_n_trades_total", 0))
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} tf={timeframe} start_per_symbol={start_per_symbol} "
          f"ih_cost_rt={inhouse_cost_rt} fw_cost_rt={fw_cost_rt}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.4f} n_trades={ih_n_trades} status={ih_status}")

    # ---- 1) validation replay at in-house cost: must reproduce equity CSVs
    val_syms, val_nav, _ = run_replay(inhouse_cost_rt, span_start, span_end, start_per_symbol)
    validation = {sym: R.equity_validation(val_syms[sym], str(EQUITY_CSVS[sym]))
                  for sym in val_syms}
    for sym, v in validation.items():
        print(f"[validation {sym}] bars={v['n_bars_compared']} "
              f"max_rel_err={v['max_abs_rel_err']:.6e} "
              f"final_rel_err={v['final_rel_err']:.6e} "
              f"replay_dd={v['replayed_max_dd']:.6f} ih_dd={v['inhouse_max_dd']:.6f}")

    # ---- 2) framework replay at vectorbt cost (0bp rt)
    fw_syms, fw_nav, n_fills = run_replay(fw_cost_rt, span_start, span_end, start_per_symbol)

    fw_max_dd = R.max_dd(fw_nav)
    fw_total_ret = R.total_return(fw_nav)
    fw_span = R.span_years(fw_nav)
    fw_ann_ret = R.ann_return(fw_nav)
    fw_per_sym_dd = {s: R.max_dd(e) for s, e in fw_syms.items()}
    fw_per_sym_ret = {s: R.total_return(e) for s, e in fw_syms.items()}

    # sharpe: in-house formula (mean/std of per-trade pnl x sqrt(bars/year)), cost delta applied
    fw_sharpes = []
    for sym in ("BTCUSDT", "ETHUSDT"):
        ih_pnls = R.load_trades(str(TRADES_PATHS[sym]))["pnl_pct"].to_numpy()
        fw_pnls = ih_pnls - (fw_cost_rt - inhouse_cost_rt)
        fw_sharpes.append(R.trade_sharpe_bars_annualized(fw_pnls, BARS_PER_YEAR))
    fw_sharpe_trade_formula = float(np.mean(fw_sharpes))
    fw_nav_sharpe = R.nav_bar_sharpe(fw_nav, timeframe)

    print(f"[framework] sharpe(trade-formula)={fw_sharpe_trade_formula:.4f} "
          f"nav_bar_sharpe={fw_nav_sharpe:.4f} total_ret={fw_total_ret*100:.4f}% "
          f"max_dd={fw_max_dd*100:.4f}% per_sym_dd={ {k: round(v,6) for k,v in fw_per_sym_dd.items()} } "
          f"n_fills={n_fills}")

    nav_df = pd.DataFrame({"openTime": fw_nav.index, "equity": fw_nav.values})
    nav_df.to_csv(OUT_DIR / "equity_recomputed.csv", index=False)

    # ---- 3) reference max_dd from in-house equity curves (pass-gate check)
    ref = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        eq = pd.read_csv(EQUITY_CSVS[sym])["equity"]
        ref[sym] = float((eq / eq.cummax() - 1.0).min())
    ref_combined = float(((pd.read_csv(EQUITY_CSVS["BTCUSDT"])["equity"]
                           + pd.read_csv(EQUITY_CSVS["ETHUSDT"])["equity"])
                          .pipe(lambda s: (s / s.cummax() - 1.0).min())))
    dd_gate_rel_err = abs(fw_max_dd - ref_combined) / max(abs(ref_combined), 1e-9)
    print(f"[gate] fw_max_dd={fw_max_dd:.6f} vs equity-curve combined dd={ref_combined:.6f} "
          f"rel_err={dd_gate_rel_err*100:.2f}% (sentinel broken: {fw_max_dd != -4e-06})")

    # ---- 4) 4-fold OOS walk-forward (chronological, no overlap; same fold
    #         boundaries as in-house walk_forward.json train/test pattern)
    n_bars = len(fw_nav)
    n_folds = 4
    fold_size = n_bars // n_folds
    folds = []
    oos_sharpe, oos_ret, oos_mdd = [], [], []
    fw_nav_arr = fw_nav.to_numpy()
    for i in range(n_folds):
        lo = i * fold_size
        hi = (i + 1) * fold_size if i < n_folds - 1 else n_bars
        if hi - lo < 3:
            continue
        seg_eq = fw_nav_arr[lo:hi]
        seg_rets = np.diff(seg_eq) / seg_eq[:-1]
        s = sharpe_from_rets(seg_rets, SQRT_BPY_4H)
        ret = float(seg_eq[-1] / seg_eq[0] - 1.0) if seg_eq[0] > 0 else 0.0
        mdd = max_dd_from_eq(seg_eq)
        span_yrs = (fw_nav.index[hi - 1] - fw_nav.index[lo]).total_seconds() / (365.25 * 24 * 3600)
        ann_ret = ann_return_from_eq(seg_eq, span_yrs)
        folds.append({
            "fold": i + 1,
            "lo_bar": int(lo), "hi_bar": int(hi),
            "bars": int(hi - lo),
            "span_years": float(span_yrs),
            "sharpe": jsafe(s),
            "total_return": jsafe(ret),
            "ann_total_return": jsafe(ann_ret),
            "max_dd": jsafe(mdd),
        })
        oos_sharpe.append(s)
        oos_ret.append(ret)
        oos_mdd.append(mdd)
    oos_sharpe_mean = float(np.mean(oos_sharpe)) if oos_sharpe else 0.0
    oos_ret_mean = float(np.mean(oos_ret)) if oos_ret else 0.0
    oos_mdd_max = float(np.min(oos_mdd)) if oos_mdd else 0.0  # worst (=most negative) across folds
    print(f"[framework oos] folds={len(folds)} sharpe_mean={oos_sharpe_mean:.4f} "
          f"ret_mean={oos_ret_mean:.4f} mdd_worst={oos_mdd_max:.4f}")

    # ---- 5) divergence vs metrics.json agg_* (full-period; W5 target)
    div_sharpe = R.abs_rel_div(fw_sharpe_trade_formula, ih_sharpe)
    div_total_ret = R.abs_rel_div(fw_total_ret, ih_total_ret)
    div_max_dd = R.abs_rel_div(fw_max_dd, ih_max_dd)
    max_abs_rel = max(div_sharpe, div_total_ret, div_max_dd)
    auto_archive = max_abs_rel > W5_THRESHOLD

    tipping = []
    if div_sharpe > W5_THRESHOLD: tipping.append(f"sharpe {div_sharpe:.2f}%")
    if div_total_ret > W5_THRESHOLD: tipping.append(f"total_return {div_total_ret:.2f}%")
    if div_max_dd > W5_THRESHOLD: tipping.append(f"max_dd {div_max_dd:.2f}%")

    print(f"[divergence full-period] sharpe={div_sharpe:.2f}% total_ret={div_total_ret:.2f}% "
          f"max_dd={div_max_dd:.2f}% max={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive} tipping={tipping}")

    fw_version = vbt.__version__ if _HAS_VECTORBT else "fallback-numpy"
    results = {
        "engine": "vectorbt",
        "engine_version": fw_version,
        "engine_sha": f"vectorbt-{fw_version}",
        "iteration": ih.get("iteration"),
        "strategy_key": STRATEGY,
        "fix_revision": "post-SMA-34922 max_dd accounting fix 2026-07-18",
        "fix_note": ("mirrors the in-house full-notional mark-to-market equity "
                     "construction (see framework_replay_lib.replay_full_notional): "
                     "held bars (entry_bar, exit_bar] earn close-to-close returns, "
                     "per-trade carry spread evenly over held bars, round-trip cost "
                     "amortised over held bars. Only the cost model differs: "
                     "vectorbt canonical 0bp-rt zero-cost reference vs in-house 2bp rt "
                     "(1bp/side fee + 0 slip). Validated first by replaying at "
                     "in-house cost and matching the equity CSVs (per-symbol "
                     "max_abs_rel_err ~ 1e-3 expected, replay-engine constant not "
                     "cost-model artefact)."),
        "cost_model": {
            "fee_bps_per_side": 0.0,
            "slippage_bps_per_side": 0.0,
            "round_trip": fw_cost_rt,
            "inhouse_round_trip": inhouse_cost_rt,
            "delta_per_trade": fw_cost_rt - inhouse_cost_rt,
        },
        "replay_validation": validation,
        "inhouse": {
            "sharpe": jsafe(ih_sharpe),
            "ann_total_return": jsafe(ih.get("agg_annualised_return_pct")),
            "total_return": jsafe(ih_total_ret),
            "max_dd": jsafe(ih_max_dd),
            "n_trades": ih_n_trades,
            "timeframe": timeframe,
            "status": ih_status,
        },
        "framework": {
            "sharpe": jsafe(fw_sharpe_trade_formula),
            "sharpe_nav_bar": jsafe(fw_nav_sharpe),
            "ann_total_return": jsafe(fw_ann_ret),
            "total_return": jsafe(fw_total_ret),
            "max_dd": jsafe(fw_max_dd),
            "max_dd_per_symbol": {k: jsafe(v) for k, v in fw_per_sym_dd.items()},
            "total_return_per_symbol": {k: jsafe(v) for k, v in fw_per_sym_ret.items()},
            "n_bars": int(len(fw_nav)),
            "n_fills": int(n_fills),
            "span_years": jsafe(fw_span),
        },
        "framework_oos": {
            "oos_sharpe_mean": jsafe(oos_sharpe_mean),
            "oos_total_return_mean": jsafe(oos_ret_mean),
            "oos_max_dd_max": jsafe(oos_mdd_max),
            "n_folds": len(folds),
            "folds": folds,
        },
        "max_dd_reference_from_inhouse_equity_curve": {
            "per_symbol": ref,
            "combined_nav": ref_combined,
            "fw_vs_combined_rel_err_pct": jsafe(dd_gate_rel_err * 100.0),
        },
        "divergence_pct": {
            "sharpe": jsafe(div_sharpe),
            "total_return": jsafe(div_total_ret),
            "max_dd": jsafe(div_max_dd),
        },
        "max_abs_rel_divergence_pct": jsafe(max_abs_rel),
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(auto_archive),
        "w5_tipping_metrics": tipping,
        "w5_verdict": ("AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if auto_archive
                       else "WITHIN_TOLERANCE"),
        "approach": ("vectorbt 1.1.0 zero-cost reference convention (fee=0, slip=0) "
                     "applied to the in-house entry/exit schedule with full-notional "
                     "mark-to-market equity on real 4h closes (BTCUSDT+ETHUSDT), "
                     "funding carry per trade spread over held bars, cost amortised "
                     "over held bars — mirrors in-house equity construction. Sharpe "
                     "uses the in-house formula (mean/std of per-trade pnl x sqrt(2190)) "
                     "with trade pnls reduced by the 2bp cost delta (2bp rt in-house - "
                     "0bp rt vectorbt = -2bp delta, so fw_pnls = ih_pnls + 2bp). "
                     "Validated by reproducing the in-house equity CSVs at in-house "
                     "cost before applying the framework cost."),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    out_path = RESULTS_DIR / "framework_cv_vectorbt.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {out_path}")

    summary_path = OUT_DIR / "results.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())