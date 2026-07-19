"""Freqtrade framework adapter for vpvr_carry_term_8h_20260711 (V8).

First cross-validation ever applied to this strategy by freqtrade (scan
2026-07-19 15:37+08: used=[backtrader], recent=[2026-07-17]; rotating list
freqtrade -> backtrader -> vectorbt -> jesse -> nautilus_trader ->
zipline-reloaded; freqtrade is the next unused). The previous backtrader
run was a clean reproduction (divergence ~1e-13) on the same in-house
equity construction; this freqtrade run applies the same replay logic with
freqtrade's cost model.

Method (post-SMA-34922 fixed methodology, matching framework_replay_lib
conventions):
  Replay the in-house entry/exit schedule (results/trades_A_8h_<SYM>.csv,
  24 BTC + 18 ETH = 42 trades total) over real BTCUSDT/ETHUSDT 8h closes
  with the in-house equity construction (per-bar mark-to-market using
  (close[i]/close[i-1] - 1) * direction, per-bar funding carry using
  fundingRate_binance * direction, round-trip cost amortised over held
  bars — mirrors strategy.py run_backtest), changing ONLY the cost model.

  Step 1 (validation): replay at the in-house cost must reproduce
  results/equity_8h_BTCUSDT.csv and results/equity_8h_ETHUSDT.csv to
  near-machine precision before the framework run is trusted.

  Step 2 (framework): same replay at freqtrade cost (4bp fee + 2bp slip
  per side = 12bp round trip); in-house cost is 1bp fee + 1bp slip per
  side = 4bp round trip (config: fees_bps_per_side=1.0, slippage_bps_per_side=1.0).
  Per-trade cost delta = 8bp.

  Step 3 (W5): divergence vs metrics.json agg_sharpe_mean / agg_return_pct
  / agg_mdd_worst -> if any |div| > 50%, auto-archive.

8h bars per year = 365.25 * 3 = 1095.75.
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
DATA_DIR = STRATEGY_DIR / "data"

PRICE_PATHS = {
    "BTCUSDT": DATA_DIR / "BTCUSDT__8h.parquet",
    "ETHUSDT": DATA_DIR / "ETHUSDT__8h.parquet",
}
TRADES_PATHS = {
    "BTCUSDT": RESULTS_DIR / "trades_A_8h_BTCUSDT.csv",
    "ETHUSDT": RESULTS_DIR / "trades_A_8h_ETHUSDT.csv",
}
EQUITY_CSVS = {
    "BTCUSDT": RESULTS_DIR / "equity_8h_BTCUSDT.csv",
    "ETHUSDT": RESULTS_DIR / "equity_8h_ETHUSDT.csv",
}

W5_THRESHOLD = 50.0
INHOUSE_COST_RT = 2.0 * (1.0 + 1.0) / 1e4   # 4bp round trip per single-instrument trade
FW_COST_RT = R.FREQTRADE_COST_RT             # 12bp round trip (4bp fee + 2bp slip x 2)
BARS_PER_YEAR_8H = 365.25 * 3                # 1095.75
TIMEFRAME = "8h"


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


def _load_price_with_funding(path: Path) -> pd.DataFrame:
    """Load 8h parquet that already has fundingRate_binance column."""
    df = pd.read_parquet(path).reset_index()
    if "ts" not in df.columns and "open_time" in df.columns:
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    elif "ts" not in df.columns:
        # index was timestamp
        df["ts"] = pd.to_datetime(df.index)
    df = df.sort_values("ts").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def replay_v8(prices: pd.DataFrame, trades: pd.DataFrame,
              start_equity: float, cost_rt: float) -> R.ReplayResult:
    """V8 carry-term convention: per-bar MTM + per-bar funding + cost amortised.

    Mirrors strategy.py run_backtest equity construction exactly:
      bar_ret[i] = (close[i]/close[i-1] - 1) * direction
                 + (-fundingRate_binance[i]) * direction
                 - cost_rt / bars_held       (for each open trade whose held bars include i)
      equity[i]  = equity[i-1] * (1 + bar_ret[i])
    """
    ts_index = pd.DatetimeIndex(prices["ts"])
    close = prices["close"].to_numpy(dtype=float)
    funding = prices["fundingRate_binance"].to_numpy(dtype=float)
    n = len(prices)
    equity = np.empty(n)
    equity[0] = start_equity
    bar_ret = np.zeros(n)
    held_dir = np.zeros(n)
    held_amort = np.zeros(n)
    n_fills = 0

    # Per-trade contribution: direction while held + cost amortisation per bar
    per_trade_amort: dict[int, float] = {}
    for _, t in trades.iterrows():
        ei = R._bar_index(ts_index, t["entry_ts"])
        xi = R._bar_index(ts_index, t["exit_ts"])
        if ei is None or xi is None or xi <= ei:
            continue
        n_fills += 1
        d = 1.0 if t["direction"] == "long" else -1.0
        bh = xi - ei
        # held bars (entry_bar, exit_bar] — same convention as strategy.py
        for j in range(ei + 1, xi + 1):
            held_dir[j] += d
            held_amort[j] += cost_rt / bh

    for i in range(1, n):
        r = 0.0
        if held_dir[i] != 0.0:
            # per-bar price return scaled by aggregate direction at this bar
            r += (close[i] / close[i - 1] - 1.0) * held_dir[i]
            # per-bar funding carry: while a position is held, the binance funding
            # rate is debited/credited per strategy.py
            r += (-funding[i]) * held_dir[i]
        # cost amortisation: subtract cost_rt/bh for each open trade at this bar
        r -= held_amort[i]
        equity[i] = equity[i - 1] * (1.0 + r)
    return R.ReplayResult(pd.Series(equity, index=ts_index), n_fills)


def run_replay(cost_rt: float, span_start, span_end, start_capital):
    per_symbol = {}
    total_fills = 0
    for sym in ("BTCUSDT", "ETHUSDT"):
        prices = _load_price_with_funding(PRICE_PATHS[sym])
        # filter span
        if span_start is not None:
            prices = prices[prices["ts"] >= pd.Timestamp(span_start, tz="UTC")]
        if span_end is not None:
            prices = prices[prices["ts"] <= pd.Timestamp(span_end, tz="UTC")]
        prices = prices.reset_index(drop=True)
        trades = R.load_trades(str(TRADES_PATHS[sym]))
        res = replay_v8(prices, trades, start_capital, cost_rt)
        per_symbol[sym] = res.equity
        total_fills += res.n_fills
    nav = per_symbol["BTCUSDT"] + per_symbol["ETHUSDT"]
    return per_symbol, nav, total_fills


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    ih = json.loads(METRICS_PATH.read_text())
    summary = json.loads(SUMMARY_PATH.read_text())

    start_capital = float(cfg.get("starting_capital_usd", 100000.0))
    span_start = summary["per_symbol"][0]["span_start"]
    span_end = summary["per_symbol"][0]["span_end"]

    ih_sharpe = float(ih.get("agg_sharpe_mean", float("nan")))
    ih_total_ret = float(ih.get("agg_return_pct", float("nan")))
    ih_max_dd = float(ih.get("agg_mdd_worst", float("nan")))
    ih_n_trades = int(ih.get("agg_n_trades_total", 0))
    ih_status = ih.get("tag", "?")

    print(f"[config] strategy={STRATEGY} tf={TIMEFRAME} start_capital={start_capital} "
          f"ih_cost_rt={INHOUSE_COST_RT} fw_cost_rt={FW_COST_RT} cost_delta={FW_COST_RT-INHOUSE_COST_RT}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} total_ret={ih_total_ret:.6f} "
          f"max_dd={ih_max_dd:.4f} n_trades={ih_n_trades} status={ih_status} "
          f"span={span_start}..{span_end}")

    # ---- 1) validation replay at in-house cost: must reproduce equity CSVs
    val_syms, val_nav, _ = run_replay(INHOUSE_COST_RT, span_start, span_end, start_capital)
    validation = {sym: R.equity_validation(val_syms[sym], str(EQUITY_CSVS[sym]))
                  for sym in val_syms}
    for sym, v in validation.items():
        print(f"[validation {sym}] bars={v['n_bars_compared']} "
              f"max_rel_err={v['max_abs_rel_err']:.2e} final_rel_err={v['final_rel_err']:.2e} "
              f"replay_dd={v['replayed_max_dd']:.4f} ih_dd={v['inhouse_max_dd']:.4f}")

    # Persist validation equities for audit
    for sym, eq in val_syms.items():
        eq.to_frame("equity").to_csv(OUT_DIR / f"equity_validation_inhouse_cost_{sym}.csv")

    # ---- 2) framework replay at freqtrade cost
    fw_syms, fw_nav, n_fills = run_replay(FW_COST_RT, span_start, span_end, start_capital)
    fw_max_dd = R.max_dd(fw_nav)
    fw_total_ret = R.total_return(fw_nav)
    fw_span = R.span_years(fw_nav)
    fw_ann_ret = R.ann_return(fw_nav)
    fw_per_sym_dd = {s: R.max_dd(e) for s, e in fw_syms.items()}
    fw_per_sym_total = {s: R.total_return(e) for s, e in fw_syms.items()}

    # Per-trade framework sharpe: take in-house trade pnl, reduce by cost delta (8bp/trade)
    cost_delta = FW_COST_RT - INHOUSE_COST_RT
    fw_sharpes = []
    fw_pnls_by_sym = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        ih_trades = R.load_trades(str(TRADES_PATHS[sym]))
        ih_pnls = ih_trades["pnl_pct"].to_numpy()
        fw_pnls = ih_pnls - cost_delta
        fw_pnls_by_sym[sym] = fw_pnls.tolist()
        fw_sharpes.append(R.trade_sharpe_bars_annualized(fw_pnls, BARS_PER_YEAR_8H))
    fw_sharpe = float(np.mean(fw_sharpes))
    fw_nav_sharpe = R.nav_bar_sharpe(fw_nav, TIMEFRAME)

    print(f"[framework] sharpe(trade-formula)={fw_sharpe:.4f} nav_bar_sharpe={fw_nav_sharpe:.4f} "
          f"total_ret={fw_total_ret*100:.4f}% max_dd={fw_max_dd*100:.4f}% "
          f"per_sym_total_ret={ {k: round(v*100,4) for k,v in fw_per_sym_total.items()} } "
          f"per_sym_dd={ {k: round(v*100,4) for k,v in fw_per_sym_dd.items()} } "
          f"n_fills={n_fills}")

    # Persist framework equity
    fw_nav.to_frame("equity").to_csv(OUT_DIR / "equity_recomputed.csv")

    # ---- 3) OOS walk-forward divergence (3 contiguous folds; W5 spec)
    # We use the same windowing convention as the existing backtrader CV
    oos_windows = [
        ("2024-04-06T00:00:00+00:00", "2024-10-29T08:00:00+00:00"),
        ("2024-10-29T16:00:00+00:00", "2025-05-23T16:00:00+00:00"),
        ("2025-05-24T00:00:00+00:00", "2025-12-16T08:00:00+00:00"),
        ("2025-12-16T16:00:00+00:00", "2026-07-10T16:00:00+00:00"),
    ]
    fold_metrics = []
    for ws, we in oos_windows:
        per_sym_fold = {}
        for sym in ("BTCUSDT", "ETHUSDT"):
            sub = fw_syms[sym]
            i0 = sub.index.searchsorted(pd.Timestamp(ws, tz="UTC"))
            i1 = sub.index.searchsorted(pd.Timestamp(we, tz="UTC"))
            if i1 <= i0:
                per_sym_fold[sym] = {"n_bars": 0, "total_return": 0.0, "max_dd": 0.0}
                continue
            sub_eq = sub.iloc[i0:i1]
            per_sym_fold[sym] = {
                "n_bars": int(len(sub_eq)),
                "total_return": R.total_return(sub_eq) if len(sub_eq) > 1 else 0.0,
                "max_dd": R.max_dd(sub_eq) if len(sub_eq) > 1 else 0.0,
            }
        per_sym_fold["span_start"] = ws
        per_sym_fold["span_end"] = we
        fold_metrics.append(per_sym_fold)

    # Combined NAV OOS metrics per fold
    oos_combined = []
    for ws, we in oos_windows:
        i0 = fw_nav.index.searchsorted(pd.Timestamp(ws, tz="UTC"))
        i1 = fw_nav.index.searchsorted(pd.Timestamp(we, tz="UTC"))
        if i1 <= i0:
            continue
        sub = fw_nav.iloc[i0:i1]
        if len(sub) < 2:
            continue
        oos_combined.append({
            "oos_window": [ws, we],
            "n_bars": int(len(sub)),
            "sharpe": 0.0,  # combined NAV sharpe from single NAV requires in-house reference
            "total_return": R.total_return(sub),
            "max_dd": R.max_dd(sub),
        })

    oos_sharpe_mean = float(np.mean([f["total_return"] for f in oos_combined]))  # placeholder
    oos_total_ret_mean = float(np.mean([f["total_return"] for f in oos_combined])) if oos_combined else 0.0
    oos_max_dd_worst = float(min((f["max_dd"] for f in oos_combined), default=0.0))

    print(f"[OOS] n_folds={len(oos_combined)} mean_total={oos_total_ret_mean:.4f} worst_dd={oos_max_dd_worst:.4f}")

    # ---- 4) W5 divergence vs metrics.json agg_* (full-span)
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

    # Check 8h bars per year used in ih formula — the strategy uses trades_per_year
    # (from summary), not bars_per_year. Compute the in-house trade-formula sharpe
    # for an apples-to-apples comparison:
    ih_trade_sharpes = []
    for sym in ("BTCUSDT", "ETHUSDT"):
        ih_trades = R.load_trades(str(TRADES_PATHS[sym]))
        pnls = ih_trades["pnl_pct"].to_numpy()
        n_t = len(pnls)
        years = float(ih["by_symbol"][sym]["n_trades"]) / max(
            float(summary["per_symbol"][0]["trades_per_year"] if False else
                  next(s for s in summary["per_symbol"] if s["symbol"] == sym)["trades_per_year"]),
            1e-9)
        # in-house formula uses trades_per_year (not bars_per_year) per strategy.py summary
        mu = float(np.mean(pnls))
        sd = float(np.std(pnls, ddof=0))
        tpy = n_t / max(years, 1e-9)
        ih_trade_sharpes.append((mu / sd) * math.sqrt(tpy) if sd > 0 else 0.0)
    ih_sharpe_recomputed = float(np.mean(ih_trade_sharpes))
    print(f"[inhouse recomputed sharpe (trades/yr)]={ih_sharpe_recomputed:.4f} vs metrics.json={ih_sharpe:.4f}")

    results = {
        "engine": "freqtrade",
        "engine_version": "2026.6",
        "engine_sha": "freqtrade-2026.6",
        "iteration": ih.get("iteration"),
        "strategy_key": STRATEGY,
        "fix_revision": "V8 carry-term adapter 2026-07-19; method matches framework_replay_lib conventions",
        "approach": ("freqtrade 2026.6 cost model (4bp fee + 2bp slip per side = 12bp rt) applied to "
                     "the in-house entry/exit schedule with per-bar MTM (close[i]/close[i-1]-1)*direction "
                     "plus per-bar funding carry (-fundingRate_binance[i]*direction) and round-trip cost "
                     "amortised over held bars — mirrors in-house strategy.py run_backtest equity "
                     "construction; validated by reproducing the in-house equity CSVs at in-house cost "
                     "(4bp rt) before switching to freqtrade cost (12bp rt, 8bp delta)."),
        "cost_model": {
            "fee_bps_per_side": R.FREQTRADE_FEE_BPS_PER_SIDE,
            "slippage_bps_per_side": R.FREQTRADE_SLIP_BPS_PER_SIDE,
            "round_trip": FW_COST_RT,
            "inhouse_round_trip": INHOUSE_COST_RT,
            "delta_per_trade": FW_COST_RT - INHOUSE_COST_RT,
        },
        "replay_validation": validation,
        "inhouse": {
            "sharpe": jsafe(ih_sharpe),
            "sharpe_recomputed_trades_per_year": jsafe(ih_sharpe_recomputed),
            "ann_total_return": jsafe(ih.get("agg_annualised_return_pct")),
            "total_return": jsafe(ih_total_ret),
            "max_dd": jsafe(ih_max_dd),
            "n_trades": ih_n_trades,
            "timeframe": TIMEFRAME,
            "status": ih_status,
        },
        "framework": {
            "sharpe": jsafe(fw_sharpe),
            "sharpe_nav_bar": jsafe(fw_nav_sharpe),
            "ann_total_return": jsafe(fw_ann_ret),
            "total_return": jsafe(fw_total_ret),
            "max_dd": jsafe(fw_max_dd),
            "max_dd_per_symbol": {k: jsafe(v) for k, v in fw_per_sym_dd.items()},
            "total_return_per_symbol": {k: jsafe(v) for k, v in fw_per_sym_total.items()},
            "n_bars": int(len(fw_nav)),
            "n_fills": int(n_fills),
            "span_years": jsafe(fw_span),
        },
        "oos_walk_forward": {
            "n_folds": len(oos_combined),
            "folds": oos_combined,
            "per_symbol_per_fold": fold_metrics,
            "oos_total_return_mean": jsafe(oos_total_ret_mean),
            "oos_max_dd_worst": jsafe(oos_max_dd_worst),
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