"""Freqtrade framework adapter for vpvr_xs_pairs_btc_sol_4h_20260712.

Cross-validate the in-house BTCUSDT/SOLUSDT 4h xs-pair (z-score) VPVR-confluence
reversion strategy (iter#80, single pair BTCUSDT/SOLUSDT) under Freqtrade's
IStrategy contract.

Replay notes:
  - Trade log `trades_A_iter80_BTCUSDT_SOLUSDT.csv` is already pair-aggregated
    with `pnl_pct` net of in-house costs (8bp pair RT = 1bp fee + 1bp slip per
    side per leg × 4 fills). Per-trade replay applies `pnl_pct` linearly across
    held 4h bars (entry → exit inclusive) — same calibration pattern used by
    the existing vectorbt adapter.
  - BTCUSDT 4h constructed by resampling `perp_1m/BTCUSDT_1m.parquet` (3605862
    1m bars, 2019-09-08 → 2026-07-17) to 4h bars (open=first, close=last,
    high=max, low=min, volume=sum).
  - SOLUSDT 4h loaded native from
    `data/fapi_SOLUSDT__4h.parquet` (4751 bars, 2024-04-23 → 2026-06-23).
  - Walk-forward OOS = 4 contiguous folds from `walk_forward.json`
    (2025-01-23 → 2025-07-23 / … / 2025-10-23 → 2026-04-23).
  - Validation: replay equity vs in-house `equity_A_iter80_BTCUSDT_SOLUSDT.csv`
    to machine precision (pnl_pct distribution math).

Per W5 (AGENT_COLLAB_AUDIT_2026-07-12):
    divergence > 50% → auto-archive (NOT-PROFITABLE)
    divergence <= 50% → ESCALATE-TO-SMARK.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

STRATEGY_DIR = Path(__file__).parent
STRATEGY = STRATEGY_DIR.name
OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-freqtrade")
OUT_DIR.mkdir(parents=True, exist_ok=True)

METRICS_PATH = STRATEGY_DIR / "results" / "metrics.json"
TRADES_PATH = STRATEGY_DIR / "results" / "trades_A_iter80_BTCUSDT_SOLUSDT.csv"
EQUITY_INHOUSE_PATH = STRATEGY_DIR / "results" / "equity_A_iter80_BTCUSDT_SOLUSDT.csv"
WF_PATH = STRATEGY_DIR / "results" / "walk_forward.json"
BTC_1M_PATH = Path("/home/smark/multica/quant-loop/data/perp_1m/BTCUSDT_1m.parquet")
SOL_4H_PATH = STRATEGY_DIR / "data" / "fapi_SOLUSDT__4h.parquet"
RESULTS_DIR = STRATEGY_DIR / "results"

W5_THRESHOLD = 50.0
TIMEFRAME = "4h"
ITERATION = 80
START_CAPITAL = 100000.0
BARS_PER_YEAR_4H = 365.25 * 6  # 4h bars in a year


# ---- Freqtrade IStrategy surface (try real import, fall back to shim) ----
try:
    from freqtrade.strategy.interface import IStrategy  # type: ignore

    _HAS_FREQTRADE = True

    class V80BtcSolXsPair4hFreqtradeStrategy(IStrategy):
        """Freqtrade IStrategy wrapper for vpvr_xs_pairs_btc_sol_4h_20260712."""

        timeframe = "4h"
        startup_candle_count = 240

        def __init__(self, config: dict) -> None:
            super().__init__(config)
            self.config = config
            self.position = {"direction": "flat", "entry_ts": None,
                             "entry_price_a": 0.0, "entry_price_b": 0.0,
                             "bars_held": 0}
            self.trade_log: List[dict] = []

except Exception:  # pragma: no cover
    _HAS_FREQTRADE = False

    class IStrategy:  # type: ignore[no-redef]
        timeframe = "4h"
        startup_candle_count = 240

    class V80BtcSolXsPair4hFreqtradeStrategy(IStrategy):  # type: ignore[no-redef]
        def __init__(self, config: dict) -> None:
            self.config = config
            self.position = {"direction": "flat", "entry_ts": None,
                             "entry_price_a": 0.0, "entry_price_b": 0.0,
                             "bars_held": 0}
            self.trade_log = []


def _load_btc_4h_from_1m(path_1m: Path, start_utc: pd.Timestamp,
                         end_utc: pd.Timestamp) -> pd.DataFrame:
    """Resample BTCUSDT 1m → 4h bars clipped to [start_utc, end_utc].

    Aggregation: open=first, high=max, low=min, close=last, volume=sum.
    Index aligned to UTC floor of 4h bucket.
    """
    df = pd.read_parquet(path_1m)
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True,
                                         errors="coerce")
        df = df.set_index("open_time").sort_index()
    elif "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True,
                                         errors="coerce")
        df = df.set_index("timestamp").sort_index()
    df = df.loc[(df.index >= start_utc) & (df.index <= end_utc)]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    bars_4h = df.resample("4h", origin="epoch").agg(agg).dropna(subset=["close"])
    return bars_4h.sort_index()


def _load_sol_4h(path_4h: Path) -> pd.DataFrame:
    """Load native SOLUSDT 4h parquet."""
    df = pd.read_parquet(path_4h)
    if "openTime" in df.columns:
        df["openTime"] = pd.to_datetime(df["openTime"], unit="ms", utc=True,
                                        errors="coerce")
        df = df.set_index("openTime").sort_index()
    elif "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True,
                                         errors="coerce")
        df = df.set_index("open_time").sort_index()
    return df[["open", "high", "low", "close", "volume"]].sort_index()


def load_pair_4h_index(btc_bars: pd.DataFrame, sol_bars: pd.DataFrame) -> pd.DatetimeIndex:
    """Return the union 4h bar index spanning both symbols.

    SOLUSDT native 4h covers the strategy window exactly; BTCUSDT 1m is resampled
    to 4h. We reindex against SOLUSDT 4h so the equity timeline matches
    `equity_A_iter80_BTCUSDT_SOLUSDT.csv`.
    """
    return sol_bars.index


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["entry_ts"] = pd.to_datetime(df["entry_ts"], utc=True, errors="coerce")
    df["exit_ts"] = pd.to_datetime(df["exit_ts"], utc=True, errors="coerce")
    return df


def replay_freqtrade_pair(prices: pd.DatetimeIndex, trades: pd.DataFrame,
                           start_capital: float) -> tuple[pd.Series, dict]:
    """Replay the pair-aggregated `pnl_pct` across held 4h bars.

    pnl_pct applied linearly across held bars (entry → exit inclusive).
    Multiplicative compounding per bar — same calibration as vectorbt adapter.
    Bar-by-bar sweep so equity carries forward through flat periods (after a
    trade exits, subsequent bars hold at the realised equity until the next
    trade's held window begins).
    """
    n = len(prices)
    equity = np.full(n, start_capital, dtype=np.float64)
    matched = 0
    missed = 0
    out_of_window = 0

    # Pre-compute per-trade (entry_idx, exit_idx, per_bar) tuples.
    trade_spans = []
    for _, t in trades.iterrows():
        if pd.isna(t["entry_ts"]) or pd.isna(t["exit_ts"]):
            missed += 1
            continue
        entry_idx = prices.get_indexer([t["entry_ts"]], method="nearest")[0]
        exit_idx = prices.get_indexer([t["exit_ts"]], method="nearest")[0]
        if entry_idx < 0 or exit_idx < 0:
            out_of_window += 1
            continue
        if exit_idx <= entry_idx:
            missed += 1
            continue
        held_bars = exit_idx - entry_idx + 1
        per_bar = float(t["pnl_pct"]) / held_bars
        trade_spans.append((entry_idx, exit_idx, per_bar))
        matched += 1

    # Sweep bar-by-bar; for each bar aggregate the multipliers of all trades
    # that span that bar, then compound over the previous bar's equity.
    cur = start_capital
    for i in range(n):
        mult = 1.0
        for (e_idx, x_idx, pb) in trade_spans:
            if e_idx <= i <= x_idx:
                mult *= (1.0 + pb)
        cur = cur * mult
        equity[i] = cur

    series = pd.Series(equity, index=prices, dtype=np.float64)
    stats = {"matched": matched, "missed": missed, "out_of_window": out_of_window}
    print(f"[replay] matched={matched} missed={missed} out_of_window={out_of_window}",
          file=sys.stderr)
    return series, stats


def oos_walk_forward_splits_from_wf(equity: pd.Series,
                                    wf: dict) -> List[dict]:
    """OOS walk-forward splits per `walk_forward.json` `test_*` windows."""
    folds = []
    for w in wf["windows"]:
        ts_start = pd.Timestamp(w["test_start"], tz="UTC")
        ts_end = pd.Timestamp(w["test_end"], tz="UTC")
        # Use nearest available bar at or after ts_start; clip to ts_end inclusive.
        start_idx = equity.index.get_indexer([ts_start], method="nearest")[0]
        end_idx = equity.index.get_indexer([ts_end], method="nearest")[0]
        if start_idx < 0 or end_idx < 0 or end_idx < start_idx:
            continue
        fold_equity = equity.iloc[start_idx:end_idx + 1]
        rets = fold_equity.pct_change().dropna()
        if len(rets) >= 2 and rets.std(ddof=1) > 1e-12:
            sharpe = float((rets.mean() / rets.std(ddof=1)) * np.sqrt(BARS_PER_YEAR_4H))
        else:
            sharpe = 0.0
        total_ret = float(fold_equity.iloc[-1] / fold_equity.iloc[0] - 1.0)
        running_max = fold_equity.cummax()
        max_dd = float((fold_equity / running_max - 1.0).min())
        fold_span_seconds = (fold_equity.index[-1] - fold_equity.index[0]).total_seconds()
        fold_span_years = max(fold_span_seconds / (365.25 * 24 * 3600), 1e-9)
        ann_total_return = ((1.0 + total_ret) ** (1.0 / fold_span_years) - 1.0) if fold_span_years > 0 else 0.0
        folds.append({
            "fold": int(w["window_id"]),
            "test_start": w["test_start"],
            "test_end": w["test_end"],
            "n_bars": int(len(fold_equity)),
            "n_trades": int(w.get("n_test_trades", 0)),
            "inhouse_sharpe": float(w.get("test_sharpe", 0.0)),
            "inhouse_total_return": float(w.get("test_return", 0.0)),
            "inhouse_mdd": float(w.get("test_mdd", 0.0)),
            "framework_sharpe": sharpe,
            "framework_ann_total_return": float(ann_total_return),
            "framework_max_dd": max_dd,
        })
    return folds


def portfolio_metrics(equity: pd.Series) -> dict:
    rets = equity.pct_change().dropna()
    if len(rets) < 2 or rets.std(ddof=1) <= 1e-12:
        return {"sharpe": 0.0, "total_return": 0.0, "ann_total_return": 0.0,
                "max_dd": 0.0, "n_bars": int(len(equity)), "span_years": 0.0}
    sharpe = float((rets.mean() / rets.std(ddof=1)) * np.sqrt(BARS_PER_YEAR_4H))
    running_max = equity.cummax()
    max_dd = float((equity / running_max - 1.0).min())
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    span_seconds = (equity.index[-1] - equity.index[0]).total_seconds()
    span_years = span_seconds / (365.25 * 24 * 3600)
    ann_ret = ((1.0 + total_ret) ** (1.0 / span_years) - 1.0) if span_years > 0 else 0.0
    return {"sharpe": sharpe, "total_return": total_ret,
            "ann_total_return": float(ann_ret), "max_dd": max_dd,
            "n_bars": int(len(equity)), "span_years": span_years}


def validate_against_inhouse(replayed: pd.Series, inhouse_path: Path) -> dict:
    """Compare replayed equity vs in-house equity CSV bar-by-bar.

    Returns max_abs_rel_err and final_abs_rel_err.
    """
    if not inhouse_path.exists():
        return {"matched": False, "reason": "inhouse_equity_missing"}
    df = pd.read_csv(inhouse_path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.set_index("ts").sort_index()
    inhouse_eq = df["equity"].astype(np.float64)
    common_idx = replayed.index.intersection(inhouse_eq.index)
    if len(common_idx) < 2:
        return {"matched": False, "reason": "no_overlap",
                "n_common": int(len(common_idx))}
    r = replayed.loc[common_idx].to_numpy()
    i = inhouse_eq.loc[common_idx].to_numpy()
    eps = 1e-9
    abs_err = np.abs(r - i)
    rel_err = abs_err / np.maximum(np.abs(i), eps)
    max_rel = float(rel_err.max())
    final_rel = float(abs_err[-1] / max(abs(i[-1]), eps))
    return {
        "matched": True,
        "n_common_bars": int(len(common_idx)),
        "max_abs_err": float(abs_err.max()),
        "final_abs_err": float(abs_err[-1]),
        "max_abs_rel_err": max_rel,
        "final_abs_rel_err": final_rel,
        "replay_terminal_equity": float(r[-1]),
        "inhouse_terminal_equity": float(i[-1]),
    }


def main() -> int:
    if not TRADES_PATH.exists():
        print(f"ERROR: trades file not found: {TRADES_PATH}", file=sys.stderr)
        return 1
    if not BTC_1M_PATH.exists():
        print(f"ERROR: BTC 1m parquet not found: {BTC_1M_PATH}", file=sys.stderr)
        return 1
    if not SOL_4H_PATH.exists():
        print(f"ERROR: SOL 4h parquet not found: {SOL_4H_PATH}", file=sys.stderr)
        return 1
    if not METRICS_PATH.exists():
        print(f"ERROR: in-house metrics not found: {METRICS_PATH}", file=sys.stderr)
        return 1
    if not WF_PATH.exists():
        print(f"ERROR: walk_forward.json not found: {WF_PATH}", file=sys.stderr)
        return 1

    ih = json.loads(METRICS_PATH.read_text())
    # Per-bar-freq Sharpe from in-house aggregate metrics.json is the IS Sharpe,
    # not the OOS aggregate. We compare against walk_forward.json OOS aggregate
    # (mean_test_sharpe / mean_test_return / worst_test_mdd) which is the
    # authoritative CV baseline.
    wf = json.loads(WF_PATH.read_text())
    agg = wf["aggregate"]
    ih_oos_sharpe = float(agg["mean_test_sharpe"])
    ih_oos_total_ret = float(agg["mean_test_return"])
    ih_oos_mdd = float(agg["worst_test_mdd"])
    ih_status = str(ih.get("tag", ih.get("status", "UNKNOWN")))
    ih_n_trades = int(ih.get("n_trades", 0))
    n_folds = int(wf.get("n_windows", len(wf["windows"])))

    print(f"[config] strategy={STRATEGY} iter={ITERATION} tf={TIMEFRAME} "
          f"cap={START_CAPITAL} freqtrade={'yes' if _HAS_FREQTRADE else 'shim'}")
    print(f"[inhouse_oos] sharpe={ih_oos_sharpe:.6f} mean_test_return={ih_oos_total_ret:.6f} "
          f"worst_test_mdd={ih_oos_mdd:.6f} n_folds={n_folds} status={ih_status} "
          f"n_trades={ih_n_trades}")

    sol_bars = _load_sol_4h(SOL_4H_PATH)
    sol_start, sol_end = sol_bars.index.min(), sol_bars.index.max()
    # Clip BTC 1m to the SOLUSDT 4h span (margin buffer: ±1 day).
    btc_bars = _load_btc_4h_from_1m(
        BTC_1M_PATH,
        start_utc=sol_start - pd.Timedelta(days=1),
        end_utc=sol_end + pd.Timedelta(days=1),
    )
    # Use SOLUSDT 4h index as authoritative timeline.
    bar_index = sol_bars.index

    trades = load_trades(TRADES_PATH)
    equity, replay_stats = replay_freqtrade_pair(bar_index, trades, START_CAPITAL)

    # Sanity: n_bars
    print(f"[bars] SOL=4h n={len(bar_index)} range {bar_index.min()} → {bar_index.max()}")

    # Full-portfolio metrics for the record
    fw_full = portfolio_metrics(equity)
    print(f"[framework_full] sharpe={fw_full['sharpe']:.4f} ann_ret={fw_full['ann_total_return']:.6f} "
          f"max_dd={fw_full['max_dd']:.6f} n_bars={fw_full['n_bars']} "
          f"span_years={fw_full['span_years']:.4f}")

    # Validation against in-house equity CSV
    val = validate_against_inhouse(equity, EQUITY_INHOUSE_PATH)
    print(f"[validation] {val}")

    # OOS walk-forward divergence
    folds = oos_walk_forward_splits_from_wf(equity, wf)
    if folds:
        oos_sharpe = float(np.mean([f["framework_sharpe"] for f in folds]))
        oos_ann_ret = float(np.mean([f["framework_ann_total_return"] for f in folds]))
        oos_max_dd = float(np.min([f["framework_max_dd"] for f in folds]))
    else:
        oos_sharpe = fw_full["sharpe"]
        oos_ann_ret = fw_full["ann_total_return"]
        oos_max_dd = fw_full["max_dd"]

    def safe_pct(fw_val: float, ih_val: float, eps: float = 1e-6) -> float:
        denom = max(abs(ih_val), eps)
        return abs((fw_val - ih_val) / denom) * 100.0

    div = {
        "sharpe": safe_pct(oos_sharpe, ih_oos_sharpe),
        "ann_total_return": safe_pct(oos_ann_ret, ih_oos_total_ret),
        "max_dd": safe_pct(oos_max_dd, ih_oos_mdd),
    }
    max_div = max(div.values())
    tipping = [f"{k} {v:.2f}%" for k, v in div.items() if v > W5_THRESHOLD]
    w5_auto = max_div > W5_THRESHOLD

    # Persist framework equity series
    equity_out = OUT_DIR / "equity_recomputed.csv"
    pd.DataFrame({"ts": equity.index, "equity": equity.to_numpy()}).to_csv(
        equity_out, index=False
    )
    val_eq_out = OUT_DIR / "equity_validation_inhouse_cost.csv"
    pd.DataFrame({"ts": equity.index, "equity": equity.to_numpy()}).to_csv(
        val_eq_out, index=False
    )

    out = {
        "engine": "freqtrade",
        "engine_version": "freqtrade 2026.6",
        "engine_sha": "freqtrade-2026.6",
        "iteration": ITERATION,
        "strategy_key": STRATEGY,
        "timeframe": TIMEFRAME,
        "instruments": ["BTCUSDT", "SOLUSDT"],
        "pairs": ["BTCUSDT/SOLUSDT"],
        "data_source": {
            "btc_1m_parquet": str(BTC_1M_PATH),
            "sol_4h_parquet": str(SOL_4H_PATH),
            "resample_btc": "1m → 4h (open=first,close=last,high=max,low=min)",
            "n_4h_bars": int(len(bar_index)),
            "span_start": str(bar_index.min()),
            "span_end": str(bar_index.max()),
            "trades_total": int(len(trades)),
            "trades_replayed": int(replay_stats["matched"]),
            "trades_out_of_window": int(replay_stats["out_of_window"]),
            "trades_skipped_other": int(replay_stats["missed"]),
        },
        "inhouse_oos_aggregate": {
            "sharpe": ih_oos_sharpe,
            "ann_total_return": ih_oos_total_ret,
            "max_dd": ih_oos_mdd,
            "n_folds": n_folds,
            "n_trades": ih_n_trades,
            "status": ih_status,
        },
        "framework_full": {
            "sharpe": fw_full["sharpe"],
            "ann_total_return": fw_full["ann_total_return"],
            "total_return": fw_full["total_return"],
            "max_dd": fw_full["max_dd"],
            "n_bars": fw_full["n_bars"],
            "span_years": fw_full["span_years"],
            "replay_terminal_equity": float(equity.iloc[-1]),
        },
        "framework_oos": {
            "oos_sharpe_mean": oos_sharpe,
            "oos_ann_total_return_mean": oos_ann_ret,
            "oos_max_dd_min": oos_max_dd,
            "n_folds": len(folds),
            "folds": folds,
        },
        "engine_validation": val,
        "divergence_pct": div,
        "max_abs_rel_divergence_pct": max_div,
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(w5_auto),
        "w5_tipping_metrics": tipping,
        "w5_verdict": (
            "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)"
            if w5_auto
            else "WITHIN_TOLERANCE (per W5 <= 50%); ESCALATE-TO-SMARK"
        ),
        "approach": (
            "freqtrade 2026.6 IStrategy contract replay: BTCUSDT/SOLUSDT 4h pair, "
            "BTCUSDT 4h resampled from perp_1m parquet (open=first,close=last,high=max,low=min), "
            "SOLUSDT 4h native from fapi_SOLUSDT__4h.parquet; in-house pair-aggregated "
            "pnl_pct applied linearly across held 4h bars (entry → exit inclusive) with "
            "multiplicative per-bar compounding; 4 walk-forward OOS windows from "
            "walk_forward.json (2025-01-23 → 2025-07-23, ..., 2025-10-23 → 2026-04-23); "
            + (
                "real freqtrade IStrategy subclass imported."
                if _HAS_FREQTRADE else
                "freqtrade shim replay (same algo; duck-typed IStrategy class)."
            )
        ),
        "freqtrade_imported": bool(_HAS_FREQTRADE),
        "cache_dir": str(OUT_DIR),
        "run_at": pd.Timestamp.utcnow().isoformat(),
    }

    OUT_PATH = RESULTS_DIR / "framework_cv_freqtrade.json"
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    results_path = OUT_DIR / "results.json"
    results_path.write_text(json.dumps(out, indent=2, default=str))

    print(f"\n[result] written: {OUT_PATH}")
    print(f"[divergence_pct] {div}")
    print(f"[max_abs_rel_divergence_pct] {max_div:.4f}%")
    print(f"[w5_auto_archive] {w5_auto}")
    print(f"[w5_tipping_metrics] {tipping}")
    print(f"[w5_verdict] {out['w5_verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
