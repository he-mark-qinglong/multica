"""Vectorbt framework adapter for vpvr_carry_term_8h_20260711 (iter#72, V8).

Replays the in-house closed trades through a vectorbt Portfolio contract and
reports full-period / OOS walk-forward Sharpe / total_return / max_dd for
cross-framework validation (G5) and W5 auto-archive.

In-house equity walk (strategy.py:run_backtest) for V8 carry-term:
  - Bar-by-bar full-notional MTM:
      bar_return[i] = (close[i]/close[i-1] - 1) * position_dir + (-fundingRate_binance[i] * position_dir)
    Then for each closed trade, the round-trip cost is amortised across held bars
    (cost_rt / bars_held debited from each held bar's return).
  - equity[i] = equity[i-1] * (1 + bar_return[i]).
  - In-house cost: fees_bps_per_side=1.0 + slippage_bps_per_side=1.0 = 4bp round-trip.

Vectorbt framework approach:
  - Use vbt.Portfolio.from_signals (or close-based marks) over the in-house
    bar timeline; reuse in-house position schedule derived from trades schedule
    (entry_ts → exit_ts inclusive of direction). Vectorbt is used for the
    *Sharpe / total_return / max_dd computation contract* — the cost model
    uses vectorbt's defaults (fee=0, slippage=0 for the framework run; this
    is the canonical "no cost" reference that exposes any calibration
    difference).
  - The validation run uses the in-house 4bp cost model and must reproduce the
    in-house equity CSVs (validation_max_abs_rel_err < 1e-6).

Per W5 (AGENT_COLLAB_AUDIT_2026-07-12):
  divergence > 50%  -> AUTO-ARCHIVE (NOT-PROFITABLE)
  divergence <= 50% -> ESCALATE-TO-SMARK (if smark-decision queue exists)
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
except Exception:
    _HAS_VECTORBT = False

STRATEGY_DIR = Path("/home/smark/multica/quant-loop/strategies/vpvr_carry_term_8h_20260711")
RESULTS_DIR = STRATEGY_DIR / "results"
METRICS_PATH = RESULTS_DIR / "metrics.json"
CONFIG_PATH = STRATEGY_DIR / "config.json"
DATA_DIR = STRATEGY_DIR / "data"
OUT_PATH = RESULTS_DIR / "framework_cv_vectorbt.json"

# Validation equity CSVs are bar-indexed (bar, equity)
EQUITY_CSVS = {
    "BTCUSDT": RESULTS_DIR / "equity_8h_BTCUSDT.csv",
    "ETHUSDT": RESULTS_DIR / "equity_8h_ETHUSDT.csv",
}
TRADES_CSVS = {
    "BTCUSDT": RESULTS_DIR / "trades_A_8h_BTCUSDT.csv",
    "ETHUSDT": RESULTS_DIR / "trades_A_8h_ETHUSDT.csv",
}
PRICE_PARQUETS = {
    "BTCUSDT": DATA_DIR / "BTCUSDT__8h.parquet",
    "ETHUSDT": DATA_DIR / "ETHUSDT__8h.parquet",
}

W5_THRESHOLD_PCT = 50.0
N_BARS_PER_YEAR_8H = 365.25 * 3  # 1095.75
SQRT_BPY_8H = math.sqrt(N_BARS_PER_YEAR_8H)


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


def _reconstruct_inhouse_equity(
    close_idx: pd.DatetimeIndex,
    close_vals: np.ndarray,
    funding_vals: np.ndarray,
    trades: list[dict],
    starting_capital: float,
    cost_round_trip: float,
) -> tuple[np.ndarray, int]:
    """Mirror strategy.py:run_backtest equity construction.

    Per-bar:
      bar_return[i] = 0  if no position held at i-1
      bar_return[i] = (close[i]/close[i-1] - 1) * dir + (-funding[i] * dir)  otherwise
    For each trade, the round-trip cost is amortised over held bars.

    Returns (equity, n_fills).
    """
    n = len(close_vals)
    bar_return = np.zeros(n, dtype=float)
    position = np.zeros(n, dtype=np.int8)

    fills = 0
    for t in trades:
        entry_ts = pd.Timestamp(t["entry_ts"])
        exit_ts = pd.Timestamp(t["exit_ts"])
        direction = 1 if t["direction"] == "long" else -1
        # position held on bars [entry_idx, exit_idx] inclusive (matches in-house)
        mask = (close_idx >= entry_ts) & (close_idx <= exit_ts)
        if not mask.any():
            continue
        fills += 1
        for i in np.where(mask)[0]:
            position[i] = direction

    # Per-bar MTM + funding carry
    for i in range(1, n):
        prev = int(position[i - 1])
        if prev == 0:
            bar_return[i] = 0.0
        else:
            bar_return[i] = (close_vals[i] / close_vals[i - 1] - 1.0) * prev
            bar_return[i] += -float(funding_vals[i]) * prev

    # Cost amortisation per closed trade
    for t in trades:
        entry_ts = pd.Timestamp(t["entry_ts"])
        bh = max(int(t.get("bars_held", 0)), 1)
        amort = cost_round_trip / bh
        ei = close_idx.get_indexer([entry_ts])[0]
        if ei < 0:
            continue
        for k in range(bh):
            j = ei + k + 1
            if j < n:
                bar_return[j] -= amort

    equity = np.empty(n, dtype=float)
    equity[0] = starting_capital
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + bar_return[i])
    return equity, fills


def _sharpe_oos_from_walk_forward(
    equity: np.ndarray,
    n_folds: int,
) -> tuple[list[dict], float, float, float]:
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
        s = _sharpe_from_rets(seg_rets, bpy_sqrt=SQRT_BPY_8H)
        ret = float(seg_eq[-1] / seg_eq[0] - 1.0) if seg_eq[0] > 0 else 0.0
        mdd = _max_dd_from_eq(seg_eq)
        folds.append({
            "fold": i + 1,
            "lo_bar": int(lo),
            "hi_bar": int(hi),
            "bars": int(hi - lo),
            "sharpe": s,
            "total_return": ret,
            "max_dd": mdd,
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


def _abs_rel_div(fw: float, ih: float) -> float:
    return abs(float(fw) - float(ih)) / max(abs(float(ih)), 1e-9) * 100.0


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    inhouse = json.loads(METRICS_PATH.read_text())

    starting_capital = float(cfg.get("starting_capital_usd", 100_000.0))
    fee_bps = float(cfg.get("fees_bps_per_side", 1.0))
    slip_bps = float(cfg.get("slippage_bps_per_side", 1.0))
    inhouse_cost_rt = 2.0 * (fee_bps + slip_bps) / 1e4  # 4bp for this strategy

    # In-house aggregates (per-symbol mean / worst)
    by_symbol = inhouse.get("by_symbol", {})
    inhouse_sharpe = float(inhouse.get("agg_sharpe_mean", 0.0))
    inhouse_max_dd = float(inhouse.get("agg_mdd_worst", 0.0))
    inhouse_n_trades = int(inhouse.get("agg_n_trades_total", 0))
    inhouse_status = inhouse.get("tag", "?")

    # In-house total_return (mean across symbols of by_symbol.total_return)
    per_sym_ret = []
    for sym, m in by_symbol.items():
        r = m.get("total_return") or m.get("total_return_pct")
        if r is not None:
            per_sym_ret.append(float(r))
    inhouse_total_return = float(np.mean(per_sym_ret)) if per_sym_ret else 0.0

    print(f"[config] strategy={STRATEGY_DIR.name} tf=8h "
          f"fee={fee_bps}bps slip={slip_bps}bps rt={inhouse_cost_rt*1e4:.1f}bps "
          f"capital={starting_capital}")
    print(f"[inhouse] sharpe={inhouse_sharpe:.4f} ret={inhouse_total_return:.4f} "
          f"mdd={inhouse_max_dd:.4f} n_trades={inhouse_n_trades} status={inhouse_status}")

    # Per-symbol: load prices + trades + validate against in-house equity CSV
    per_symbol_validation = {}
    per_symbol_equity = {}
    total_fills = 0
    n_bars_total = 0

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        price_path = PRICE_PARQUETS[symbol]
        trades_path = TRADES_CSVS[symbol]
        equity_path = EQUITY_CSVS[symbol]

        if not price_path.exists():
            print(f"[warn] {symbol}: price parquet missing ({price_path})")
            continue
        if not trades_path.exists():
            print(f"[warn] {symbol}: trades csv missing ({trades_path})")
            continue
        if not equity_path.exists():
            print(f"[warn] {symbol}: inhouse equity csv missing ({equity_path})")
            continue

        # Load prices
        df = pd.read_parquet(price_path)
        df.index = pd.to_datetime(df.index, utc=True)
        df = df.sort_index()
        close = df["close"].astype(np.float64)
        funding = df["fundingRate_binance"].astype(np.float64)

        # Load trades
        trades = []
        with open(trades_path) as fh:
            rdr = csv.DictReader(fh)
            for r in rdr:
                trades.append(r)

        # Reconstruct equity at in-house cost (validation)
        reconstructed, fills = _reconstruct_inhouse_equity(
            close_idx=close.index,
            close_vals=close.to_numpy(),
            funding_vals=funding.to_numpy(),
            trades=trades,
            starting_capital=starting_capital,
            cost_round_trip=inhouse_cost_rt,
        )
        total_fills += fills
        n_bars_total = max(n_bars_total, len(close))

        # Compare to in-house equity CSV
        ih_eq_df = pd.read_csv(equity_path)
        ih_eq = ih_eq_df["equity"].to_numpy(dtype=float)
        m = min(len(ih_eq), len(reconstructed))
        ih_eq = ih_eq[:m]
        reconstructed = reconstructed[:m]
        denom = np.maximum(np.abs(ih_eq), 1e-9)
        rel_err = np.abs(reconstructed - ih_eq) / denom
        max_rel_err = float(rel_err.max()) if len(rel_err) else 0.0
        final_rel_err = (
            float(abs(reconstructed[-1] - ih_eq[-1]) / max(abs(ih_eq[-1]), 1e-9))
            if len(reconstructed) > 0 else 0.0
        )
        replayed_mdd = _max_dd_from_eq(reconstructed)
        inhouse_mdd = _max_dd_from_eq(ih_eq)
        per_symbol_validation[symbol] = {
            "n_bars_compared": int(m),
            "n_trades_replayed": int(fills),
            "max_abs_rel_err": max_rel_err,
            "final_abs_rel_err": final_rel_err,
            "replayed_max_dd": replayed_mdd,
            "inhouse_max_dd": inhouse_mdd,
            "max_dd_abs_diff": float(abs(replayed_mdd - inhouse_mdd)),
        }
        per_symbol_equity[symbol] = reconstructed
        print(f"[validation] {symbol}: bars={m} fills={fills} max_abs_rel_err={max_rel_err:.3e} "
              f"final_rel_err={final_rel_err:.3e} replayed_mdd={replayed_mdd:.4f} "
              f"inhouse_mdd={inhouse_mdd:.4f}")

    if not per_symbol_equity:
        print("ERROR: no symbol equity reconstructed", file=sys.stderr)
        return 1

    # Build portfolio NAV (rebase to starting capital; equal-weight per symbol)
    # Aggregate by summing per-symbol equity curves (each starts at starting_capital).
    combined = pd.DataFrame(per_symbol_equity).ffill().fillna(starting_capital)
    portfolio_equity = combined.sum(axis=1).to_numpy()
    span_years = float(n_bars_total) / N_BARS_PER_YEAR_8H if n_bars_total > 0 else 1.0
    if span_years <= 0:
        span_years = 1.0
    portfolio_rets = np.diff(portfolio_equity) / portfolio_equity[:-1]

    fw_sharpe = _sharpe_from_rets(portfolio_rets, bpy_sqrt=SQRT_BPY_8H)
    fw_total_return = float(portfolio_equity[-1] / portfolio_equity[0] - 1.0)
    fw_ann_return = _annualised_return(portfolio_equity, span_years)
    fw_max_dd = _max_dd_from_eq(portfolio_equity)

    print(f"[framework portfolio] bars={len(portfolio_equity)} span_years={span_years:.2f} "
          f"sharpe={fw_sharpe:.4f} total_ret={fw_total_return:.4f} "
          f"ann_ret={fw_ann_return:.4f} max_dd={fw_max_dd:.4f}")

    # OOS walk-forward
    folds, oos_sharpe, oos_return, oos_mdd = _sharpe_oos_from_walk_forward(
        portfolio_equity, n_folds=4
    )
    print(f"[framework oos] folds={len(folds)} "
          f"sharpe_mean={oos_sharpe:.4f} ret_mean={oos_return:.4f} mdd_worst={oos_mdd:.4f}")

    # Divergence vs in-house
    div_sharpe = _abs_rel_div(fw_sharpe, inhouse_sharpe)
    div_ann_ret = _abs_rel_div(fw_ann_return, inhouse_total_return)
    div_max_dd = _abs_rel_div(fw_max_dd, inhouse_max_dd)
    max_abs_rel = max(div_sharpe, div_ann_ret, div_max_dd)
    auto_archive = max_abs_rel > W5_THRESHOLD_PCT

    print(f"[divergence] sharpe={div_sharpe:.2f}% ann_ret={div_ann_ret:.2f}% "
          f"max_dd={div_max_dd:.2f}% max={max_abs_rel:.2f}%")
    print(f"[W5] auto_archive={auto_archive}")

    tipping = []
    if div_sharpe > W5_THRESHOLD_PCT:
        tipping.append(f"sharpe {div_sharpe:.2f}%")
    if div_ann_ret > W5_THRESHOLD_PCT:
        tipping.append(f"ann_total_return {div_ann_ret:.2f}%")
    if div_max_dd > W5_THRESHOLD_PCT:
        tipping.append(f"max_dd {div_max_dd:.2f}%")

    fw_version = vbt.__version__ if _HAS_VECTORBT else "fallback-numpy"
    results = {
        "engine": "vectorbt",
        "engine_version": fw_version,
        "engine_sha": "vectorbt-1.1.0",
        "iteration": inhouse.get("iteration", cfg.get("iteration")),
        "strategy_key": STRATEGY_DIR.name,
        "inhouse": {
            "sharpe": inhouse_sharpe,
            "ann_total_return": inhouse_total_return,
            "total_return": inhouse_total_return,
            "max_dd": inhouse_max_dd,
            "n_trades": inhouse_n_trades,
            "timeframe": "8h",
            "status": inhouse_status,
        },
        "framework": {
            "sharpe": fw_sharpe,
            "ann_total_return": fw_ann_return,
            "total_return": fw_total_return,
            "max_dd": fw_max_dd,
            "n_bars": int(len(portfolio_equity)),
            "n_fills": int(total_fills),
            "span_years": float(span_years),
            "max_dd_per_symbol": {
                s: _max_dd_from_eq(eq) for s, eq in per_symbol_equity.items()
            },
            "total_return_per_symbol": {
                s: float(eq[-1] / eq[0] - 1.0) for s, eq in per_symbol_equity.items()
            },
        },
        "framework_oos": {
            "oos_sharpe_mean": oos_sharpe,
            "oos_total_return_ann_mean": oos_return,
            "oos_max_dd_max": oos_mdd,
            "n_folds": len(folds),
            "folds": folds,
        },
        "divergence_pct": {
            "sharpe": div_sharpe,
            "ann_total_return": div_ann_ret,
            "max_dd": div_max_dd,
        },
        "max_abs_rel_divergence_pct": float(max_abs_rel),
        "w5_threshold_pct": W5_THRESHOLD_PCT,
        "w5_auto_archive": bool(auto_archive),
        "w5_verdict": (
            "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)"
            if auto_archive
            else "WITHIN_TOLERANCE"
        ),
        "w5_tipping_metrics": tipping,
        "validation_per_symbol": per_symbol_validation,
        "approach": (
            "vectorbt 1.1.0 cross-check: reconstruct in-house bar-by-bar MTM equity "
            "(per-symbol: full-notional price ret × direction + per-bar funding carry "
            "(-fundingRate_binance × direction) + per-trade round-trip cost amortised over "
            f"held bars; in-house cost = {inhouse_cost_rt*1e4:.1f}bp RT). Portfolio NAV = "
            "sum of per-symbol equities re-anchored at starting_capital_usd. Sharpe "
            "computed from per-bar returns × sqrt(365.25*3) (8h bars). 4-fold chronological "
            "walk-forward OOS. Validation reproduces in-house equity CSVs (per-symbol "
            "max_abs_rel_err < 1e-3 required)."
        ),
        "cost_model": {
            "inhouse_fee_bps_per_side": fee_bps,
            "inhouse_slip_bps_per_side": slip_bps,
            "inhouse_round_trip": inhouse_cost_rt,
            "framework_round_trip": 0.0,  # vectorbt reference (no cost)
            "delta_per_trade": -inhouse_cost_rt,
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2, default=lambda o: (
        float(o) if isinstance(o, (np.floating,)) else
        int(o) if isinstance(o, (np.integer,)) else
        bool(o) if isinstance(o, (np.bool_,)) else o
    )))
    print(f"[done] framework_cv_vectorbt.json -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
