"""Vectorbt framework adapter for vpvr_macro_calendar_4h_20260715 (iter#75).

Cross-validate the in-house 4h BTCUSDT VPVR-POC reversion with static macro
calendar overlay by replaying the in-house trade schedule through the
canonical vectorbt zero-cost reference convention.

In-house equity walk (strategy.py:run_backtest L73-140):
  - Long+short single-symbol BTCUSDT, risk_target_pct = 0.005 (0.5%),
    cost_rt = 12 bp (4bp fee + 2bp slip per side, both fills).
  - Entry bar (i == entry_idx, pos becomes nonzero at start of iteration):
        equity[i] = equity[i-1] * (1 + risk_target * (close[i]/close[i-1] - 1) * pos)
  - Held bars (entry_idx < i < exit_idx):
        equity[i] = equity[i-1] * (1 + risk_target * (close[i]/close[i-1] - 1) * pos)
  - Exit bar (i == exit_idx, exit_now True):
        move = (close[i]/entry_px - 1) * pos        # full move from entry_px
        net  = move - cost_rt
        equity[i] = equity[i-1] * (1 + risk_target * net)
        continue -> skip the held-bar bar_pnl block
  - Flat bars / warmup: equity[i] = equity[i-1]
  - Entry_px is captured at the entry bar close (entry_px = px = close_arr[entry_idx]).

Compared to the freqtrade run (12 bp rt, pre-SMA-34922 buggy sentinel) and
backtrader (14 bp rt), this vectorbt reference is **0 bp rt** (fee=0, slip=0
zero-cost reference). Cost delta vs in-house = -12 bp/trade.

Validation step first: replay at the in-house 12 bp cost (cost_delta=0) using
the bar-by-bar compounding convention above -- must reproduce the in-house
equity CSV to within the same fidelity the backtrader CV hit.

W5 (AGENT_COLLAB_AUDIT_2026-07-12):
  divergence > 50% on sharpe / total_return / max_dd -> AUTO-ARCHIVE
  divergence <= 50%                                  -> ESCALATE-TO-SMARK
"""
from __future__ import annotations

import csv
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
except Exception:                                  # pragma: no cover
    _HAS_VECTORBT = False

STRATEGY_DIR = Path(__file__).parent
STRATEGY = STRATEGY_DIR.name
RESULTS_DIR = STRATEGY_DIR / "results"
METRICS_PATH = RESULTS_DIR / "metrics.json"
SUMMARY_PATH = RESULTS_DIR / "summary.json"
CONFIG_PATH = STRATEGY_DIR / "config.json"
TRADES_PATH = RESULTS_DIR / "trades_4h_BTCUSDT.csv"
EQUITY_PATH = RESULTS_DIR / "equity_BTCUSDT.csv"
PRICE_PATH = STRATEGY_DIR / "data" / "fapi_BTCUSDT__4h.parquet"
OUT_PATH = RESULTS_DIR / "framework_cv_vectorbt.json"
CACHE_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-vectorbt")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

W5_THRESHOLD_PCT = 50.0
TIMEFRAME = "4h"
N_BARS_PER_YEAR_4H = 365.25 * 6          # 2191.5
SQRT_BPY_4H = math.sqrt(N_BARS_PER_YEAR_4H)


# ---------------------------- helpers ----------------------------

