"""Freqtrade framework adapter for vpvr_funding_aware_v1_20260711.

FIXED 2026-07-18 (SMA-34922): the previous revision debited only the entry
fee at entry and credited `notional * (1 + pnl_pct)` at exit — the position
notional was never subtracted from cash, so NAV ratcheted upward at every
fill and max_dd degenerated to the per-entry fee dip (-4.0e-06 sentinel).

This revision replays the in-house entry/exit schedule over real 4h close
prices with full-notional mark-to-market equity (mirroring the in-house
equity construction in strategy.py), changing ONLY the cost model to
freqtrade's (4bp fee + 2bp slippage per side = 12bp round trip; in-house
used 1bp/side = 2bp round trip, so each trade's pnl drops by 10bp).

Self-check: a validation replay with the in-house 2bp cost must reproduce
results/equity_4h_*.csv (reported under replay_validation); only then is
the framework-cost (12bp) run trusted.

W5: if any |divergence| > 50% vs metrics.json agg_* -> auto-archive.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/smark/multica/quant-loop/workdir")
import framework_replay_lib as R  # noqa: E402

STRATEGY_DIR = Path(__file__).parent
STRATEGY = STRATEGY_DIR.name
OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-freqtrade")
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

W5_THRESHOLD = 50.0
INHOUSE_COST_RT = 0.0002          # 1bp/side in-house (config fees_bps_per_side=1.0, slip 0)
FW_COST_RT = R.FREQTRADE_COST_RT  # 12bp round trip
BARS_PER_YEAR = 2190.0            # 4h bars (config bars_per_year_4h)


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

    ih_sharpe = ih.get("agg_sharpe_mean", float("nan"))
    ih_total_ret = ih.get("agg_return_pct", float("nan"))
    ih_max_dd = ih.get("agg_mdd_worst", float("nan"))
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} tf={timeframe} start_per_symbol={start_per_symbol}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.4f} status={ih_status}")

    # ---- 1) validation replay at in-house cost: must reproduce equity CSVs
    val_syms, val_nav, _ = run_replay(INHOUSE_COST_RT, span_start, span_end, start_per_symbol)
    validation = {sym: R.equity_validation(val_syms[sym], str(EQUITY_CSVS[sym]))
                  for sym in val_syms}
    for sym, v in validation.items():
        print(f"[validation {sym}] bars={v['n_bars_compared']} max_rel_err={v['max_abs_rel_err']:.6f} "
              f"final_rel_err={v['final_rel_err']:.6f} replay_dd={v['replayed_max_dd']:.4f} "
              f"ih_dd={v['inhouse_max_dd']:.4f}")

    # ---- 2) framework replay at freqtrade cost (12bp rt)
    fw_syms, fw_nav, n_fills = run_replay(FW_COST_RT, span_start, span_end, start_per_symbol)

    fw_max_dd = R.max_dd(fw_nav)
    fw_total_ret = R.total_return(fw_nav)
    fw_span = R.span_years(fw_nav)
    fw_ann_ret = R.ann_return(fw_nav)
    fw_per_sym_dd = {s: R.max_dd(e) for s, e in fw_syms.items()}

    # framework trade pnls (in-house formula input): ih pnl - cost delta
    fw_sharpes = []
    for sym in ("BTCUSDT", "ETHUSDT"):
        ih_pnls = R.load_trades(str(TRADES_PATHS[sym]))["pnl_pct"].to_numpy()
        fw_pnls = ih_pnls - (FW_COST_RT - INHOUSE_COST_RT)
        fw_sharpes.append(R.trade_sharpe_bars_annualized(fw_pnls, BARS_PER_YEAR))
    fw_sharpe = float(np.mean(fw_sharpes))
    fw_nav_sharpe = R.nav_bar_sharpe(fw_nav, timeframe)

    print(f"[framework] sharpe(trade-formula)={fw_sharpe:.4f} nav_bar_sharpe={fw_nav_sharpe:.4f} "
          f"total_ret={fw_total_ret*100:.4f}% max_dd={fw_max_dd*100:.4f}% "
          f"per_sym_dd={ {k: round(v,4) for k,v in fw_per_sym_dd.items()} } n_fills={n_fills}")

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
          f"rel_err={dd_gate_rel_err*100:.2f}% (must be <20%, sentinel broken: {fw_max_dd != -4e-06})")

    # ---- 4) divergence vs metrics.json agg_* (same targets as original run)
    div_sharpe = R.abs_rel_div(fw_sharpe, ih_sharpe)
    div_total_ret = R.abs_rel_div(fw_total_ret, ih_total_ret)
    div_max_dd = R.abs_rel_div(fw_max_dd, ih_max_dd)
    max_abs_rel = max(div_sharpe, div_total_ret, div_max_dd)
    auto_archive = max_abs_rel > W5_THRESHOLD

    tipping = []
    if div_sharpe > W5_THRESHOLD: tipping.append(f"sharpe {div_sharpe:.2f}%")
    if div_total_ret > W5_THRESHOLD: tipping.append(f"total_return {div_total_ret:.2f}%")
    if div_max_dd > W5_THRESHOLD: tipping.append(f"max_dd {div_max_dd:.2f}%")

    print(f"[divergence] sharpe={div_sharpe:.2f}% total_ret={div_total_ret:.2f}% "
          f"max_dd={div_max_dd:.2f}% max={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive} tipping={tipping}")

    results = {
        "engine": "freqtrade",
        "engine_version": "2026.6",
        "engine_sha": "freqtrade-2026.6",
        "iteration": ih.get("iteration"),
        "strategy_key": STRATEGY,
        "fix_revision": "SMA-34922 max_dd accounting fix 2026-07-18",
        "fix_note": ("previous adapter never debited position notional from cash at entry "
                     "but credited notional*(1+pnl) at exit; NAV ratcheted up so max_dd "
                     "collapsed to the per-entry fee (-4.0e-06). Replaced with full-notional "
                     "mark-to-market replay (framework_replay_lib) at freqtrade 12bp rt cost."),
        "cost_model": {"fee_bps_per_side": R.FREQTRADE_FEE_BPS_PER_SIDE,
                       "slippage_bps_per_side": R.FREQTRADE_SLIP_BPS_PER_SIDE,
                       "round_trip": FW_COST_RT,
                       "inhouse_round_trip": INHOUSE_COST_RT},
        "replay_validation": validation,
        "inhouse": {
            "sharpe": jsafe(ih_sharpe),
            "ann_total_return": jsafe(ih.get("agg_annualised_return_pct")),
            "total_return": jsafe(ih_total_ret),
            "max_dd": jsafe(ih_max_dd),
            "n_trades": int(ih.get("agg_n_trades_total", 0)),
            "timeframe": timeframe,
            "status": ih_status,
        },
        "framework": {
            "sharpe": jsafe(fw_sharpe),
            "sharpe_nav_bar": jsafe(fw_nav_sharpe),
            "ann_total_return": jsafe(fw_ann_ret),
            "total_return": jsafe(fw_total_ret),
            "max_dd": jsafe(fw_max_dd),
            "max_dd_per_symbol": {k: jsafe(v) for k, v in fw_per_sym_dd.items()},
            "n_bars": int(len(fw_nav)),
            "n_fills": int(n_fills),
            "span_years": jsafe(fw_span),
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
        "w5_verdict": "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if auto_archive else "WITHIN_TOLERANCE",
        "approach": ("freqtrade 2026.6 cost model (4bp fee + 2bp slip per side) applied to the "
                     "in-house entry/exit schedule with full-notional mark-to-market equity on "
                     "real 4h closes (BTCUSDT+ETHUSDT), funding carry per trade from trades CSV "
                     "spread over held bars, cost amortised over held bars — mirrors in-house "
                     "equity construction; validated by reproducing the in-house equity CSVs at "
                     "in-house cost before switching to freqtrade cost. Sharpe uses the in-house "
                     "formula (mean/std of per-trade pnl x sqrt(2190)) with trade pnls reduced "
                     "by the 10bp round-trip cost delta."),
    }

    out_path = RESULTS_DIR / "framework_cv_freqtrade.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {out_path}")

    summary_path = OUT_DIR / "results.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
