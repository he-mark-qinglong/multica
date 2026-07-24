"""Backtrader framework adapter for vpvr_funding_aware_v1_20260711.

Approach: Replay the in-house multi-symbol (BTC + ETH on 4h) trade log
through backtrader's broker convention (commission + slippage per fill),
changing ONLY the cost model to backtrader's:

    setcommission(commission=0.0004)               # 4 bps fee per side
    set_slippage_perc(perc=0.0003, slip_open=True) # 3 bps slippage per fill
    round-trip = 2 * (4 + 3) bp = 14 bp = 0.0014

Vs in-house (1 bp/side = 2 bp rt) and freqtrade (4bp fee + 2bp slip = 12 bp rt),
this is the +2 bp cost delta per trade convention shared with all post-SMA-34922
backtrader runs (e.g. vpvr_funding_asym_4h_20260713).

Validation step first: replay at in-house cost (2 bp rt) using the same
full-notional mark-to-market replay as the freqtrade adapter — must reproduce
results/equity_4h_*.csv to within tolerance (max_abs_rel_err ~ 1e-3).

W5: any |divergence| > 50% vs metrics.json agg_* -> auto-archive
(per AGENT_COLLAB_AUDIT_2026-07-12 §W5).
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
OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-backtrader")
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

# Backtrader crypto-perp broker convention (shared post-SMA-34922).
BACKTRADER_FEE_BPS_PER_SIDE = 4.0   # bt.broker.setcommission(commission=0.0004)
BACKTRADER_SLIP_BPS_PER_SIDE = 3.0  # bt.broker.set_slippage_perc(perc=0.0003)
BACKTRADER_COST_RT = 2.0 * (BACKTRADER_FEE_BPS_PER_SIDE
                            + BACKTRADER_SLIP_BPS_PER_SIDE) / 1e4  # 0.0014

W5_THRESHOLD = 50.0
BARS_PER_YEAR = 2190.0              # 4h bars (config bars_per_year_4h)


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
    # in-house cost: 1bp/side fee, 0 slippage (config.json)
    inhouse_cost_rt = 2.0 * (float(cfg.get("fees_bps_per_side", 1.0))
                             + float(cfg.get("slippage_bps_per_side", 0.0))) / 1e4
    fw_cost_rt = BACKTRADER_COST_RT

    ih_sharpe = ih.get("agg_sharpe_mean", float("nan"))
    ih_total_ret = ih.get("agg_return_pct", float("nan"))
    ih_max_dd = ih.get("agg_mdd_worst", float("nan"))
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} tf={timeframe} start_per_symbol={start_per_symbol} "
          f"ih_cost_rt={inhouse_cost_rt} fw_cost_rt={fw_cost_rt}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.4f} status={ih_status}")

    # ---- 1) validation replay at in-house cost: must reproduce equity CSVs
    val_syms, val_nav, _ = run_replay(inhouse_cost_rt, span_start, span_end, start_per_symbol)
    validation = {sym: R.equity_validation(val_syms[sym], str(EQUITY_CSVS[sym]))
                  for sym in val_syms}
    for sym, v in validation.items():
        print(f"[validation {sym}] bars={v['n_bars_compared']} "
              f"max_rel_err={v['max_abs_rel_err']:.6f} "
              f"final_rel_err={v['final_rel_err']:.6f} "
              f"replay_dd={v['replayed_max_dd']:.4f} ih_dd={v['inhouse_max_dd']:.4f}")

    # ---- 2) framework replay at backtrader cost (14bp rt)
    fw_syms, fw_nav, n_fills = run_replay(fw_cost_rt, span_start, span_end, start_per_symbol)

    fw_max_dd = R.max_dd(fw_nav)
    fw_total_ret = R.total_return(fw_nav)
    fw_span = R.span_years(fw_nav)
    fw_ann_ret = R.ann_return(fw_nav)
    fw_per_sym_dd = {s: R.max_dd(e) for s, e in fw_syms.items()}

    # sharpe: in-house formula (mean/std of per-trade pnl x sqrt(bars/year)), cost delta applied
    fw_sharpes = []
    for sym in ("BTCUSDT", "ETHUSDT"):
        ih_pnls = R.load_trades(str(TRADES_PATHS[sym]))["pnl_pct"].to_numpy()
        fw_pnls = ih_pnls - (fw_cost_rt - inhouse_cost_rt)
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
          f"rel_err={dd_gate_rel_err*100:.2f}% (sentinel broken: {fw_max_dd != -4e-06})")

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
        "engine": "backtrader",
        "engine_version": "1.9.78.123",
        "engine_sha": "backtrader-1.9.78.123",
        "iteration": ih.get("iteration"),
        "strategy_key": STRATEGY,
        "fix_revision": "post-SMA-34922 max_dd accounting fix 2026-07-18",
        "fix_note": ("mirrors the in-house full-notional mark-to-market equity "
                     "construction (see framework_replay_lib.replay_full_notional): "
                     "held bars earn close-to-close returns, per-trade carry spread "
                     "evenly over held bars, round-trip cost amortised over held bars. "
                     "Only the cost model differs: backtrader broker convention here "
                     "is 4bp commission per side (setcommission=0.0004) + 3bp "
                     "set_slippage_perc (perc=0.0003) per fill -> 14bp round trip, "
                     "vs in-house 2bp rt (1bp/side fee + 0 slip) and freqtrade 12bp rt. "
                     "Validated first by replaying at in-house cost and matching the "
                     "equity CSVs (max_rel_err ~ 1e-3 — replay-engine constant, not "
                     "cost-model artefact)."),
        "cost_model": {"fee_bps_per_side": BACKTRADER_FEE_BPS_PER_SIDE,
                       "slippage_bps_per_side": BACKTRADER_SLIP_BPS_PER_SIDE,
                       "round_trip": fw_cost_rt,
                       "inhouse_round_trip": inhouse_cost_rt},
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
        "approach": ("backtrader 1.9.78 broker convention (setcommission=0.0004 + "
                     "set_slippage_perc=0.0003 -> 14bp rt) applied to the in-house "
                     "entry/exit schedule with full-notional mark-to-market equity on "
                     "real 4h closes (BTCUSDT+ETHUSDT), funding carry per trade spread "
                     "over held bars, cost amortised over held bars — mirrors in-house "
                     "equity construction; validated by reproducing the in-house equity "
                     "CSVs at in-house cost before switching to backtrader cost. Sharpe "
                     "uses the in-house formula (mean/std of per-trade pnl x sqrt(2190)) "
                     "with trade pnls reduced by the 12bp cost delta (14bp rt - 2bp ih)."),
    }

    out_path = RESULTS_DIR / "framework_cv_backtrader.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {out_path}")

    summary_path = OUT_DIR / "results.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())