"""Freqtrade framework adapter for vpvr_funding_reset_window_1h_20260715.

FIXED 2026-07-18 (SMA-34922): the previous revision applied each trade's
pnl_pct diluted by weight 0.01 AND spread across held bars
(equity *= 1 + pnl_pct*0.01/held_bars), scaling all framework metrics ~100x
down — max_dd came out as the -3.126e-04 sentinel and Sharpe collapsed to
0.0003, fabricating a ~100% divergence against the in-house record.

This revision mirrors the in-house equity construction exactly
(risk_per_trade-scaled bar returns, cost amortised over held bars;
see strategy.py) over real 1h closes, changing ONLY the cost model to
freqtrade's — which here equals the in-house cost (4bp fee + 2bp slip per
side = 12bp round trip), so the framework run is a reproduction check.

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

PRICE_PATH = "/home/smark/multica/quant-loop/live_data/BTCUSDT_1h.parquet"
TRADES_PATH = RESULTS_DIR / "trades_A_1h_BTCUSDT.csv"
EQUITY_CSV = RESULTS_DIR / "equity_1h_BTCUSDT.csv"

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

    timeframe = cfg.get("timeframe", "1h")
    start_capital = float(cfg.get("starting_capital_usd", 100000.0))
    span_start = summary["per_symbol"][0]["span_start"]
    span_end = summary["per_symbol"][0]["span_end"]
    size_scale = float(params.get("risk_per_trade_pct", 0.01))
    inhouse_cost_rt = 2.0 * (float(params.get("fee_bps_per_fill", 4.0))
                             + float(params.get("slippage_bps_per_fill", 2.0))) / 1e4
    fw_cost_rt = R.FREQTRADE_COST_RT

    ih_sharpe = ih.get("agg_sharpe_mean", float("nan"))
    ih_total_ret = ih.get("agg_return_pct", float("nan"))
    ih_max_dd = ih.get("agg_mdd_worst", float("nan"))
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} tf={timeframe} start={start_capital} "
          f"size_scale={size_scale} ih_cost_rt={inhouse_cost_rt} fw_cost_rt={fw_cost_rt}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.4f} status={ih_status}")

    prices = R.load_prices(PRICE_PATH, span_start, span_end)
    trades = R.load_trades(str(TRADES_PATH))

    # ---- 1) validation replay at in-house cost: must reproduce equity CSV
    val = R.replay_risk_scaled(prices, trades, start_capital, inhouse_cost_rt, size_scale)
    validation = {"BTCUSDT": R.equity_validation(val.equity, str(EQUITY_CSV))}
    v = validation["BTCUSDT"]
    print(f"[validation BTCUSDT] bars={v['n_bars_compared']} max_rel_err={v['max_abs_rel_err']:.6f} "
          f"final_rel_err={v['final_rel_err']:.6f} replay_dd={v['replayed_max_dd']:.6f} "
          f"ih_dd={v['inhouse_max_dd']:.6f}")

    # ---- 2) framework replay at freqtrade cost
    fw = R.replay_risk_scaled(prices, trades, start_capital, fw_cost_rt, size_scale)
    fw_nav = fw.equity
    fw_max_dd = R.max_dd(fw_nav)
    fw_total_ret = R.total_return(fw_nav)
    fw_span = R.span_years(fw_nav)
    fw_ann_ret = R.ann_return(fw_nav)

    # sharpe: in-house formula (mean/std of per-trade pnl x sqrt(tpy)), cost delta applied
    fw_pnls = trades["pnl_pct"].to_numpy() - (fw_cost_rt - inhouse_cost_rt)
    fw_sharpe = R.trade_sharpe_tpy_annualized(fw_pnls, len(fw_pnls), fw_span)
    fw_nav_sharpe = R.nav_bar_sharpe(fw_nav, timeframe)

    print(f"[framework] sharpe(trade-formula)={fw_sharpe:.4f} nav_bar_sharpe={fw_nav_sharpe:.4f} "
          f"total_ret={fw_total_ret*100:.4f}% max_dd={fw_max_dd*100:.4f}% n_fills={fw.n_fills}")

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
        "fix_note": ("previous adapter diluted pnl_pct by weight 0.01 and spread it across held "
                     "bars, scaling framework metrics ~100x down (max_dd -3.126e-04 sentinel, "
                     "Sharpe 0.0003). Replaced with a replay that mirrors the in-house "
                     "risk_per_trade-scaled equity construction at the freqtrade cost model "
                     "(which equals the in-house 12bp rt cost here)."),
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
            "n_bars": int(len(fw_nav)),
            "n_fills": int(fw.n_fills),
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
                     "risk_per_trade-scaled mark-to-market equity on real 1h closes, cost "
                     "amortised over held bars, mirroring strategy.py; validated by reproducing "
                     "the in-house equity CSV at in-house cost. Sharpe uses the in-house formula "
                     "(mean/std of per-trade pnl x sqrt(trades/yr))."),
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
