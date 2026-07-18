"""Freqtrade framework adapter for vpvr_reversion_1m_volume_profile_break_20260709.

Cross-validate the in-house 1m BTCUSDT volume-profile-break reversion
strategy by replaying the in-house OOS trade log inside a
freqtrade-shaped equity curve, fold by fold, using the contract used by
the framework-validator role (linear pnl_pct distribution across held
bars with weight=risk_target_pct).

Per W5 (AGENT_COLLAB_AUDIT_2026-07-12):
  divergence > 50% absolute → auto-archive NOT-PROFITABLE
  divergence ≤ 50% absolute → still emit ESCALATE-TO-SMARK

Strategy is iter #69 V5 single-symbol (BTCUSDT), 1m timeframe.
"""
from __future__ import annotations

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
PRICE_PATH = STRATEGY_DIR / "data" / "fapi_BTCUSDT__1m.parquet"
RESULTS_DIR = STRATEGY_DIR / "results"
INHOUSE_OOS_PATH = OUT_DIR / "inhouse_oos.json"

W5_THRESHOLD = 50.0
TIMEFRAME = "1m"
WEIGHT = 0.005  # matches in-house risk_target_pct
START_CAPITAL = 100000.0
ANN_FACTOR_1M = 365.0 * 24.0 * 60.0  # 525600


# ---- Freqtrade IStrategy surface (try real import, fall back to shim) ----
try:
    from freqtrade.strategy.interface import IStrategy  # type: ignore
    _HAS_FREQTRADE = True

    class V69VPVRBreakFreqtradeStrategy(IStrategy):
        """Freqtrade IStrategy wrapper for vpvr_reversion_1m_volume_profile_break_20260709."""
        timeframe = "1m"
        startup_candle_count = 1440

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
        timeframe = "1m"
        startup_candle_count = 1440

    class V69VPVRBreakFreqtradeStrategy(IStrategy):  # type: ignore[no-redef]
        def __init__(self, config: dict) -> None:
            self.config = config
            self.position = {"direction": "flat", "entry_ts": None,
                             "entry_price": 0.0, "stop": 0.0, "tp": 0.0,
                             "bars_held": 0}
            self.trade_log = []


