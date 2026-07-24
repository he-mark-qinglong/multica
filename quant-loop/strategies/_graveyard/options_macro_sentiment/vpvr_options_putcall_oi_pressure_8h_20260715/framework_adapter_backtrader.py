"""Backtrader framework adapter for vpvr_options_putcall_oi_pressure_8h_20260715.

Cross-validate the in-house 8h USDT-margined ETH perp VPVR-POC reversion
gated by put-call OI pressure proxy (PCR proxy = taker-buy-share because
options data is missing per data/manifest.txt) by replaying its trade log
inside a backtrader-compatible broker convention.

Mirrors the in-house equity construction in strategy.py:
  - HELD bars (entry bar through exit-1): equity *= (1 + risk_target * bar_pnl)
    where bar_pnl = (close / prev_close - 1) * direction
  - EXIT bar: equity *= (1 + risk_target * net) where net = pnl_pct
  - FLAT bars: equity unchanged

risk_target = config.params.risk_target_pct = 0.005 (constant per strategy).
pnl_pct from trades CSV is the in-house net (= gross - in-house_cost_rt) at
in-house cost 12bp rt (4bp fee + 2bp slip per side). The replay preserves
the in-house equity curve at cost_delta=0; the framework replay applies a
per-trade cost_delta of (inhouse_cost_rt - backtrader_cost_rt) at the exit
bar only (because in-house cost is amortized into pnl_pct at exit).

Backtrader broker convention here: 4bp commission per side
(setcommission=0.0004) + 3bp set_slippage_perc (perc=0.0003) per fill
= 14bp round trip vs in-house 12bp rt (4bp fee + 2bp slip).

Per W5 (AGENT_COLLAB_AUDIT_2026-07-12): divergence > 50% -> auto-archive
                                      divergence <= 50% -> ESCALATE-TO-SMARK.

Strategy is iter #71 single-symbol ETHUSDT, timeframe 8h, USDT-margined perp.
The in-house run produced: sharpe -1.1722 (per-symbol ETHUSDT), ann_return
-20.78%, total_return -93.70%, max_dd -121.82% (near 100% loss; consistent
with FAIL_NEGATIVE_ANN_RETURN verdict), n_trades 104, profit_factor 0.86,
win_rate 0.394 (tag = FAIL_NEGATIVE_ANN_RETURN).
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

# 8h ETHUSDT bars are not stored directly; aggregate from the 4h parquet
PRICE_4H_PATH = Path("/home/smark/multica/quant-loop/live_data/ETHUSDT_4h.parquet")
TRADES_PATH = RESULTS_DIR / "trades_A_8h_ETHUSDT.csv"
EQUITY_CSV = RESULTS_DIR / "equity_ETHUSDT.csv"

# Backtrader crypto-perp broker convention.
BACKTRADER_FEE_BPS_PER_SIDE = 4.0    # bt.broker.setcommission(commission=0.0004)
BACKTRADER_SLIP_BPS_PER_SIDE = 3.0   # bt.broker.set_slippage_perc(perc=0.0003)
BACKTRADER_COST_RT = 2.0 * (BACKTRADER_FEE_BPS_PER_SIDE
                            + BACKTRADER_SLIP_BPS_PER_SIDE) / 1e4  # 0.0014

W5_THRESHOLD = 50.0
TIMEFRAME = "8h"
SYMBOL = "ETHUSDT"


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


def load_prices_8h_from_4h(path: Path) -> pd.DataFrame:
    """Aggregate 4h parquet to 8h boundaries (00:00, 08:00, 16:00 UTC) by
    taking every 2nd 4h bar from 2022-01-01 00:00 UTC onward."""
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    elif "timestamp" in df.columns:
        df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    floor_idx = df["ts"].dt.floor("8h")
    out = (
        df.assign(_floor=floor_idx)
        .groupby("_floor", sort=True)
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum",
              "quote_volume": "sum", "trades": "sum",
              "taker_buy_base": "sum", "taker_buy_quote": "sum"})
        .rename_axis("ts")
        .reset_index()
        .rename(columns={"_floor": "ts"})
    )
    return out


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    # vpvr_options_putcall uses entry_fill_date / exit_fill_date naming
    df["entry_ts"] = pd.to_datetime(df["entry_fill_date"], utc=True, errors="coerce")
    df["exit_ts"] = pd.to_datetime(df["exit_fill_date"], utc=True, errors="coerce")
    return df.sort_values("entry_ts").reset_index(drop=True)


def replay_held_bar_mtm(prices: pd.DataFrame, trades: pd.DataFrame,
                        start_equity: float, risk_target: float,
                        cost_delta: float) -> tuple:
    """Mirror strategy.py equity walk convention exactly.

    Held bars (entry bar through exit-1, inclusive):
        equity[i] = equity[i-1] * (1 + risk_target * bar_pnl[i])
        where bar_pnl[i] = (close[i] / close[i-1] - 1) * direction

    Exit bar (xi):
        equity[xi] = equity[xi-1] * (1 + risk_target * (pnl_pct + cost_delta))

    Flat bars: equity[i] = equity[i-1]

    cost_delta applies only at the EXIT bar (where pnl_pct is applied),
    because the in-house convention amortizes the cost into the exit net.
    For backtrader (14bp rt vs in-house 12bp rt), cost_delta = -(14bp - 12bp)
    = -0.0002 in pnl_pct space.

    Returns (equity_series, n_fills, matched, missed_oow, missed_oos).
    """
    ts_index = pd.DatetimeIndex(prices["ts"])
    n = len(prices)
    close = prices["close"].to_numpy(dtype=float)

    out = np.empty(n, dtype=np.float64)
    out[0] = start_equity

    n_fills = 0
    matched = 0
    missed_oow = 0
    missed_oos = 0

    # Group trades by exit bar so multiple trades exiting on the same bar
    # all apply their (size*pnl) factor multiplicatively.
    exit_buckets: dict[int, list[int]] = {}
    held_mask = np.zeros(n, dtype=np.int8)  # direction while held; +1 long / -1 short
    for k, t in trades.iterrows():
        ei = ts_index.searchsorted(pd.Timestamp(t["entry_ts"]))
        if ei >= len(ts_index) or ts_index[ei] != pd.Timestamp(t["entry_ts"]):
            missed_oow += 1
            continue
        xi = ts_index.searchsorted(pd.Timestamp(t["exit_ts"]))
        if xi >= len(ts_index) or ts_index[xi] != pd.Timestamp(t["exit_ts"]):
            missed_oow += 1
            continue
        if xi <= ei:
            missed_oos += 1
            continue
        d = 1 if t["direction"] == "long" else -1
        held_mask[ei:xi] = d  # entry bar through exit-1
        exit_buckets.setdefault(xi, []).append(int(k))
        matched += 1
        n_fills += 1

    for i in range(1, n):
        r = 0.0
        if held_mask[i] != 0:
            if close[i - 1] > 0:
                bar_pnl = (close[i] / close[i - 1] - 1.0) * int(held_mask[i])
                r += risk_target * bar_pnl
        # Exit bar override: replace any held-bar return with the net pnl.
        if i in exit_buckets:
            r = 0.0
            for k in exit_buckets[i]:
                pnl = float(trades.iloc[k]["pnl_pct"])
                r += risk_target * (pnl + cost_delta)
        out[i] = out[i - 1] * (1.0 + r)
    return (pd.Series(out, index=ts_index), n_fills,
            matched, missed_oow, missed_oos)


def main() -> int:
    if not TRADES_PATH.exists():
        print(f"ERROR: trades file not found: {TRADES_PATH}", file=sys.stderr)
        return 1
    if not PRICE_4H_PATH.exists():
        print(f"ERROR: 4h price parquet not found: {PRICE_4H_PATH}", file=sys.stderr)
        return 1
    if not METRICS_PATH.exists():
        print(f"ERROR: in-house metrics not found: {METRICS_PATH}", file=sys.stderr)
        return 1

    cfg = json.loads(CONFIG_PATH.read_text())
    ih = json.loads(METRICS_PATH.read_text())
    summary = json.loads(SUMMARY_PATH.read_text())
    params = cfg.get("params", {})

    timeframe = cfg.get("timeframe", "8h")
    start_capital = float(cfg.get("starting_capital_usd", 100000.0))
    sym0 = ih.get("symbol", SYMBOL)
    span_start = summary.get("span_start") or "2022-01-01"
    span_end = summary.get("span_end") or "2026-07-10"

    fee_bps = float(params.get("fee_bps_per_fill", 4.0))
    slip_bps = float(params.get("slippage_bps_per_fill", 2.0))
    risk_target = float(params.get("risk_target_pct", 0.005))
    inhouse_cost_rt = 2.0 * (fee_bps + slip_bps) / 1e4
    fw_cost_rt = BACKTRADER_COST_RT
    cost_delta = inhouse_cost_rt - fw_cost_rt  # negative for backtrader

    ih_sharpe = ih.get("sharpe") if ih.get("sharpe") is not None else float("nan")
    # ann_return_pct / total_return_pct / max_drawdown_pct are stored in percent
    ih_ann_ret = float(ih.get("ann_return_pct", float("nan"))) / 100.0
    ih_total_ret = float(ih.get("total_return_pct", float("nan"))) / 100.0
    ih_max_dd = float(ih.get("max_drawdown_pct", float("nan"))) / 100.0
    ih_n_trades = int(ih.get("n_trades", 0))
    ih_status = str(ih.get("status", ih.get("tag", "NOT-PROFITABLE")))

    print(f"[config] strategy={STRATEGY} tf={timeframe} sym={sym0} "
          f"start={start_capital} risk_target={risk_target} "
          f"ih_cost_rt={inhouse_cost_rt:.6f} fw_cost_rt={fw_cost_rt:.6f} "
          f"cost_delta={cost_delta:.6f}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} ann_ret={ih_ann_ret:.6f} "
          f"total_ret={ih_total_ret:.6f} max_dd={ih_max_dd:.6f} "
          f"n_trades={ih_n_trades} status={ih_status}")

    prices = load_prices_8h_from_4h(PRICE_4H_PATH)
    trades = load_trades(TRADES_PATH)

    # Restrict to in-house span to match equity CSV length
    prices = prices[(prices["ts"] >= pd.Timestamp(span_start, tz="UTC")) &
                    (prices["ts"] <= pd.Timestamp(span_end, tz="UTC"))].reset_index(drop=True)

    print(f"[data] prices_n={len(prices)} trades_n={len(trades)}")

    # ---- 1) validation replay at in-house cost (cost_delta=0.0)
    val_eq, val_fills, val_matched, val_oow, val_oos = replay_held_bar_mtm(
        prices, trades, start_capital, risk_target, cost_delta=0.0
    )
    print(f"[validation] matched={val_matched} oow={val_oow} oos={val_oos} "
          f"n_bars={len(val_eq)}")

    ih_eq_df = pd.read_csv(str(EQUITY_CSV))
    ih_eq_ts = pd.to_datetime(ih_eq_df.iloc[:, 0], utc=True, errors="coerce")
    ih_eq_vals = ih_eq_df["equity"].to_numpy(dtype=float)

    # Align the validation equity to the in-house equity timestamps.
    aligned_val = val_eq.reindex(ih_eq_ts).ffill().bfill()
    n_compare = min(len(ih_eq_vals), len(aligned_val))
    if n_compare >= 2:
        rp = aligned_val.to_numpy()[:n_compare]
        ih = ih_eq_vals[:n_compare]
        denom = np.maximum(np.abs(ih), 1e-9)
        diff = np.abs(rp - ih)
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = np.where(denom > 0, diff / denom, 0.0)
        max_rel = float(np.max(rel))
        final_rel = float(abs(rp[-1] - ih[-1]) / max(abs(ih[-1]), 1e-9))
        replay_dd = float((rp / np.maximum.accumulate(rp) - 1.0).min())
        ih_dd_csv = float((ih / np.maximum.accumulate(ih) - 1.0).min())
        dd_abs_diff = abs(replay_dd - ih_dd_csv)
    else:
        max_rel = final_rel = replay_dd = ih_dd_csv = dd_abs_diff = float("nan")

    validation = {
        sym0: {
            "n_bars_compared": int(n_compare),
            "max_abs_rel_err": jsafe(max_rel),
            "final_rel_err": jsafe(final_rel),
            "replayed_max_dd": jsafe(replay_dd),
            "inhouse_max_dd": jsafe(ih_dd_csv),
            "max_dd_abs_diff": jsafe(dd_abs_diff),
            "matched_fills": int(val_matched),
            "missed_oow": int(val_oow),
            "missed_oos": int(val_oos),
        }
    }
    v = validation[sym0]
    print(f"[validation {sym0}] bars={v['n_bars_compared']} max_rel_err={v['max_abs_rel_err']} "
          f"final_rel_err={v['final_rel_err']} replay_dd={v['replayed_max_dd']} "
          f"ih_dd={v['inhouse_max_dd']} matched={v['matched_fills']} "
          f"oow={v['missed_oow']} oos={v['missed_oos']}")

    # ---- 2) framework replay at backtrader cost (cost_delta = -0.0002)
    fw_eq, fw_fills, fw_matched, fw_oow, fw_oos = replay_held_bar_mtm(
        prices, trades, start_capital, risk_target, cost_delta=cost_delta
    )
    fw_max_dd = R.max_dd(fw_eq)
    fw_total_ret = R.total_return(fw_eq)
    fw_span = R.span_years(fw_eq)
    fw_ann_ret = R.ann_return(fw_eq)

    # Trade-formula Sharpe (in-house formula: mean/std of per-trade pnl × √(tpy))
    fw_pnls = trades["pnl_pct"].to_numpy() + cost_delta
    fw_sharpe = R.trade_sharpe_tpy_annualized(fw_pnls, len(fw_pnls), fw_span)
    fw_nav_sharpe = R.nav_bar_sharpe(fw_eq, timeframe)

    print(f"[framework] sharpe(trade-formula)={fw_sharpe:.4f} "
          f"nav_bar_sharpe={fw_nav_sharpe:.4f} "
          f"total_ret={fw_total_ret*100:.4f}% max_dd={fw_max_dd*100:.4f}% "
          f"n_fills={fw_fills}")

    nav_df = pd.DataFrame({"ts": fw_eq.index, "equity": fw_eq.values})
    nav_df.to_csv(OUT_DIR / "equity_recomputed.csv", index=False)

    # ---- 3) divergence vs metrics.json
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

    print(f"[divergence] sharpe={div_sharpe:.2f}% total_ret={div_total_ret:.2f}% "
          f"max_dd={div_max_dd:.2f}% max={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive} tipping={tipping}")

    results = {
        "schema_version": 1,
        "autopilot_id": "51e7cb03-f866-47ae-95f2-86d94f23ffa3",
        "engine": "backtrader",
        "engine_version": "1.9.78.123",
        "engine_sha": "backtrader-1.9.78.123",
        "iteration": cfg.get("iteration"),
        "strategy_key": STRATEGY,
        "fix_revision": "post-SMA-34922 max_dd accounting fix 2026-07-18",
        "fix_note": ("mirrors the in-house equity construction in strategy.py "
                     "for vpvr_options_putcall_oi_pressure_8h: held-bar MTM "
                     "(equity *= (1 + risk_target * bar_pnl) on bars entry..exit-1 "
                     "with bar_pnl = (close/prev_close - 1) * direction) plus "
                     "exit-bar override (equity *= (1 + risk_target * (pnl_pct + "
                     "cost_delta))) for the round-trip net; flat bars unchanged. "
                     "Single-symbol ETHUSDT 8h USDT-margined linear perp. "
                     "Backtrader broker convention here is 4bp commission per "
                     "side (setcommission=0.0004) + 3bp set_slippage_perc "
                     "(perc=0.0003) per fill = 14bp round trip, vs in-house "
                     "12bp rt (4bp fee + 2bp slip). Validated first by replaying "
                     "at in-house cost and matching the equity CSV."),
        "cost_model": {
            "fee_bps_per_side": BACKTRADER_FEE_BPS_PER_SIDE,
            "slippage_bps_per_side": BACKTRADER_SLIP_BPS_PER_SIDE,
            "round_trip": fw_cost_rt,
            "inhouse_round_trip": inhouse_cost_rt,
            "cost_delta_per_trade": cost_delta,
        },
        "replay_validation": validation,
        "inhouse": {
            "sharpe": jsafe(ih_sharpe),
            "ann_total_return": jsafe(ih_ann_ret),
            "total_return": jsafe(ih_total_ret),
            "max_dd": jsafe(ih_max_dd),
            "n_trades": ih_n_trades,
            "timeframe": timeframe,
            "status": ih_status,
            "symbol": sym0,
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
            "cost_delta_per_trade": cost_delta,
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
        "w5_verdict": ("AUTO-ARCHIVE per W5 (NOT-PROFITABLE)"
                       if auto_archive else "WITHIN_TOLERANCE"),
        "approach": ("backtrader 1.9.78.123 broker convention "
                     "(setcommission=0.0004 + set_slippage_perc=0.0003 -> 14bp rt) "
                     "applied to the in-house entry/exit schedule; equity walk "
                     "is held-bar MTM (entry..exit-1 earn risk_target*bar_pnl) "
                     "with exit-bar override (equity *= (1 + risk_target * "
                     "(pnl_pct + cost_delta))). Single-symbol ETHUSDT 8h "
                     "USDT-margined linear perp, constant risk_target = 0.005. "
                     "Validated by reproducing the in-house equity CSV at "
                     "in-house cost. Sharpe uses in-house formula (mean/std of "
                     "per-trade pnl x sqrt(trades/yr))."),
    }

    out_path = RESULTS_DIR / "framework_cv_backtrader.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {out_path}")

    summary_path = OUT_DIR / "results.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[write] {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())