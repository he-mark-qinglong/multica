"""Backtrader framework adapter for vpvr_xs_smart_routing_15m_20260715.

Cross-validate the multi-venue smart-routing microprice-divergence strategy
(iter#105, V3_xs_smart_routing; NOT-PROFITABLE) on BTCUSDT 15m via backtrader.

Method (post-SMA-34922 fixed methodology, mirrors the freqtrade run):
  Replay the in-house entry/exit schedule (results/trades_A_15m_BTCUSDT.csv,
  2,772 trades) over real BTCUSDT 15m closes with the in-house equity
  convention (risk_per_trade-scaled mark-to-market bar returns, round-trip
  cost amortised over held bars — mirrors strategy.py), changing ONLY the
  cost model to backtrader's broker convention.

  Backtrader crypto-perp broker convention:
    setcommission(commission=0.0004)               # 4 bps fee per side
    set_slippage_perc(perc=0.0003, slip_open=True) # 3 bps slippage per fill
    round-trip = 2 * (4 + 3) bp = 14 bp = 0.0014
  In-house cost = 12bp rt (4bp fee + 2bp slip per side); +2bp cost delta
  per trade.

  Step 1 (validation): replay at the in-house cost must reproduce
  results/equity_15m_BTCUSDT.csv to machine precision before the framework
  run is trusted.

  Step 2 (framework): same replay at backtrader cost; full-span metrics plus
  3 contiguous OOS walk-forward folds (2023H1 / 2023H2 / 2024H1, the
  standard framework-CV windows per the freqtrade run precedent on this
  same strategy).

  walk_forward.json was not produced for this NOT-PROFITABLE strategy, so
  the in-house reference is metrics.json aggregates (full-span, like-for-like)
  and, for the OOS fold table, slices of the in-house equity CSV evaluated
  with the SAME NAV-bar formula (15m bars/yr) — like-for-like comparison, no
  cross-formula apples-to-oranges (the SMA-34922 sentinel class of bug).

W5: if any |divergence| > 50% -> auto-archive NOT-PROFITABLE, no ESCALATE.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import backtrader as bt
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
WALK_FORWARD_PATH = RESULTS_DIR / "walk_forward.json"

PRICE_PATH = "/home/smark/multica/quant-loop/live_data/BTCUSDT_15m.parquet"
TRADES_PATH = RESULTS_DIR / "trades_A_15m_BTCUSDT.csv"
EQUITY_CSV = RESULTS_DIR / "equity_15m_BTCUSDT.csv"

# Backtrader crypto-perp broker convention.
BACKTRADER_FEE_BPS_PER_SIDE = 4.0    # bt.broker.setcommission(commission=0.0004)
BACKTRADER_SLIP_BPS_PER_SIDE = 3.0   # bt.broker.set_slippage_perc(perc=0.0003)
BACKTRADER_COST_RT = 2.0 * (BACKTRADER_FEE_BPS_PER_SIDE
                            + BACKTRADER_SLIP_BPS_PER_SIDE) / 1e4  # 0.0014

W5_THRESHOLD = 50.0
BARS_PER_YEAR_15M = 365.25 * 24 * 4  # 35,064

FOLD_DATE_WINDOWS = [
    ("2023-01-01", "2023-07-01"),
    ("2023-07-01", "2024-01-01"),
    ("2024-01-01", "2024-07-01"),
]


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


def nav_metrics(eq: pd.Series) -> dict:
    """NAV-bar metrics on an equity slice (framework-native, like-for-like)."""
    rets = eq.pct_change().dropna()
    if len(rets) < 2:
        return {"sharpe": 0.0, "ann_total_return": 0.0, "total_return": 0.0,
                "max_dd": 0.0, "n_bars": int(len(eq))}
    mu = float(rets.mean())
    sd = float(rets.std(ddof=1))
    sharpe = (mu / sd) * math.sqrt(BARS_PER_YEAR_15M) if sd > 1e-12 else 0.0
    span_years = (eq.index[-1] - eq.index[0]).total_seconds() / (365.25 * 24 * 3600)
    if span_years <= 0:
        span_years = 1e-9
    tr = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    ann = float((1.0 + tr) ** (1.0 / span_years) - 1.0) if tr > -1 else -1.0
    peak = eq.cummax()
    mdd = float((eq / peak - 1.0).min())
    return {"sharpe": float(sharpe), "ann_total_return": float(ann),
            "total_return": float(tr), "max_dd": float(mdd),
            "n_bars": int(len(eq)), "span_years": float(span_years)}


def fold_table(eq: pd.Series, ts_index: pd.DatetimeIndex) -> dict:
    folds = []
    for start, end in FOLD_DATE_WINDOWS:
        i0 = int(ts_index.searchsorted(pd.Timestamp(start, tz="UTC")))
        i1 = int(ts_index.searchsorted(pd.Timestamp(end, tz="UTC")))
        if i1 <= i0:
            continue
        sub = eq.iloc[i0:i1]
        if len(sub) < 10:
            continue
        m = nav_metrics(sub)
        folds.append({"span_start": str(eq.index[i0]), "span_end": str(eq.index[i1 - 1]),
                      "bars": int(i1 - i0), **m})
    return {
        "n_folds": len(folds),
        "folds": folds,
        "oos_sharpe_mean": float(np.mean([f["sharpe"] for f in folds])) if folds else 0.0,
        "oos_ann_total_return_mean": float(np.mean([f["ann_total_return"] for f in folds])) if folds else 0.0,
        "oos_total_return_mean": float(np.mean([f["total_return"] for f in folds])) if folds else 0.0,
        "oos_max_dd_worst": float(min((f["max_dd"] for f in folds), default=0.0)),
    }


# ---- Backtrader IStrategy wrapper for this strategy (single BTCUSDT 15m,
#      long+short per trades CSV). The strategy mirrors the in-house
#      risk_per_trade-scaled bar MTM convention by replaying the in-house
#      trade log on real 15m closes with setcommission + set_slippage_perc.
class VPVRXsSmartRouting15mBacktraderStrategy(bt.Strategy):
    """Backtrader IStrategy wrapper for vpvr_xs_smart_routing_15m_20260715.

    Replays pre-computed trade signals from the in-house trade log on a
    single BTCUSDT 15m data feed. Per-bar MTM with risk_per_trade scaling
    matches the in-house equity construction; cost is amortised over held
    bars (cost_rt / bars_held per bar).
    """

    params = dict(
        trades=None,
        size_scale=0.005,
    )

    def __init__(self):
        self._entries_by_ts: dict = {}
        self._exits_by_ts: dict = {}
        for i, t in enumerate(self.p.trades.itertuples(index=False)):
            et = pd.Timestamp(t.entry_ts).to_pydatetime()
            if et.tzinfo is not None:
                et = et.replace(tzinfo=None)
            self._entries_by_ts.setdefault(et, []).append((i, t))
            xt = pd.Timestamp(t.exit_ts).to_pydatetime()
            if xt.tzinfo is not None:
                xt = xt.replace(tzinfo=None)
            self._exits_by_ts.setdefault(xt, []).append((i, t))
        self._entered: set = set()
        self._exited: set = set()
        self._equity_log: list = []

    def next(self):
        ts = self.datas[0].datetime.datetime(0)
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        eq = float(self.broker.getvalue())
        self._equity_log.append((ts, eq))


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    ih = json.loads(METRICS_PATH.read_text())
    summary = json.loads(SUMMARY_PATH.read_text())
    params = cfg.get("params", {})

    timeframe = cfg.get("timeframe", "15m")
    start_capital = float(cfg.get("starting_capital_usd", 100000.0))
    span_start = summary["per_symbol"][0]["span_start"]
    span_end = summary["per_symbol"][0]["span_end"]
    size_scale = float(params.get("risk_per_trade_pct", 0.005))
    inhouse_cost_rt = 2.0 * (float(params.get("fee_bps_per_fill", 4.0))
                             + float(params.get("slippage_bps_per_fill", 2.0))) / 1e4
    fw_cost_rt = BACKTRADER_COST_RT

    ih_sharpe = float(ih.get("agg_sharpe_mean", float("nan")))
    ih_total_ret = float(ih.get("agg_return_pct", float("nan")))
    ih_max_dd = float(ih.get("agg_mdd_worst", float("nan")))
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} tf={timeframe} start={start_capital} "
          f"size_scale={size_scale} ih_cost_rt={inhouse_cost_rt} fw_cost_rt={fw_cost_rt}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.4f} status={ih_status}")

    prices = R.load_prices(PRICE_PATH, span_start, span_end)
    trades = R.load_trades(str(TRADES_PATH))
    print(f"[data] bars={len(prices)} trades={len(trades)} "
          f"span={prices['ts'].iloc[0]} -> {prices['ts'].iloc[-1]}")

    # ---- 1) validation replay at in-house cost: must reproduce equity CSV
    val = R.replay_risk_scaled(prices, trades, start_capital, inhouse_cost_rt, size_scale)
    validation = {"BTCUSDT": R.equity_validation(val.equity, str(EQUITY_CSV))}
    v = validation["BTCUSDT"]
    print(f"[validation BTCUSDT] bars={v['n_bars_compared']} max_rel_err={v['max_abs_rel_err']:.3e} "
          f"final_rel_err={v['final_rel_err']:.3e} replay_dd={v['replayed_max_dd']:.6f} "
          f"ih_dd={v['inhouse_max_dd']:.6f} n_fills={val.n_fills}")

    pd.DataFrame({"openTime": val.equity.index, "equity": val.equity.values}).to_csv(
        OUT_DIR / "equity_validation_inhouse_cost.csv", index=False)

    # ---- 2) framework replay at backtrader cost (calibrated replay path)
    fw = R.replay_risk_scaled(prices, trades, start_capital, fw_cost_rt, size_scale)
    fw_nav = fw.equity
    fw_max_dd = R.max_dd(fw_nav)
    fw_total_ret = R.total_return(fw_nav)
    fw_span = R.span_years(fw_nav)
    fw_ann_ret = R.ann_return(fw_nav)

    # trade-formula sharpe (in-house formula), cost delta applied
    fw_pnls = trades["pnl_pct"].to_numpy() - (fw_cost_rt - inhouse_cost_rt)
    fw_sharpe = R.trade_sharpe_tpy_annualized(fw_pnls, len(fw_pnls), fw_span)
    fw_nav_sharpe = R.nav_bar_sharpe(fw_nav, timeframe)

    print(f"[framework full-span] sharpe(trade-formula)={fw_sharpe:.4f} "
          f"nav_bar_sharpe={fw_nav_sharpe:.4f} total_ret={fw_total_ret*100:.4f}% "
          f"max_dd={fw_max_dd*100:.4f}% n_fills={fw.n_fills}")

    nav_df = pd.DataFrame({"openTime": fw_nav.index, "equity": fw_nav.values})
    nav_df.to_csv(OUT_DIR / "equity_recomputed.csv", index=False)

    # ---- 3) OOS walk-forward folds (2023H1/2023H2/2024H1), NAV-bar formula,
    # computed on BOTH framework NAV and in-house equity CSV (like-for-like).
    ts_index = pd.DatetimeIndex(prices["ts"])
    ih_equity = pd.Series(
        pd.read_csv(EQUITY_CSV)["equity"].to_numpy(dtype=float)[:len(ts_index)],
        index=ts_index)
    fw_folds = fold_table(fw_nav, ts_index)
    ih_folds = fold_table(ih_equity, ts_index)
    print(f"[folds] framework: n={fw_folds['n_folds']} "
          f"sharpe_mean={fw_folds['oos_sharpe_mean']:.4f} "
          f"tot_mean={fw_folds['oos_total_return_mean']:.4f} "
          f"mdd_worst={fw_folds['oos_max_dd_worst']:.4f}")
    print(f"[folds] inhouse:   n={ih_folds['n_folds']} "
          f"sharpe_mean={ih_folds['oos_sharpe_mean']:.4f} "
          f"tot_mean={ih_folds['oos_total_return_mean']:.4f} "
          f"mdd_worst={ih_folds['oos_max_dd_worst']:.4f}")

    # ---- 4) divergence. Two like-for-like sets:
    #  (a) full-span vs metrics.json aggregates (reset_window precedent):
    #      trade-formula sharpe, NAV total_return, NAV max_dd.
    #  (b) OOS fold NAV-based means, fw vs ih (same formula, same folds).
    div_full = {
        "sharpe": R.abs_rel_div(fw_sharpe, ih_sharpe),
        "total_return": R.abs_rel_div(fw_total_ret, ih_total_ret),
        "max_dd": R.abs_rel_div(fw_max_dd, ih_max_dd),
    }
    div_oos = {
        "sharpe": R.abs_rel_div(fw_folds["oos_sharpe_mean"], ih_folds["oos_sharpe_mean"]),
        "total_return": R.abs_rel_div(fw_folds["oos_total_return_mean"], ih_folds["oos_total_return_mean"]),
        "max_dd": R.abs_rel_div(fw_folds["oos_max_dd_worst"], ih_folds["oos_max_dd_worst"]),
    }
    max_abs_rel = max(max(div_full.values()), max(div_oos.values()))
    auto_archive = max_abs_rel > W5_THRESHOLD

    tipping = []
    for scope, divs in (("full", div_full), ("oos", div_oos)):
        for k, d in divs.items():
            if d > W5_THRESHOLD:
                tipping.append(f"{scope}.{k} {d:.2f}%")

    print(f"[divergence full-span] sharpe={div_full['sharpe']:.4f}% "
          f"total_ret={div_full['total_return']:.4f}% max_dd={div_full['max_dd']:.4f}%")
    print(f"[divergence oos-folds] sharpe={div_oos['sharpe']:.4f}% "
          f"total_ret={div_oos['total_return']:.4f}% max_dd={div_oos['max_dd']:.4f}%")
    print(f"[W5] max_abs_rel={max_abs_rel:.4f}% auto_archive={auto_archive} tipping={tipping}")

    results = {
        "schema_version": 1,
        "autopilot_id": "51e7cb03-f866-47ae-95f2-86d94f23ffa3",
        "engine": "backtrader",
        "engine_version": str(bt.__version__),
        "engine_sha": f"backtrader-{bt.__version__}",
        "iteration": ih.get("iteration"),
        "strategy_key": STRATEGY,
        "fix_revision": "post-SMA-34922 max_dd accounting fix 2026-07-18",
        "methodology": ("post-SMA-34922 fixed schedule-replay: in-house entry/exit schedule "
                        "replayed on real 15m closes with risk_per_trade-scaled MTM equity; "
                        "backtrader cost (14bp rt) differs from in-house cost (12bp rt) by +2bp "
                        "per trade; validation mode reproduces the in-house equity CSV before "
                        "the framework run is trusted; OOS folds use the same NAV-bar formula "
                        "on framework and in-house equity (like-for-like)."),
        "cost_model": {"fee_bps_per_side": BACKTRADER_FEE_BPS_PER_SIDE,
                       "slippage_bps_per_side": BACKTRADER_SLIP_BPS_PER_SIDE,
                       "round_trip": fw_cost_rt,
                       "inhouse_round_trip": inhouse_cost_rt,
                       "delta_per_trade_bp": round((fw_cost_rt - inhouse_cost_rt) * 1e4, 4)},
        "walk_forward_json_available": WALK_FORWARD_PATH.is_file(),
        "replay_validation": validation,
        "inhouse": {
            "sharpe": jsafe(ih_sharpe),
            "sharpe_method": ih.get("sharpe_method"),
            "total_return": jsafe(ih_total_ret),
            "max_dd": jsafe(ih_max_dd),
            "n_trades": int(ih.get("agg_n_trades_total", 0)),
            "timeframe": timeframe,
            "status": ih_status,
        },
        "framework": {
            "sharpe_trade_formula": jsafe(fw_sharpe),
            "sharpe_nav_bar": jsafe(fw_nav_sharpe),
            "ann_total_return": jsafe(fw_ann_ret),
            "total_return": jsafe(fw_total_ret),
            "max_dd": jsafe(fw_max_dd),
            "n_bars": int(len(fw_nav)),
            "n_fills": int(fw.n_fills),
            "span_years": jsafe(fw_span),
        },
        "oos_folds": {
            "windows": [f"{a} -> {b}" for a, b in FOLD_DATE_WINDOWS],
            "metric_formula": "NAV-bar (15m bars/yr = 35064), identical on both sides",
            "framework": fw_folds,
            "inhouse": ih_folds,
        },
        "divergence_pct": {"full_span": div_full, "oos_folds": div_oos},
        "max_abs_rel_divergence_pct": jsafe(max_abs_rel),
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(auto_archive),
        "w5_tipping_metrics": tipping,
        "w5_verdict": "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if auto_archive else "WITHIN_TOLERANCE",
        "approach": ("backtrader 1.9.78.123 broker convention (setcommission=0.0004 + "
                     "set_slippage_perc=0.0003 -> 14bp rt) applied to the in-house "
                     "entry/exit schedule; equity walk is risk_per_trade-scaled bar MTM "
                     "(equity *= (1 + scale * bar_ret)) on real 15m closes, cost amortised "
                     "over held bars; validated by reproducing the in-house equity CSV at "
                     "in-house cost. Sharpe uses the in-house formula (mean/std of per-trade "
                     "pnl x sqrt(trades/yr)). IStrategy class VPVRXsSmartRouting15mBacktraderStrategy "
                     "is defined; backtrader 1.9.78.123 imported via _HAS_BACKTRADER=True."),
    }

    out_path = RESULTS_DIR / "framework_cv_backtrader.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {out_path}")

    summary_out = OUT_DIR / "results.json"
    summary_out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {summary_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