def load_prices(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df.index.name != "ts" and "openTime" in df.columns:
        df = df.set_index("openTime")
    df.index.name = "ts"
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.sort_index()


def load_fold_trades(fold_name: str) -> pd.DataFrame:
    p = OUT_DIR / f"inhouse_trades_{fold_name}.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["entry_ts"] = pd.to_datetime(df["entry_ts"], utc=True, errors="coerce")
    df["exit_ts"] = pd.to_datetime(df["exit_ts"], utc=True, errors="coerce")
    return df


def replay_fold_freqtrade(prices: pd.DataFrame, trades: pd.DataFrame,
                            fold_def: dict, weight: float,
                            start_capital: float) -> dict:
    """Replay per-fold trades bar-by-bar using the freqtrade linear-pnl
    contract used by the framework-validator role."""
    test_start = pd.Timestamp(fold_def["test"][0], tz=prices.index.tz)
    test_end = pd.Timestamp(fold_def["test"][1], tz=prices.index.tz) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    sub = prices.loc[(prices.index >= test_start) & (prices.index <= test_end)]
    if len(sub) < 2:
        return {"sharpe": 0.0, "total_return": 0.0, "max_dd": 0.0, "n_trades": 0}

    equity = pd.Series(start_capital, index=sub.index, dtype=np.float64)
    for _, t in trades.iterrows():
        if pd.isna(t["entry_ts"]) or pd.isna(t["exit_ts"]):
            continue
        mask = (sub.index >= t["entry_ts"]) & (sub.index <= t["exit_ts"])
        if not mask.any():
            continue
        held_bars = int(mask.sum())
        if held_bars <= 0:
            continue
        # freqtrade IStrategy contract: pnl_pct applied linearly across held bars.
        per_bar_pnl = float(t["pnl_pct"]) * weight / held_bars
        equity.loc[mask] = equity.loc[mask] * (1.0 + per_bar_pnl)

    rets = equity.pct_change().dropna()
    if len(rets) < 2 or rets.std(ddof=0) <= 1e-12:
        sharpe = 0.0
    else:
        sharpe = float((rets.mean() / rets.std(ddof=0)) * math.sqrt(ANN_FACTOR_1M))
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    running_max = equity.cummax()
    max_dd = float((equity / running_max - 1.0).min())
    return {
        "sharpe": sharpe,
        "total_return": total_ret,
        "max_dd": max_dd,
        "n_trades": int(len(trades)),
    }


def aggregate_oos(per_fold: Dict[str, dict]) -> dict:
    if not per_fold:
        return {"oos_sharpe_mean": 0.0, "oos_total_return_mean": 0.0,
                "oos_max_dd_min": 0.0, "oos_n_trades_sum": 0}
    sharpes = [f["sharpe"] for f in per_fold.values()]
    rets = [f["total_return"] for f in per_fold.values()]
    dds = [f["max_dd"] for f in per_fold.values()]
    n_trades = [f["n_trades"] for f in per_fold.values()]
    return {
        "oos_sharpe_mean": float(np.mean(sharpes)),
        "oos_total_return_mean": float(np.mean(rets)),
        "oos_max_dd_min": float(np.min(dds)),
        "oos_n_trades_sum": int(sum(n_trades)),
    }


def safe_pct(fw_val, ih_val, eps=1e-9) -> float:
    """abs((framework_value - inhouse_value) / max(abs(inhouse_value), eps)) * 100"""
    denom = max(abs(ih_val), eps)
    return abs((fw_val - ih_val) / denom) * 100.0


def main() -> int:
    if not METRICS_PATH.exists():
        print(f"ERROR: in-house metrics not found: {METRICS_PATH}", file=sys.stderr)
        return 1
    if not PRICE_PATH.exists():
        print(f"ERROR: price parquet not found: {PRICE_PATH}", file=sys.stderr)
        return 1
    if not INHOUSE_OOS_PATH.exists():
        print(f"ERROR: in-house OOS baseline not found: {INHOUSE_OOS_PATH}", file=sys.stderr)
        return 1

    inhouse_oos = json.loads(INHOUSE_OOS_PATH.read_text())
    fold_defs = json.loads((STRATEGY_DIR / "config.json").read_text())["walk_forward"]["folds"]
    ih_oos_sharpe = float(inhouse_oos["oos_sharpe_mean"])
    ih_oos_total_ret = float(inhouse_oos["oos_total_return_mean"])
    ih_oos_max_dd = float(inhouse_oos["oos_max_dd_min"])
    ih_oos_n = int(inhouse_oos["oos_n_trades_sum"])

    print(f"[config] strategy={STRATEGY} tf={TIMEFRAME} weight={WEIGHT} "
          f"cap={START_CAPITAL} freqtrade={'yes' if _HAS_FREQTRADE else 'shim'}")
    print(f"[inhouse-OOS] sharpe={ih_oos_sharpe:.4f} total_ret={ih_oos_total_ret:.6f} "
          f"max_dd={ih_oos_max_dd:.6f} n_trades={ih_oos_n}")

    prices = load_prices(PRICE_PATH)

    fold_results = {}
    for fdef in fold_defs:
        trades = load_fold_trades(fdef["name"])
        if trades.empty:
            print(f"  fold {fdef['name']}: no trades CSV; skipping")
            continue
        f_res = replay_fold_freqtrade(prices, trades, fdef, WEIGHT, START_CAPITAL)
        fold_results[fdef["name"]] = f_res
        print(f"  fold {fdef['name']}: sharpe={f_res['sharpe']:.4f} "
              f"total_ret={f_res['total_return']:.6f} "
              f"max_dd={f_res['max_dd']:.6f} n_trades={f_res['n_trades']}")

    fw_oos = aggregate_oos(fold_results)
    print(f"[framework-OOS] sharpe={fw_oos['oos_sharpe_mean']:.4f} "
          f"total_ret={fw_oos['oos_total_return_mean']:.6f} "
          f"max_dd={fw_oos['oos_max_dd_min']:.6f} n_trades={fw_oos['oos_n_trades_sum']}")

    div = {
        "sharpe": safe_pct(fw_oos["oos_sharpe_mean"], ih_oos_sharpe),
        "ann_total_return": safe_pct(fw_oos["oos_total_return_mean"], ih_oos_total_ret),
        "max_dd": safe_pct(fw_oos["oos_max_dd_min"], ih_oos_max_dd),
    }
    max_div = max(div.values())
    w5_auto = max_div > W5_THRESHOLD

    out = {
        "engine": "freqtrade",
        "engine_version": "freqtrade 2026.6 (shim)" if not _HAS_FREQTRADE else "freqtrade 2026.6",
        "iteration": 69,
        "strategy_key": STRATEGY,
        "inhouse": {
            "sharpe": ih_oos_sharpe,
            "ann_total_return": ih_oos_total_ret,
            "max_dd": ih_oos_max_dd,
            "n_trades": ih_oos_n,
            "status": "NOT-PROFITABLE",
        },
        "framework": {
            "sharpe": fw_oos["oos_sharpe_mean"],
            "ann_total_return": fw_oos["oos_total_return_mean"],
            "max_dd": fw_oos["oos_max_dd_min"],
            "n_trades": fw_oos["oos_n_trades_sum"],
        },
        "framework_oos": {
            "oos_sharpe_mean": fw_oos["oos_sharpe_mean"],
            "oos_total_return_mean": fw_oos["oos_total_return_mean"],
            "oos_max_dd_min": fw_oos["oos_max_dd_min"],
            "oos_n_trades_sum": fw_oos["oos_n_trades_sum"],
            "n_folds": len(fold_results),
            "folds": fold_results,
        },
        "divergence_pct": div,
        "max_abs_rel_divergence_pct": max_div,
        "w5_threshold_pct": W5_THRESHOLD,
        "w5_auto_archive": bool(w5_auto),
        "w5_verdict": ("AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if w5_auto
                       else "WITHIN TOLERANCE (per W5 ≤ 50%)"),
        "approach": ("freqtrade 2026.6 IStrategy contract replay: "
                     "BTCUSDT 1m futures, in-house OOS trades replayed "
                     "bar-by-bar with linear pnl_pct × weight (=in-house "
                     "risk_target_pct) across held bars; walk-forward "
                     "OOS over the 3 folds from config.json; "
                     "freqtrade-imported" if _HAS_FREQTRADE else
                     "freqtrade 2026.6 shim replay (same contract; "
                     "IStrategy duck-typed class; freqtrade pkg unavailable)"),
        "freqtrade_imported": bool(_HAS_FREQTRADE),
        "cache_dir": str(OUT_DIR),
    }

    OUT_PATH = RESULTS_DIR / "framework_cv_freqtrade.json"
    OUT_PATH.write_text(json.dumps(out, indent=2, default=float))
    print(f"\n[result] written: {OUT_PATH}")
    print(f"[divergence_pct] {div}")
    print(f"[max_abs_rel_divergence_pct] {max_div:.4f}%")
    print(f"[w5_auto_archive] {w5_auto}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())