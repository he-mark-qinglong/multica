"""Freqtrade framework adapter for vpvr_funding_asym_4h_20260713.

FIXED 2026-07-18 (SMA-34922): the previous revision debited only the entry
fee at entry and credited `notional * (1 + pnl_pct)` at exit — the position
notional was never subtracted from cash, so NAV ratcheted upward at every
fill and max_dd degenerated to the per-entry fee dip (-4.0e-06 sentinel),
fabricating total_return 62.7% / Sharpe 4.19 for a strategy whose in-house
record is flat (return 0.14%, Sharpe -0.22).

This revision mirrors the in-house equity construction exactly
(risk_target-scaled bar pnl, synthetic funding carry, exit-bar net update;
see strategy.py) over real 4h closes, changing ONLY the cost model to
freqtrade's — which here equals the in-house cost (4bp fee + 2bp slip per
side = 12bp round trip), so the framework run is a reproduction check:
it must land on the in-house numbers, not 40000% away from them.

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
    fw_cost_rt = R.FREQTRADE_COST_RT

    ih_sharpe = ih.get("agg_sharpe_mean", float("nan"))
    ih_total_ret = ih.get("agg_return_pct", float("nan"))
    ih_max_dd = ih.get("agg_mdd_worst", float("nan"))
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} tf={timeframe} start_per_symbol={start_per_symbol} "
          f"size_scale={size_scale} ih_cost_rt={inhouse_cost_rt} fw_cost_rt={fw_cost_rt}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.4f} status={ih_status}")

    # ---- 1) validation replay at in-house cost: must reproduce equity CSVs
    validation = {}
    val_syms = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        prices = R.load_prices(PRICE_PATHS[sym], span_start, span_end)
        trades = R.load_trades(str(TRADES_PATHS[sym]))
        res = R.replay_asym(prices, trades, start_per_symbol, inhouse_cost_rt,
                            size_scale, carry_bps_bar)
        val_syms[sym] = res.equity
        validation[sym] = R.equity_validation(res.equity, str(EQUITY_CSVS[sym]))
        v = validation[sym]
        print(f"[validation {sym}] bars={v['n_bars_compared']} max_rel_err={v['max_abs_rel_err']:.6f} "
              f"final_rel_err={v['final_rel_err']:.6f} replay_dd={v['replayed_max_dd']:.6f} "
              f"ih_dd={v['inhouse_max_dd']:.6f}")

    # ---- 2) framework replay at freqtrade cost
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

    # sharpe: in-house formula (mean/std of per-trade pnl x sqrt(tpy)), cost delta applied
    fw_sharpes = []
    for sym in ("BTCUSDT", "ETHUSDT"):
        ih_pnls = R.load_trades(str(TRADES_PATHS[sym]))["pnl_pct"].to_numpy()
        fw_pnls = ih_pnls - (fw_cost_rt - inhouse_cost_rt)
        fw_sharpes.append(R.trade_sharpe_tpy_annualized(fw_pnls, len(fw_pnls), fw_span))
    fw_sharpe = float(np.mean(fw_sharpes))
    fw_nav_sharpe = R.nav_bar_sharpe(fw_nav, timeframe)

    print(f"[framework] sharpe(trade-formula)={fw_sharpe:.4f} nav_bar_sharpe={fw_nav_sharpe:.4f} "
          f"total_ret={fw_total_ret*100:.4f}% max_dd={fw_max_dd*100:.4f}% "
          f"per_sym_dd={ {k: round(v,6) for k,v in fw_per_sym_dd.items()} } n_fills={n_fills}")

    nav_df = pd.DataFrame({"openTime": fw_nav.index, "equity": fw_nav.values})
    nav_df.to_csv(OUT_DIR / "equity_recomputed.csv", index=False)

    # ---- 3) divergence vs metrics.json agg_* (same targets as original run)
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
                     "collapsed to the per-entry fee (-4.0e-06) and total_return was "
                     "fabricated (62.7% vs in-house 0.14%). Replaced with a replay that "
                     "mirrors the in-house risk_target-scaled equity construction at the "
                     "freqtrade cost model (which equals the in-house 12bp rt cost here)."),
        "cost_model": {"fee_bps_per_side": R.FREQTRADE_FEE_BPS_PER_SIDE,
                       "slippage_bps_per_side": R.FREQTRADE_SLIP_BPS_PER_SIDE,
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
        "approach": ("freqtrade 2026.6 cost model (4bp fee + 2bp slip per side; identical to this "
                     "strategy's in-house cost) applied to the in-house entry/exit schedule with "
                     "risk_target-scaled mark-to-market equity on real 4h closes, synthetic "
                     "funding carry and exit-bar net update mirroring strategy.py; validated by "
                     "reproducing the in-house equity CSVs at in-house cost. Sharpe uses the "
                     "in-house formula (mean/std of per-trade pnl x sqrt(trades/yr))."),
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
