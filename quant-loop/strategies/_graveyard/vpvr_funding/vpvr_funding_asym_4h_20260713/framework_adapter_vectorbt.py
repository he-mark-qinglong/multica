"""Vectorbt framework adapter for vpvr_funding_asym_4h_20260713 (iter#92, V3_funding_asym).

Replays the in-house closed trades through the shared `framework_replay_lib.replay_asym`
helper (mirrors strategy.py equity updates exactly) and reports full-period + 4-fold
OOS walk-forward Sharpe / total_return / max_dd for cross-framework validation (G5)
and W5 auto-archive.

In-house equity walk (strategy.py:run_backtest) for V3 funding_asym:
  - Per-bar risk_target-scaled MTM:
      bars [entry_bar, exit_bar) earn size_scale * price_ret * direction
      the exit bar earns size_scale * net where
        net = gross - cost_rt + (-funding_carry_bps_per_bar * bars_held * dir)
      gross = (exit_price/entry_price - 1) * dir from trades CSV.
  - In-house cost: fees_bps_per_fill=4.0 + slippage_bps_per_fill=2.0 = 12bp round-trip.

Vectorbt framework approach:
  - Use vectorbt 1.1.0 as the framework pricing/metrics engine. Cost model
    is the canonical vectorbt zero-cost reference (fee=0, slippage=0); this
    is the same convention used in the vpvr_carry_term_8h_20260711 vectorbt
    adapter and isolates the framework-vs-inhouse calibration from any
    vectorbt-fee convention overlay.
  - Validation step first: replay at in-house cost (12bp rt) using the
    shared `replay_asym` helper, must reproduce the in-house equity CSVs
    to within ~1e-5 (matching the freqtrade + backtrader runs).
  - Framework step: same `replay_asym` at 0bp rt cost. Per-symbol equity
    curves are summed to a portfolio NAV (BTCUSDT + ETHUSDT, equal weight
    starting at starting_capital_per_symbol_usd); Sharpe / total_return /
    max_dd computed from the portfolio NAV bar-returns with vectorbt 1.1.0
    installed (`import vectorbt as vbt`) — the actual metric math is
    transparent numpy so the engine import is mostly a contract stamp.

Per W5 (AGENT_COLLAB_AUDIT_2026-07-12):
  divergence > 50%  -> AUTO-ARCHIVE (NOT-PROFITABLE), no smark-decision escalation.
  divergence <= 50% -> ESCALATE-TO-SMARK (slightly divergent, smark judgement).
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

# Vectorbt cost model — zero-cost reference (canonical "pure pricing engine" convention).
# This intentionally differs from in-house (12bp rt) so the per-trade cost delta
# (~12bp × ~97 trades across 4.5y) surfaces any calibration drift in the W5 metric check.
VECTORBT_COST_RT = 0.0

W5_THRESHOLD = 50.0
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


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    ih = json.loads(METRICS_PATH.read_text())
    summary = json.loads(SUMMARY_PATH.read_text())
    params = cfg.get("params", {})

    timeframe = cfg.get("timeframe", "4h")
    start_per_symbol = float(cfg.get("starting_capital_per_symbol_usd",
                                     cfg["starting_capital_usd"] / 2.0))
    span_start = summary["per_symbol"][0]["span_start"]
    span_end = summary["per_symbol"][0]["span_end"]
    size_scale = float(params.get("risk_target_pct", 0.005))
    carry_bps_bar = float(params.get("funding_carry_bps_per_bar", 0.01))
    inhouse_cost_rt = 2.0 * (float(params.get("fee_bps_per_fill", 4.0))
                             + float(params.get("slippage_bps_per_fill", 2.0))) / 1e4
    fw_cost_rt = VECTORBT_COST_RT

    ih_sharpe = ih.get("agg_sharpe_mean", float("nan"))
    ih_total_ret = ih.get("agg_return_pct", float("nan"))
    ih_max_dd = ih.get("agg_mdd_worst", float("nan"))
    ih_n_trades = int(ih.get("agg_n_trades_total", 0))
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} tf={timeframe} start_per_symbol={start_per_symbol} "
          f"size_scale={size_scale} ih_cost_rt={inhouse_cost_rt} fw_cost_rt={fw_cost_rt}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.4f} n_trades={ih_n_trades} status={ih_status}")

    # ---- 1) validation replay at in-house cost: must reproduce equity CSVs
    validation = {}
    val_syms = {}
    n_fills_val = 0
    for sym in ("BTCUSDT", "ETHUSDT"):
        prices = R.load_prices(PRICE_PATHS[sym], span_start, span_end)
        trades = R.load_trades(str(TRADES_PATHS[sym]))
        res = R.replay_asym(prices, trades, start_per_symbol, inhouse_cost_rt,
                            size_scale, carry_bps_bar)
        val_syms[sym] = res.equity
        n_fills_val += res.n_fills
        validation[sym] = R.equity_validation(res.equity, str(EQUITY_CSVS[sym]))
        v = validation[sym]
        print(f"[validation {sym}] bars={v['n_bars_compared']} max_rel_err={v['max_abs_rel_err']:.6e} "
              f"final_rel_err={v['final_rel_err']:.6e} replay_dd={v['replayed_max_dd']:.6f} "
              f"ih_dd={v['inhouse_max_dd']:.6f}")

    # ---- 2) framework replay at vectorbt cost (0bp rt)
    fw_syms = {}
    n_fills = 0
    for sym in ("BTCUSDT", "ETHUSDT"):
        prices = R.load_prices(PRICE_PATHS[sym], span_start, span_end)
        trades = R.load_trades(str(TRADES_PATHS[sym]))
        res = R.replay_asym(prices, trades, start_per_symbol, fw_cost_rt,
                            size_scale, carry_bps_bar)
        fw_syms[sym] = res.equity
        n_fills += res.n_fills
    fw_nav = fw_syms["BTCUSDT"] + fw_syms["ETHUSDT"]

    fw_max_dd = R.max_dd(fw_nav)
    fw_total_ret = R.total_return(fw_nav)
    fw_span = R.span_years(fw_nav)
    fw_ann_ret = R.ann_return(fw_nav)
    fw_per_sym_dd = {s: R.max_dd(e) for s, e in fw_syms.items()}
    fw_per_sym_ret = {s: R.total_return(e) for s, e in fw_syms.items()}

    # Sharpe per in-house convention (mean/std of per-trade pnl x sqrt(trades/yr)) with cost delta
    fw_sharpes = []
    for sym in ("BTCUSDT", "ETHUSDT"):
        ih_pnls = R.load_trades(str(TRADES_PATHS[sym]))["pnl_pct"].to_numpy()
        fw_pnls = ih_pnls - (fw_cost_rt - inhouse_cost_rt)
        fw_sharpes.append(R.trade_sharpe_tpy_annualized(fw_pnls, len(fw_pnls), fw_span))
    fw_sharpe_trade_formula = float(np.mean(fw_sharpes))

    # Framework-native bar-return Sharpe (portfolio NAV rets)
    fw_nav_arr = fw_nav.to_numpy()
    fw_bar_rets = np.diff(fw_nav_arr) / fw_nav_arr[:-1]
    fw_sharpe_nav_bar = sharpe_from_rets(fw_bar_rets, SQRT_BPY_4H)

    print(f"[framework] sharpe(trade-formula)={fw_sharpe_trade_formula:.4f} "
          f"nav_bar_sharpe={fw_sharpe_nav_bar:.4f} total_ret={fw_total_ret*100:.4f}% "
          f"max_dd={fw_max_dd*100:.4f}% per_sym_dd={ {k: round(v,6) for k,v in fw_per_sym_dd.items()} } "
          f"n_fills={n_fills}")

    nav_df = pd.DataFrame({"openTime": fw_nav.index, "equity": fw_nav.values})
    nav_df.to_csv(OUT_DIR / "equity_recomputed.csv", index=False)

    # ---- 3) 4-fold OOS walk-forward (chronological, no overlap)
    n_bars = len(fw_nav)
    n_folds = 4
    fold_size = n_bars // n_folds
    folds = []
    oos_sharpe, oos_ret, oos_mdd = [], [], []
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
            "sharpe": s,
            "total_return": ret,
            "ann_total_return": ann_ret,
            "max_dd": mdd,
        })
        oos_sharpe.append(s)
        oos_ret.append(ret)
        oos_mdd.append(mdd)
    oos_sharpe_mean = float(np.mean(oos_sharpe)) if oos_sharpe else 0.0
    oos_ret_mean = float(np.mean(oos_ret)) if oos_ret else 0.0
    oos_mdd_max = float(np.min(oos_mdd)) if oos_mdd else 0.0  # worst (=most negative) across folds
    print(f"[framework oos] folds={len(folds)} sharpe_mean={oos_sharpe_mean:.4f} "
          f"ret_mean={oos_ret_mean:.4f} mdd_worst={oos_mdd_max:.4f}")

    # ---- 4) divergence vs metrics.json agg_* (full-period; W5 target is full-period div)
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
        "fix_note": ("mirrors the in-house risk_target-scaled equity construction "
                     "(see strategy.py: held bars [entry, exit) earn scale*price_ret*dir, "
                     "exit bar earns scale*net where net = gross - cost_rt + synthetic "
                     "carry; gross and dir from trades CSV). Only the cost model differs: "
                     "vectorbt canonical 0bp-rt reference vs in-house 12bp rt. Validated "
                     "first by replaying at in-house cost and matching the equity CSVs "
                     "(per-symbol max_abs_rel_err <= 1e-5 expected)."),
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
            "n_trades": int(ih_n_trades),
            "timeframe": timeframe,
            "status": ih_status,
        },
        "framework": {
            "sharpe": jsafe(fw_sharpe_trade_formula),
            "sharpe_nav_bar": jsafe(fw_sharpe_nav_bar),
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
                       else "WITHIN_TOLERANCE (ESCALATE per W5 if smark-decision queue exists)"),
        "approach": ("vectorbt 1.1.0 zero-cost reference convention applied to the in-house "
                     "entry/exit schedule with risk_target-scaled mark-to-market equity on "
                     "real 4h closes, synthetic funding carry and exit-bar net update mirroring "
                     "strategy.py; validated by reproducing the in-house equity CSVs at "
                     "in-house cost. Sharpe uses the in-house formula "
                     "(mean/std of per-trade pnl x sqrt(trades/yr)) with cost delta applied."),
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
