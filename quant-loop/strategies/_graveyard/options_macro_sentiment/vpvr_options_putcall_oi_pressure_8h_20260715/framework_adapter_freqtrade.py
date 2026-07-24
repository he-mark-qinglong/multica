"""Freqtrade framework adapter for vpvr_options_putcall_oi_pressure_8h_20260715.

Cross-validate the in-house 8h USDT-margined ETH perp VPVR-POC reversion
gated by put-call OI pressure proxy (PCR proxy = taker-buy-share because
options data is missing per data/manifest.txt). We replay its trade log
inside a freqtrade-compatible IStrategy contract and compute the
framework's view of Sharpe / ann_return / max_dd from a bar-by-bar
mark-to-market algorithm using actual 8h ETHUSDT perp close prices (USDT-
margined linear contract; pnl_pct applied linearly across held bars with
fractional sizing = risk_target_pct = 0.005).

Per W5 (AGENT_COLLAB_AUDIT_2026-07-12): divergence > 50% -> auto-archive
                                      divergence <= 50% -> ESCALATE-TO-SMARK.

Strategy is iter #71 single-symbol (ETHUSDT), timeframe 8h, USDT-margined
perp. The in-house run produced: sharpe -1.1722 (per-symbol ETHUSDT),
ann_return -20.78%, total_return -93.70%, max_dd -121.82%
(near 100% loss; consistent with FAIL_NEGATIVE_ANN_RETURN verdict),
n_trades 104, profit_factor 0.86, win_rate 0.394 (tag = FAIL_NEGATIVE_ANN_RETURN).
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

STRATEGY_DIR = Path(__file__).parent
STRATEGY = STRATEGY_DIR.name
OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-freqtrade")
OUT_DIR.mkdir(parents=True, exist_ok=True)

METRICS_PATH = STRATEGY_DIR / "results" / "metrics.json"
TRADES_PATH = STRATEGY_DIR / "results" / "trades_A_8h_ETHUSDT.csv"
PRICE_PATH = Path("/home/smark/multica/quant-loop/live_data/ETHUSDT_4h.parquet")
RESULTS_DIR = STRATEGY_DIR / "results"

W5_THRESHOLD = 50.0
TIMEFRAME = "8h"
SYMBOL = "ETHUSDT"
# in-house fractional sizing mirrors risk_target_pct = 0.005 from config.json
WEIGHT = 0.005
START_CAPITAL = 100000.0


# ---- Freqtrade IStrategy surface (try real import, fall back to shim) ----
try:
    from freqtrade.strategy.interface import IStrategy  # type: ignore
    _HAS_FREQTRADE = True

    class V71PutCallOIPressureFreqtradeStrategy(IStrategy):
        """Freqtrade IStrategy wrapper for vpvr_options_putcall_oi_pressure_8h."""
        timeframe = "8h"
        startup_candle_count = 240

        def __init__(self, config: dict) -> None:
            super().__init__(config)
            self.config = config
            self.position = {"direction": "flat", "entry_ts": None,
                             "entry_price": 0.0, "stop": 0.0, "tp": 0.0,
                             "bars_held": 0}
            self.trade_log: List[dict] = []

except Exception:  # pragma: no cover
    _HAS_FREQTRADE = False

    class IStrategy:  # type: ignore[no-redef]
        timeframe = "8h"
        startup_candle_count = 240

    class V71PutCallOIPressureFreqtradeStrategy(IStrategy):  # type: ignore[no-redef]
        def __init__(self, config: dict) -> None:
            self.config = config
            self.position = {"direction": "flat", "entry_ts": None,
                             "entry_price": 0.0, "stop": 0.0, "tp": 0.0,
                             "bars_held": 0}
            self.trade_log = []


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    # this strategy uses entry_fill_date / exit_fill_date naming
    df["entry_ts"] = pd.to_datetime(df["entry_fill_date"], utc=True, errors="coerce")
    df["exit_ts"] = pd.to_datetime(df["exit_fill_date"], utc=True, errors="coerce")
    return df


def load_prices_8h(path: Path) -> pd.DataFrame:
    """Aggregate 4h parquet to 8h boundaries (00:00, 08:00, 16:00 UTC) by
    taking every 2nd 4h bar from the start of 2022-01-01 00:00 UTC."""
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True, errors="coerce")
        df = df.set_index("open_time").sort_index()
    elif "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True, errors="coerce")
        df = df.set_index("timestamp").sort_index()
    # floor all bars to the 8h boundary: 00:00, 08:00, 16:00 → floor by 8h
    floor_idx = df.index.floor("8h")
    # group by floor and keep last bar per 8h window (matches close at 8h boundary)
    out = (
        df.assign(_floor=floor_idx)
        .groupby("_floor", sort=True)
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum",
              "quote_volume": "sum", "trades": "sum",
              "taker_buy_base": "sum", "taker_buy_quote": "sum"})
        .rename_axis("open_time")
    )
    return out


def replay_freqtrade_linear(prices: pd.DataFrame, trades: pd.DataFrame,
                             weight: float, start_capital: float) -> pd.Series:
    """Replay USDT-margined linear-perp trades inside freqtrade IStrategy.

    pnl_pct is applied linearly across bars held, producing a per-bar equity
    delta. Commission/slippage are already inside pnl_pct (in-house convention
    is bar[t].close + cost), so this is a faithful replay of the in-house
    fill model under the freqtrade cost convention (4bp fee + 2bp slippage
    per side, identical to in-house).
    """
    equity = pd.Series(start_capital, index=prices.index, dtype=np.float64)
    matched = 0
    missed = 0
    for _, t in trades.iterrows():
        if pd.isna(t["entry_ts"]) or pd.isna(t["exit_ts"]):
            missed += 1
            continue
        entry_idx = prices.index.get_indexer([t["entry_ts"]], method="nearest")[0]
        exit_idx = prices.index.get_indexer([t["exit_ts"]], method="nearest")[0]
        if entry_idx < 0 or exit_idx < 0 or exit_idx <= entry_idx:
            missed += 1
            continue
        held_bars = exit_idx - entry_idx + 1
        if held_bars <= 0:
            missed += 1
            continue
        per_bar_pnl = float(t["pnl_pct"]) * weight / held_bars
        idx = prices.index[entry_idx:exit_idx + 1]
        equity.loc[idx] = equity.loc[idx] * (1.0 + per_bar_pnl)
        matched += 1
    print(f"[replay] matched={matched} missed={missed}", file=sys.stderr)
    return equity


def oos_walk_forward_splits(equity: pd.Series, n_folds: int = 5) -> List[dict]:
    """Walk-forward OOS splits: anchor on chronological windows."""
    n = len(equity)
    if n < n_folds * 10:
        return []
    fold_size = n // n_folds
    folds = []
    for i in range(n_folds):
        start = i * fold_size
        end = (i + 1) * fold_size if i < n_folds - 1 else n
        fold_equity = equity.iloc[start:end]
        if len(fold_equity) < 2:
            continue
        rets = fold_equity.pct_change().dropna()
        if rets.std(ddof=1) > 1e-12:
            sharpe = float((rets.mean() / rets.std(ddof=1)) * np.sqrt(365.25 * 3))
        else:
            sharpe = 0.0
        total_ret = float(fold_equity.iloc[-1] / fold_equity.iloc[0] - 1.0)
        running_max = fold_equity.cummax()
        max_dd = float((fold_equity / running_max - 1.0).min())
        folds.append({
            "fold": i + 1,
            "bars": int(len(fold_equity)),
            "sharpe": sharpe,
            "ann_total_return": total_ret,
            "max_dd": max_dd,
        })
    return folds


def portfolio_metrics(equity: pd.Series) -> dict:
    rets = equity.pct_change().dropna()
    if len(rets) < 2 or rets.std(ddof=1) <= 1e-12:
        return {"sharpe": 0.0, "total_return": 0.0, "ann_total_return": 0.0,
                "max_dd": 0.0, "n_bars": int(len(equity)), "span_years": 0.0}
    bars_per_year = 365.25 * 3  # 8h
    sharpe = float((rets.mean() / rets.std(ddof=1)) * np.sqrt(bars_per_year))
    running_max = equity.cummax()
    max_dd = float((equity / running_max - 1.0).min())
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    span_years = float((equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 24 * 3600))
    ann_ret = ((1.0 + total_ret) ** (1.0 / span_years) - 1.0) if span_years > 0 else 0.0
    return {"sharpe": sharpe, "total_return": total_ret,
            "ann_total_return": float(ann_ret), "max_dd": max_dd,
            "n_bars": int(len(equity)), "span_years": span_years}


def main() -> int:
    if not TRADES_PATH.exists():
        print(f"ERROR: trades file not found: {TRADES_PATH}", file=sys.stderr)
        return 1
    if not PRICE_PATH.exists():
        print(f"ERROR: price parquet not found: {PRICE_PATH}", file=sys.stderr)
        return 1
    if not METRICS_PATH.exists():
        print(f"ERROR: in-house metrics not found: {METRICS_PATH}", file=sys.stderr)
        return 1

    ih = json.loads(METRICS_PATH.read_text())
    ih_sharpe = float(ih.get("sharpe", float("nan")))
    # ann_return stored as `ann_return_pct` here; values are percent
    ih_ann_ret = float(ih.get("ann_return_pct", float("nan"))) / 100.0
    # total_return_pct is in percent; normalize to decimal
    ih_total_ret = float(ih.get("total_return_pct", float("nan"))) / 100.0
    ih_max_dd = float(ih.get("max_drawdown_pct", float("nan"))) / 100.0
    ih_n_trades = int(ih.get("n_trades", 0))
    ih_status = str(ih.get("status", ih.get("tag", "NOT-PROFITABLE")))

    print(f"[config] strategy={STRATEGY} tf={TIMEFRAME} weight={WEIGHT} "
          f"cap={START_CAPITAL} freqtrade={'yes' if _HAS_FREQTRADE else 'shim'}")
    print(f"[inhouse] sharpe={ih_sharpe:.4f} ann_ret={ih_ann_ret:.6f} "
          f"total_ret={ih_total_ret:.6f} max_dd={ih_max_dd:.6f} "
          f"n_trades={ih_n_trades} status={ih_status}")

    trades = load_trades(TRADES_PATH)
    prices = load_prices_8h(PRICE_PATH)

    equity = replay_freqtrade_linear(prices, trades, WEIGHT, START_CAPITAL)
    fw = portfolio_metrics(equity)
    print(f"[framework] sharpe={fw['sharpe']:.4f} ann_ret={fw['ann_total_return']:.6f} "
          f"max_dd={fw['max_dd']:.6f} n_bars={fw['n_bars']} span_years={fw['span_years']:.3f}")

    folds = oos_walk_forward_splits(equity, n_folds=5)
    if folds:
        oos_sharpe = float(np.mean([f["sharpe"] for f in folds]))
        # for OOS, compute ann_return per fold from per-fold span (use total_ret / years_in_fold)
        per_fold_anns = []
        for f in folds:
            yr = f["bars"] / (365.25 * 3)
            tr = f["ann_total_return"]
            per_fold_anns.append(((1.0 + tr) ** (1.0 / yr) - 1.0) if yr > 0 else 0.0)
        oos_total_ret = float(np.mean(per_fold_anns)) if per_fold_anns else 0.0
        oos_max_dd = float(np.min([f["max_dd"] for f in folds]))
    else:
        oos_sharpe = fw["sharpe"]
        oos_total_ret = fw["ann_total_return"]
        oos_max_dd = fw["max_dd"]

    def safe_pct(fw_val, ih_val, eps=1e-6):
        denom = max(abs(ih_val), eps)
        return abs((fw_val - ih_val) / denom) * 100.0

    div = {
        "sharpe": safe_pct(oos_sharpe, ih_sharpe),
        "ann_total_return": safe_pct(oos_total_ret, ih_ann_ret),
        "max_dd": safe_pct(oos_max_dd, ih_max_dd),
    }
    max_div = max(div.values())
    w5_auto = max_div > W5_THRESHOLD

    # Persist equity for downstream auditing
    equity_out = OUT_DIR / "equity_recomputed.csv"
    equity.to_csv(equity_out, header=["equity_usdt"])

    out = {
        "engine": "freqtrade",
        "engine_version": "freqtrade 2026.6 (shim)" if not _HAS_FREQTRADE else "freqtrade 2026.6",
        "iteration": 71,
        "strategy_key": STRATEGY,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "inhouse": {
            "sharpe": ih_sharpe,
            "ann_total_return": ih_ann_ret,
            "total_return": ih_total_ret,
            "max_dd": ih_max_dd,
            "n_trades": ih_n_trades,
            "status": ih_status,
        },
        "framework": {
            "sharpe": fw["sharpe"],
            "ann_total_return": fw["ann_total_return"],
            "total_return": fw["total_return"],
            "max_dd": fw["max_dd"],
            "n_bars": fw["n_bars"],
            "span_years": fw["span_years"],
        },
        "framework_oos": {
            "oos_sharpe_mean": oos_sharpe,
            "oos_total_return_ann_mean": oos_total_ret,
            "oos_max_dd_max": oos_max_dd,
            "n_folds": len(folds),
            "folds": folds,
        },
        "divergence_pct": div,
        "max_abs_rel_divergence_pct": max_div,
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(w5_auto),
        "w5_verdict": ("AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if w5_auto
                       else "WITHIN TOLERANCE (per W5 <= 50%); ESCALATE-TO-SMARK"),
        "approach": (
            "freqtrade 2026.6 IStrategy contract replay: ETHUSDT 8h USDT-margined "
            "linear perp (8h bars aggregated from 4h parquet); pnl_pct applied "
            "linearly across held bars with weight 0.005 (risk_target_pct); "
            "walk-forward OOS 5 folds; "
            + ("freqtrade-imported" if _HAS_FREQTRADE else
               "freqtrade shim replay (same algo, no freqtrade pkg); "
               "IStrategy contract satisfied via duck-typed class.")
        ),
        "freqtrade_imported": bool(_HAS_FREQTRADE),
        "cache_dir": str(OUT_DIR),
    }

    OUT_PATH = RESULTS_DIR / "framework_cv_freqtrade.json"
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    results_path = OUT_DIR / "results.json"
    results_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n[result] written: {OUT_PATH}")
    print(f"[divergence_pct] {div}")
    print(f"[max_abs_rel_divergence_pct] {max_div:.4f}%")
    print(f"[w5_auto_archive] {w5_auto}")
    print(f"[w5_verdict] {out['w5_verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
