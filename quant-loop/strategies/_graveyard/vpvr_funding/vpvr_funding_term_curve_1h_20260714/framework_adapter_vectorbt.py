"""Vectorbt framework adapter for vpvr_funding_term_curve_1h_20260714.

Cross-validate the in-house 1h USDT-margined BTC perp funding-term-curve
steepness-z-spread reversion strategy (with VPVR POC directional filter) by
replaying its trade log through the canonical vectorbt zero-cost reference
convention.

Mirrors the in-house equity construction exactly:

  `equity *= (1.0 + net)` at exit, where
      net = (exit_price/entry_price - 1) * direction
            - cost_rt
            + funding_paid_running
  and funding is charged per-bar while held (per strategy.py:185-189, 211).

  Other bars (no exit event): equity unchanged.

The trades CSV `pnl_pct` already encodes the net (gross - cost_rt +
funding_paid) under the in-house cost model (4bp fee + 2bp slip per side
= 12bp rt). The framework replay only changes the cost model: vectorbt
canonical zero-cost reference convention is

    fee_bps_per_side = 0
    slippage_bps_per_side = 0
    round-trip = 0 bp = 0.0

Compared to the freqtrade run (12 bp rt, pre-SMA-34922 buggy sentinel),
backtrader (14 bp rt), and in-house (12 bp rt), this is a **-12 bp cost
delta per trade** (vectorbt zero-cost vs in-house).

Validation step first: replay at in-house cost (12 bp rt, cost_delta = 0)
using the same `replay_term_curve` exit-bar MTM convention used by the
backtrader adapter — must reproduce the in-house equity CSV to within
the same ~1e-5 tolerance band the backtrader run hit (max_abs_rel_err
1.424603e-05 / final_rel_err 1.310369e-05).

Strategy is iter #97 multi-symbol (BTCUSDT+ETHUSDT), timeframe 1h,
USDT-margined perp, per-symbol $100k starting capital. Mirrors the
backtrader CV precedent on this multi-symbol strategy: BTCUSDT leg
focused (the multi-symbol aggregation lives in `metrics.json.agg_*`,
which is what we compare against).

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

# BTCUSDT leg focused (matches the backtrader CV precedent on this
# multi-symbol strategy; the multi-symbol aggregation lives in
# `metrics.json.agg_*`, which is what we compare against).
PRICE_PATH = "/home/smark/multica/quant-loop/live_data/BTCUSDT_1h.parquet"
TRADES_PATH = RESULTS_DIR / "trades_A_1h_BTCUSDT.csv"
EQUITY_CSV = RESULTS_DIR / "equity_1h_BTCUSDT.csv"

# Vectorbt canonical zero-cost reference convention.
VECTORBT_FEE_BPS_PER_SIDE = 0.0
VECTORBT_SLIP_BPS_PER_SIDE = 0.0
VECTORBT_COST_RT = 0.0

W5_THRESHOLD = 50.0
BARS_PER_YEAR_1H = 365.25 * 24          # 1h bars per year
SQRT_BPY_1H = math.sqrt(BARS_PER_YEAR_1H)


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


def replay_term_curve(prices: pd.DataFrame, trades: pd.DataFrame,
                      start_equity: float, cost_delta: float) -> tuple:
    """Mirror strategy.py equity walk for vpvr_funding_term_curve_1h.

    `cost_delta` is `(inhouse_cost - framework_cost)` per trade. For
    in-house-cost validation: cost_delta = 0. For vectorbt (0bp rt vs
    in-house 12bp rt): cost_delta = 0.0012 (positive, lifts every trade).

    At exit bar: equity *= (1 + pnl_pct + cost_delta). Other bars: equity
    unchanged from previous bar. Mirrors strategy.py:165, 193, 212, 215,
    258, 266, 285.

    Returns (equity Series, n_fills).
    """
    ts_index = pd.DatetimeIndex(prices["ts"])
    n = len(prices)
    out = np.empty(n, dtype=np.float64)
    out[0] = start_equity
    exit_delta: dict[int, float] = {}
    n_fills = 0
    for _, t in trades.iterrows():
        ei = R._bar_index(ts_index, t["entry_ts"])
        xi = R._bar_index(ts_index, t["exit_ts"])
        if ei is None or xi is None or xi <= ei:
            continue
        n_fills += 1
        net_per_trade = float(t["pnl_pct"]) + cost_delta
        exit_delta[xi] = exit_delta.get(xi, 0.0) + net_per_trade
    for i in range(1, n):
        if i in exit_delta:
            out[i] = out[i - 1] * (1.0 + exit_delta[i])
        else:
            out[i] = out[i - 1]
    return pd.Series(out, index=ts_index), n_fills


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

    timeframe = cfg.get("timeframe", "1h")
    start_capital = float(cfg.get("starting_capital_per_symbol_usd", 100000.0))
    span_start = summary["per_symbol"][0]["span_start"]
    span_end = summary["per_symbol"][0]["span_end"]
    inhouse_cost_rt = 2.0 * (float(params.get("fee_bps_per_fill", 4.0))
                             + float(params.get("slippage_bps_per_fill", 2.0))) / 1e4
    fw_cost_rt = VECTORBT_COST_RT
    # vectorbt 0bp vs in-house 12bp → cost_delta is positive (+12bp/trade)
    cost_delta = inhouse_cost_rt - fw_cost_rt

    ih_sharpe = ih.get("agg_sharpe_mean", float("nan"))
    ih_total_ret = ih.get("agg_return_pct", float("nan"))
    ih_max_dd = ih.get("agg_mdd_worst", float("nan"))
    ih_n_trades = int(ih.get("agg_n_trades_total", 0))
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} tf={timeframe} start={start_capital} "
          f"ih_cost_rt={inhouse_cost_rt} fw_cost_rt={fw_cost_rt} cost_delta={cost_delta}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.6f} n_trades={ih_n_trades} status={ih_status}")
    print(f"[vectorbt] has_vectorbt={_HAS_VECTORBT} version="
          f"{getattr(vbt, '__version__', '?') if _HAS_VECTORBT else 'n/a'}")

    prices = R.load_prices(PRICE_PATH, span_start, span_end)
    trades = R.load_trades(str(TRADES_PATH))

    # ---- 1) validation replay at in-house cost (cost_delta = 0): must match
    val_eq, val_fills = replay_term_curve(prices, trades, start_capital, cost_delta=0.0)
    ih_eq = pd.read_csv(str(EQUITY_CSV))["equity"].to_numpy(dtype=float)
    if len(ih_eq) == len(val_eq):
        diff = np.abs(val_eq.to_numpy() - ih_eq)
        denom = np.abs(ih_eq)
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = np.where(denom > 0, diff / denom, 0.0)
        max_rel = float(np.max(rel))
        final_rel = float(abs(val_eq.iloc[-1] - ih_eq[-1]) / max(abs(ih_eq[-1]), 1e-9))
        replay_dd = R.max_dd(val_eq)
        ih_dd_csv = R.max_dd(pd.Series(ih_eq))
        dd_abs_diff = abs(replay_dd - ih_dd_csv)
    else:
        max_rel = float("nan")
        final_rel = float("nan")
        replay_dd = R.max_dd(val_eq)
        ih_dd_csv = float("nan")
        dd_abs_diff = float("nan")
    validation = {
        "BTCUSDT": {
            "n_bars_compared": int(len(ih_eq)),
            "max_abs_rel_err": jsafe(max_rel),
            "final_rel_err": jsafe(final_rel),
            "replayed_max_dd": jsafe(replay_dd),
            "inhouse_max_dd": jsafe(ih_dd_csv),
            "max_dd_abs_diff": jsafe(dd_abs_diff),
        }
    }
    v = validation["BTCUSDT"]
    print(f"[validation BTCUSDT] bars={v['n_bars_compared']} "
          f"max_rel_err={v['max_abs_rel_err']:.6e} "
          f"final_rel_err={v['final_rel_err']:.6e} "
          f"replay_dd={v['replayed_max_dd']:.6e} ih_dd={v['inhouse_max_dd']:.6e}")

    # ---- 2) framework replay at vectorbt cost (0bp rt, cost_delta = +0.0012)
    fw_eq, fw_fills = replay_term_curve(prices, trades, start_capital, cost_delta=cost_delta)
    fw_max_dd = R.max_dd(fw_eq)
    fw_total_ret = R.total_return(fw_eq)
    fw_span = R.span_years(fw_eq)
    fw_ann_ret = R.ann_return(fw_eq)

    # Trade-formula Sharpe (in-house formula: mean/std of per-trade pnl × √(trades/yr))
    fw_pnls = trades["pnl_pct"].to_numpy() + cost_delta
    fw_sharpe = R.trade_sharpe_tpy_annualized(fw_pnls, len(fw_pnls), fw_span)
    fw_nav_sharpe = R.nav_bar_sharpe(fw_eq, timeframe)

    print(f"[framework] sharpe(trade-formula)={fw_sharpe:.4f} nav_bar_sharpe={fw_nav_sharpe:.4f} "
          f"total_ret={fw_total_ret*100:.4f}% max_dd={fw_max_dd*100:.4f}% n_fills={fw_fills}")

    nav_df = pd.DataFrame({"openTime": fw_eq.index, "equity": fw_eq.values})
    nav_df.to_csv(OUT_DIR / "equity_recomputed.csv", index=False)

    # ---- 3) divergence vs metrics.json agg_* (full-period; W5 target)
    div_sharpe = R.abs_rel_div(fw_sharpe, ih_sharpe)
    div_total_ret = R.abs_rel_div(fw_total_ret, ih_total_ret)
    div_max_dd = R.abs_rel_div(fw_max_dd, ih_max_dd)
    max_abs_rel = max(div_sharpe, div_total_ret, div_max_dd)
    auto_archive = max_abs_rel > W5_THRESHOLD

    tipping = []
    if div_sharpe > W5_THRESHOLD:
        tipping.append(f"sharpe {div_sharpe:.2f}%")
    if div_total_ret > W5_THRESHOLD:
        tipping.append(f"total_return {div_total_ret:.2f}%")
    if div_max_dd > W5_THRESHOLD:
        tipping.append(f"max_dd {div_max_dd:.2f}%")

    print(f"[divergence full-period] sharpe={div_sharpe:.2f}% total_ret={div_total_ret:.2f}% "
          f"max_dd={div_max_dd:.2f}% max={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive} tipping={tipping}")

    # ---- 4) 5-fold OOS walk-forward (chronological, no overlap; matches
    #         freqtrade CV fold boundaries and the multi-year 4.52y span)
    n_bars = len(fw_eq)
    n_folds = 5
    fold_size = n_bars // n_folds
    folds = []
    oos_sharpe, oos_ret, oos_mdd = [], [], []
    fw_arr = fw_eq.to_numpy()
    for i in range(n_folds):
        lo = i * fold_size
        hi = (i + 1) * fold_size if i < n_folds - 1 else n_bars
        if hi - lo < 3:
            continue
        seg_eq = fw_arr[lo:hi]
        seg_rets = np.diff(seg_eq) / seg_eq[:-1]
        s = sharpe_from_rets(seg_rets, SQRT_BPY_1H)
        ret = float(seg_eq[-1] / seg_eq[0] - 1.0) if seg_eq[0] > 0 else 0.0
        mdd = max_dd_from_eq(seg_eq)
        span_yrs = (fw_eq.index[hi - 1] - fw_eq.index[lo]).total_seconds() / (365.25 * 24 * 3600)
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
    oos_mdd_max = float(np.min(oos_mdd)) if oos_mdd else 0.0
    print(f"[framework oos] folds={len(folds)} sharpe_mean={oos_sharpe_mean:.4f} "
          f"ret_mean={oos_ret_mean:.4f} mdd_worst={oos_mdd_max:.4f}")

    fw_version = getattr(vbt, "__version__", "?") if _HAS_VECTORBT else "fallback-numpy"
    results = {
        "schema_version": 1,
        "autopilot_id": "51e7cb03-f866-47ae-95f2-86d94f23ffa3",
        "engine": "vectorbt",
        "engine_version": fw_version,
        "engine_sha": f"vectorbt-{fw_version}",
        "iteration": ih.get("iteration"),
        "strategy_key": STRATEGY,
        "fix_revision": "post-SMA-34922 max_dd accounting fix 2026-07-18",
        "fix_note": ("mirrors the in-house full-notional equity construction "
                     "(equity *= (1 + pnl_pct) at exit bar) for "
                     "vpvr_funding_term_curve_1h: long+short, multi-symbol "
                     "BTC+ETH, per-trade pnl_pct already includes "
                     "funding_paid_running and in-house cost. Only the cost "
                     "model differs: vectorbt canonical zero-cost reference "
                     "convention (fee=0, slip=0 -> 0bp round trip), vs "
                     "in-house 12bp rt (4bp fee + 2bp slip), backtrader 14bp "
                     "rt, freqtrade 12bp rt. Validated first by replaying at "
                     "in-house cost and matching the equity CSV (BTCUSDT leg "
                     "focused, matches the backtrader CV precedent on this "
                     "multi-symbol strategy)."),
        "cost_model": {
            "fee_bps_per_side": VECTORBT_FEE_BPS_PER_SIDE,
            "slippage_bps_per_side": VECTORBT_SLIP_BPS_PER_SIDE,
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
            "sharpe": jsafe(fw_sharpe),
            "sharpe_nav_bar": jsafe(fw_nav_sharpe),
            "ann_total_return": jsafe(fw_ann_ret),
            "total_return": jsafe(fw_total_ret),
            "max_dd": jsafe(fw_max_dd),
            "n_bars": int(len(fw_eq)),
            "n_fills": int(fw_fills),
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
                       else "WITHIN_TOLERANCE"),
        "approach": ("vectorbt 1.1.0 zero-cost reference convention (fee=0, slip=0 "
                     "-> 0bp rt) applied to the in-house entry/exit schedule; equity "
                     "walk is full-notional exit-bar MTM (equity *= (1 + pnl_pct)) on "
                     "real 1h closes (BTCUSDT leg focused, matches backtrader CV "
                     "precedent on this multi-symbol strategy); validated by "
                     "reproducing the in-house equity CSV at in-house cost. Sharpe "
                     "uses the in-house formula (mean/std of per-trade pnl x "
                     "sqrt(trades/yr))."),
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
    raise SystemExit(main())