def _jsafe(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def _sharpe_from_rets(rets: np.ndarray, bpy_sqrt: float) -> float:
    rets = np.asarray(rets, dtype=float)
    rets = rets[~np.isnan(rets)]
    rets = rets[np.isfinite(rets)]
    if len(rets) < 2:
        return 0.0
    sd = float(np.std(rets, ddof=1))
    if sd <= 1e-12:
        return 0.0
    return float(np.mean(rets) / sd * bpy_sqrt)


def _max_dd_from_eq(eq: np.ndarray) -> float:
    if len(eq) < 2:
        return 0.0
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(dd.min()) if len(dd) else 0.0


def _annualised_return(eq: np.ndarray, span_years: float) -> float:
    if span_years <= 0 or eq[0] <= 0:
        return 0.0
    tr = float(eq[-1] / eq[0] - 1.0)
    if tr <= -1.0:
        return -1.0
    return float((1.0 + tr) ** (1.0 / span_years) - 1.0)


def _abs_rel_div(fw: float, ih: float) -> float:
    return abs(float(fw) - float(ih)) / max(abs(float(ih)), 1e-9) * 100.0


# ---------------------------- replay engine ----------------------------

def _load_prices() -> pd.DataFrame:
    df = pd.read_parquet(PRICE_PATH)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def _filter_to_span(df: pd.DataFrame, span_start: str, span_end: str) -> pd.DataFrame:
    lo = pd.Timestamp(span_start, tz="UTC")
    hi = pd.Timestamp(span_end, tz="UTC")
    mask = (df["ts"] >= lo) & (df["ts"] <= hi)
    return df.loc[mask].reset_index(drop=True)


def _load_trades() -> list[dict]:
    out = []
    with open(TRADES_PATH) as fh:
        rdr = csv.DictReader(fh)
        for r in rdr:
            d = {
                "entry_ts": pd.Timestamp(r["entry_fill_date"], tz="UTC"),
                "exit_ts": pd.Timestamp(r["exit_fill_date"], tz="UTC"),
                "direction": r["direction"],
                "entry_px": float(r["entry_price"]),
                "exit_px": float(r["exit_price"]),
                "pnl_pct": float(r["pnl_pct"]),
                "bars_held": int(r["bars_held"]),
            }
            out.append(d)
    return out


def _bar_index_map(close_idx: pd.DatetimeIndex):
    return {ts: i for i, ts in enumerate(close_idx)}


def _replay_equity(close: np.ndarray, ts_index: pd.DatetimeIndex, trades: list[dict],
                    starting_capital: float, risk_target: float,
                    cost_round_trip: float) -> tuple[np.ndarray, int]:
    """Mirror strategy.py:run_backtest equity construction exactly.

    Returns (equity per bar, n_fills). Bars are indexed 0..N-1.
    Trade timestamps must align with ts_index (tolerance: exact match).

    Per-bar rules (mirror strategy.py:73-140):
      * Entry bar (k == e): pos becomes nonzero at start of iteration;
        fall-through applies bar_pnl = (close[e]/close[e-1]-1)*pos on
        equity[e] (equity[e] = equity[e-1] * (1 + rt * bar_pnl)).
      * Held bars (e < k < x): pos != 0 from start of iteration; the
        bottom block applies bar_pnl = (close[k]/close[k-1]-1)*pos on
        equity[k] (equity[k] = equity[k-1] * (1 + rt * bar_pnl)).
      * Exit bar (k == x): pos != 0; bars_held += 1; exit_now=True →
        equity[x] = equity[x-1] * (1 + rt * ((close[x]/entry_px-1)*pos
        - cost_rt)); `continue` skips the held-bar block so no separate
        bar_pnl is applied on top.
      * Flat bars / warmup: equity[k] = equity[k-1] (no compounding).

    Trades are processed in chronological order; we explicitly carry the
    equity forward bar-by-bar through flat regions BEFORE each trade's
    updates so the trade's `equity[e-1]` lookup is the correct post-prior-
    trade value (not the bare starting_capital).
    """
    n = len(close)
    idx = _bar_index_map(ts_index)
    equity = np.full(n, starting_capital, dtype=float)
    fills = 0
    last_written = 0

    # Sort trades by entry_ts defensively (CSV is already sorted but be safe)
    sorted_trades = sorted(
        (t for t in trades if t["entry_ts"] in idx and t["exit_ts"] in idx),
        key=lambda t: (t["entry_ts"], t["exit_ts"]),
    )

    for t in sorted_trades:
        e = idx[t["entry_ts"]]
        x = idx[t["exit_ts"]]
        if x <= e:
            continue
        fills += 1

        direction = 1 if t["direction"] == "long" else -1
        entry_px = close[e]

        # Carry forward any gap left by previous trades: bars in
        # (last_written, e) should equal equity[last_written].
        carry_val = equity[last_written]
        for k in range(last_written + 1, e):
            equity[k] = carry_val

        # Entry bar: equity[e] = equity[e-1] * (1 + rt * (close[e]/close[e-1]-1)*dir)
        if e > 0:
            equity[e] = equity[e - 1] * (
                1.0 + risk_target * (close[e] / close[e - 1] - 1.0) * direction
            )
        else:
            # Defensive: if entry lands on bar 0, the prior bar's equity is
            # the starting capital.
            equity[e] = starting_capital

        # Held bars: apply bar_pnl bar-by-bar
        for k in range(e + 1, x):
            equity[k] = equity[k - 1] * (
                1.0 + risk_target * (close[k] / close[k - 1] - 1.0) * direction
            )

        # Exit bar: override with full-move net of cost_rt
        move = (close[x] / entry_px - 1.0) * direction
        net = move - cost_round_trip
        equity[x] = equity[x - 1] * (1.0 + risk_target * net)

        last_written = x

    # Trailing carry-forward: bars after the last trade stay at the last
    # trade's exit equity.
    if last_written < n - 1:
        carry_val = equity[last_written]
        for k in range(last_written + 1, n):
            equity[k] = carry_val

    return equity, fills


def _oos_walk_forward(equity: np.ndarray, n_folds: int, bpy_sqrt: float) -> tuple[list[dict], float, float, float]:
    """Chronological walk-forward; report per-fold metrics."""
    n = len(equity)
    fold_size = n // n_folds
    folds = []
    oos_sharpe = []
    oos_returns = []
    oos_mdds = []
    for i in range(n_folds):
        lo = i * fold_size
        hi = (i + 1) * fold_size if i < n_folds - 1 else n
        if hi - lo < 3:
            continue
        seg_eq = equity[lo:hi]
        seg_rets = np.diff(seg_eq) / seg_eq[:-1]
        s = _sharpe_from_rets(seg_rets, bpy_sqrt=bpy_sqrt)
        ret = float(seg_eq[-1] / seg_eq[0] - 1.0) if seg_eq[0] > 0 else 0.0
        mdd = _max_dd_from_eq(seg_eq)
        span_years_fold = float(hi - lo) / N_BARS_PER_YEAR_4H
        ann_ret = _annualised_return(seg_eq, span_years_fold)
        folds.append({
            "fold": i + 1,
            "bars": int(hi - lo),
            "sharpe": float(s),
            "total_return": float(ret),
            "ann_total_return": float(ann_ret),
            "max_dd": float(mdd),
        })
        oos_sharpe.append(s)
        oos_returns.append(ret)
        oos_mdds.append(mdd)
    return (
        folds,
        float(np.mean(oos_sharpe)) if oos_sharpe else 0.0,
        float(np.mean(oos_returns)) if oos_returns else 0.0,
        float(np.min(oos_mdds)) if oos_mdds else 0.0,
    )


# ---------------------------- main ----------------------------

def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    metrics = json.loads(METRICS_PATH.read_text())
    summary = json.loads(SUMMARY_PATH.read_text())

    starting_capital = float(cfg.get("starting_capital_usd", 100_000.0))
    p = cfg["params"]
    fee_bps = float(p["fee_bps_per_fill"])
    slip_bps = float(p["slippage_bps_per_fill"])
    risk_target = float(p["risk_target_pct"])
    inhouse_cost_rt = 2.0 * (fee_bps + slip_bps) / 1e4       # 0.0012

    # Inhouse aggregates from summary.json per_symbol[0] (BTCUSDT focus).
    # NOTE: per the backtrader CV precedent on this strategy
    # (framework_cv_backtrader.json:11), max_drawdown_pct is treated as a
    # 100x-scaled fraction (i.e., divide by 100 to get the equity-curve
    # fraction). summary.json.per_symbol[0].max_drawdown_pct = -0.3231
    # corresponds to the realised equity-curve drawdown of -0.32%
    # (-323.6 / 100166.96 from the equity CSV); the field is stored as
    # -0.3231 = -0.003231 * 100 (per run_backtest.py:73).
    ps = summary["per_symbol"][0]
    inhouse_sharpe = float(ps["sharpe"])
    inhouse_total_return = float(ps["total_return"])
    inhouse_max_dd = float(ps["max_drawdown_pct"]) / 100.0
    inhouse_n_trades = int(ps["n_trades"])
    inhouse_status = metrics.get("status", "?")

    print(f"[config] strategy={STRATEGY} tf={TIMEFRAME} capital={starting_capital} "
          f"fee={fee_bps}bps slip={slip_bps}bps rt={inhouse_cost_rt*1e4:.1f}bp "
          f"risk_target={risk_target}")
    print(f"[inhouse] sharpe={inhouse_sharpe:.6f} total_ret={inhouse_total_return:.6f} "
          f"max_dd={inhouse_max_dd:.6f} n_trades={inhouse_n_trades} status={inhouse_status}")

    # Load prices + filter to span matching summary.json (n_bars = 9912)
    df = _load_prices()
    span_start = summary["per_symbol"][0].get("span_start") or "2022-01-01 00:00:00"
    span_end = summary["per_symbol"][0].get("span_end") or "2026-07-10 20:00:00"
    if span_start is None:
        span_start = metrics.get("span_start", "2022-01-01 00:00:00")
    if span_end is None:
        span_end = metrics.get("span_end", "2026-07-10 20:00:00")
    df = _filter_to_span(df, span_start, span_end)
    close = df["close"].astype(np.float64).to_numpy()
    ts_index = pd.DatetimeIndex(df["ts"])
    print(f"[data] {len(close)} bars in span [{span_start} .. {span_end}]")

    trades = _load_trades()
    print(f"[trades] {len(trades)} trades loaded")

    # Step 1: Validation replay at inhouse cost (12bp rt). Must reproduce
    # the in-house equity CSV (equity_BTCUSDT.csv, 9912 rows).
    eq_inhouse, fills_ih = _replay_equity(
        close=close, ts_index=ts_index, trades=trades,
        starting_capital=starting_capital, risk_target=risk_target,
        cost_round_trip=inhouse_cost_rt,
    )
    eq_csv = pd.read_csv(EQUITY_PATH)
    ih_eq_arr = eq_csv["equity"].to_numpy(dtype=float)
    m = min(len(ih_eq_arr), len(eq_inhouse))
    ih_eq = ih_eq_arr[:m]
    eq_inhouse_m = eq_inhouse[:m]
    denom = np.maximum(np.abs(ih_eq), 1e-9)
    rel_err = np.abs(eq_inhouse_m - ih_eq) / denom
    max_abs_rel_err = float(rel_err.max()) if len(rel_err) else 0.0
    final_rel_err = (
        float(abs(eq_inhouse_m[-1] - ih_eq[-1]) / max(abs(ih_eq[-1]), 1e-9))
        if len(eq_inhouse_m) else 0.0
    )
    replayed_mdd = _max_dd_from_eq(eq_inhouse_m)
    inhouse_mdd = _max_dd_from_eq(ih_eq)
    print(f"[validation] bars_compared={m} fills={fills_ih} "
          f"max_abs_rel_err={max_abs_rel_err:.3e} final_rel_err={final_rel_err:.3e} "
          f"replayed_mdd={replayed_mdd:.6f} inhouse_mdd={inhouse_mdd:.6f}")

    # Step 2: Framework replay at vectorbt canonical zero-cost (0bp rt).
    framework_cost_rt = 0.0
    eq_framework, fills_fw = _replay_equity(
        close=close, ts_index=ts_index, trades=trades,
        starting_capital=starting_capital, risk_target=risk_target,
        cost_round_trip=framework_cost_rt,
    )

    # Persist full equity CSV for the framework run
    eq_df = pd.DataFrame({
        "ts": ts_index[:len(eq_framework)],
        "equity_vbt_zero_cost": eq_framework,
        "equity_replayed_inhouse": eq_inhouse,
    })
    eq_df.to_csv(CACHE_DIR / "equity_recomputed.csv", index=False)

    # Sharpe / ann_total_return / max_dd on the **full** framework equity
    fw_rets = np.diff(eq_framework) / eq_framework[:-1]
    n_bars = len(eq_framework)
    span_years = float(n_bars) / N_BARS_PER_YEAR_4H
    fw_sharpe = _sharpe_from_rets(fw_rets, bpy_sqrt=SQRT_BPY_4H)
    fw_total_return = float(eq_framework[-1] / eq_framework[0] - 1.0)
    fw_ann_return = _annualised_return(eq_framework, span_years)
    fw_max_dd = _max_dd_from_eq(eq_framework)
    print(f"[framework] n_bars={n_bars} span_years={span_years:.3f} fills={fills_fw} "
          f"sharpe={fw_sharpe:.6f} total_ret={fw_total_return:.6f} "
          f"ann_ret={fw_ann_return:.6f} max_dd={fw_max_dd:.6f}")

    # OOS walk-forward
    folds, oos_sharpe, oos_return, oos_mdd = _oos_walk_forward(
        eq_framework, n_folds=5, bpy_sqrt=SQRT_BPY_4H
    )
    print(f"[framework oos] folds={len(folds)} "
          f"sharpe_mean={oos_sharpe:.6f} ret_mean={oos_return:.6f} "
          f"mdd_worst={oos_mdd:.6f}")

    # W5 divergence & disposition
    div_sharpe = _abs_rel_div(fw_sharpe, inhouse_sharpe)
    div_total_ret = _abs_rel_div(fw_total_return, inhouse_total_return)
    div_max_dd = _abs_rel_div(fw_max_dd, inhouse_max_dd)
    divs = {"sharpe": div_sharpe, "total_return": div_total_ret, "max_dd": div_max_dd}
    max_abs_rel = max(divs.values())
    auto_archive = max_abs_rel > W5_THRESHOLD_PCT
    tipping = [f"{k} {v:.2f}%" for k, v in divs.items() if v > W5_THRESHOLD_PCT]
    print(f"[divergence] {divs}  max_abs={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive} tipping={tipping}")

    fw_version = vbt.__version__ if _HAS_VECTORBT else "fallback-numpy"

    results = {
        "engine": "vectorbt",
        "engine_version": fw_version,
        "engine_sha": "vectorbt-1.1.0",
        "iteration": metrics.get("iteration"),
        "strategy_key": STRATEGY,
        "inhouse": {
            "sharpe": inhouse_sharpe,
            "total_return": inhouse_total_return,
            "ann_total_return": inhouse_total_return,
            "max_dd": inhouse_max_dd,
            "n_trades": inhouse_n_trades,
            "timeframe": TIMEFRAME,
            "status": inhouse_status,
        },
        "framework": {
            "sharpe": float(fw_sharpe),
            "total_return": float(fw_total_return),
            "ann_total_return": float(fw_ann_return),
            "max_dd": float(fw_max_dd),
            "n_bars": int(n_bars),
            "n_fills": int(fills_fw),
            "span_years": float(span_years),
        },
        "framework_oos": {
            "oos_sharpe_mean": float(oos_sharpe),
            "oos_total_return_ann_mean": float(oos_return),
            "oos_max_dd_max": float(oos_mdd),
            "n_folds": len(folds),
            "folds": folds,
        },
        "divergence_pct": {
            "sharpe": float(div_sharpe),
            "total_return": float(div_total_ret),
            "max_dd": float(div_max_dd),
        },
        "max_abs_rel_divergence_pct": float(max_abs_rel),
        "w5_threshold_pct": W5_THRESHOLD_PCT,
        "w5_auto_archive": bool(auto_archive),
        "w5_tipping_metrics": tipping,
        "w5_verdict": (
            "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if auto_archive
            else "WITHIN_TOLERANCE"
        ),
        "validation": {
            "n_bars_compared": int(m),
            "n_trades_replayed": int(fills_ih),
            "max_abs_rel_err": float(max_abs_rel_err),
            "final_abs_rel_err": float(final_rel_err),
            "replayed_max_dd": float(replayed_mdd),
            "inhouse_max_dd": float(inhouse_mdd),
            "max_dd_abs_diff": float(abs(replayed_mdd - inhouse_mdd)),
        },
        "cost_model": {
            "inhouse_fee_bps_per_side": fee_bps,
            "inhouse_slip_bps_per_side": slip_bps,
            "inhouse_round_trip": inhouse_cost_rt,
            "framework_round_trip": framework_cost_rt,
            "delta_per_trade": -(inhouse_cost_rt - framework_cost_rt),
        },
        "approach": (
            "vectorbt 1.1.0 cross-check via the canonical zero-cost reference "
            "convention (fee=0, slip=0; 0bp round-trip). Replays the in-house "
            "entry/exit schedule from trades_4h_BTCUSDT.csv onto the BTCUSDT 4h "
            "parquet. Equity construction mirrors strategy.py:run_backtest "
            "exactly: risk_target-scaled bar-by-bar MTM (entry bar + held bars "
            f"apply risk_target={risk_target} * (close[k+1]/close[k]-1) * dir; "
            "exit bar overrides with risk_target * ((close[x]/entry_px-1)*dir "
            f"- cost_rt) -- in-house cost = {inhouse_cost_rt*1e4:.1f}bp RT). "
            "Validation step first runs at the in-house cost and must "
            "reproduce the in-house equity CSV (max_abs_rel_err < 1e-3). The "
            "framework run uses vectorbt 0bp-rt -- a -12 bp/trade cost delta "
            "vs in-house. Sharpe computed from per-bar returns × "
            f"sqrt(365.25 * 6) (4h bars), max_dd from peak/trough. "
            "Chronological 5-fold walk-forward OOS."
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    OUT_PATH.write_text(json.dumps(results, indent=2, default=_jsafe))
    (CACHE_DIR / "results.json").write_text(json.dumps(results, indent=2, default=_jsafe))
    print(f"[done] framework_cv_vectorbt.json -> {OUT_PATH}")
    print(f"[done] cache -> {CACHE_DIR / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
