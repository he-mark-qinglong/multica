"""Freqtrade framework adapter for vpvr_xs_pairs_4h_zscore_vpvr_20260710 (V3).

Wraps the 3-pair (BTC/ETH/SOL) xs-pair z-score + VPVR confluence strategy
inside a freqtrade IStrategy contract and reports the OOS walk-forward
Sharpe / total_return / max_dd for ship-gate 5 (G5).

Pattern mirrors pairs_cointegration_1d_20260709 framework_adapter_freqtrade
plus vpvr_mtf_reversion_5m_consensus_20260710 pattern: deterministic
shim if freqtrade unavailable; trade CSV replay against B3 fold
boundaries; output divergence_pct_oos vs inhouse_oos_walk_forward.

Per W5 of AGENT_COLLAB_AUDIT_2026-07-12, this run records:
  framework_oos_sharpe / framework_oos_total_return / framework_oos_max_dd
and the in-house equivalents, so the autopilot can compute divergence and
trigger W5 auto-archive if any abs rel divergence > 50%.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"
OUT_PATH = RESULTS_DIR / "framework_cv_freqtrade.json"
WF_PATH = RESULTS_DIR / "walk_forward.json"
METRICS_PATH = RESULTS_DIR / "metrics.json"


# Try to import freqtrade; fall back to a lightweight shim.
try:
    from freqtrade.strategy.interface import IStrategy  # type: ignore
    _HAS_FREQTRADE = True
except Exception:
    _HAS_FREQTRADE = False

    class IStrategy:  # type: ignore[no-redef]
        """Minimal freqtrade-compatible shim used when freqtrade is
        unavailable in the runtime."""

        timeframe = "4h"
        startup_candle_count = 60

        def __init__(self) -> None:
            self.custom_state: Dict[str, object] = {}


@dataclass
class ZscoreVpvrFreqtradePosition:
    """One running pair-position inside the freqtrade strategy."""
    pair: str = ""
    direction: str = "flat"
    entry_ts: Optional[pd.Timestamp] = None
    entry_price_a: float = 0.0
    entry_price_b: float = 0.0
    exit_ts: Optional[pd.Timestamp] = None
    exit_price_a: float = 0.0
    exit_price_b: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0


class ZscoreVpvrFreqtradeStrategy(IStrategy):
    """Freqtrade IStrategy wrapper for the 3-pair xs-zscore+VPVR strategy."""

    timeframe = "4h"
    startup_candle_count = 60

    def __init__(self, config: dict) -> None:
        if _HAS_FREQTRADE:
            super().__init__(config)
        else:
            IStrategy.__init__(self)
        self.config = config
        self.positions: List[ZscoreVpvrFreqtradePosition] = []
        self.trade_log: List[dict] = []

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Pairs strategy reads pre-computed z-scores + VPVR confluence
        # from the canonical pipeline; no inline indicator calc here.
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        trades = self._load_all_trades()
        enter_mask = pd.Series(False, index=dataframe.index)
        if not trades:
            return dataframe.assign(enter_long=enter_mask, exit_long=enter_mask.copy())
        for t in trades:
            ts = pd.Timestamp(t["entry_ts"])
            if ts in dataframe.index:
                enter_mask.loc[ts] = True
        return dataframe.assign(enter_long=enter_mask, exit_long=enter_mask.copy())

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        trades = self._load_all_trades()
        exit_mask = pd.Series(False, index=dataframe.index)
        if not trades:
            return dataframe.assign(exit_long=exit_mask)
        for t in trades:
            ts = pd.Timestamp(t["exit_ts"])
            if ts in dataframe.index:
                exit_mask.loc[ts] = True
        return dataframe.assign(exit_long=exit_mask)

    def _load_all_trades(self) -> List[dict]:
        """Load trades from each pair's CSV and concatenate."""
        all_trades: List[dict] = []
        for pair_csv in sorted(RESULTS_DIR.glob("trades_A_iter75_*.csv")):
            if "summary" in pair_csv.name:
                continue
            with open(pair_csv) as f:
                rows = [row for row in csv.DictReader(f)]
                all_trades.extend(rows)
        return all_trades


def replay_walk_forward_oos(trades: List[dict], wf_path: Path) -> List[dict]:
    """Replay trades against B3's actual walk-forward test_windows.

    Reads ``results/walk_forward.json`` to use the exact OOS fold
    boundaries the in-house backtest emitted, then computes per-fold OOS
    Sharpe inside the freqtrade engine contract.

    4h timeframe → annualisation = sqrt(365 * 6) = sqrt(2190) ≈ 46.8
    """
    if not wf_path.exists():
        return []

    wf = json.loads(wf_path.read_text())
    folds_meta = wf.get("windows", [])

    # 4h bars per year: 365 d × 6 bars/day = 2190
    bars_per_year = 2190

    folds = []
    for fm in folds_meta:
        test_window = [fm.get("test_start"), fm.get("test_end")]
        if not test_window[0] or not test_window[1]:
            folds.append({
                "fold": fm.get("window_id"),
                "n_trades": 0,
                "oos_sharpe": 0.0,
                "oos_return_pct": 0.0,
                "oos_max_dd_pct": 0.0,
            })
            continue
        oos_start = pd.Timestamp(test_window[0])
        oos_end = pd.Timestamp(test_window[1])

        def _ts(row_ts: str) -> pd.Timestamp:
            t = pd.Timestamp(row_ts)
            if t.tz is not None:
                t = t.tz_convert(None)
            return t

        oos_trades = [
            t for t in trades
            if oos_start <= _ts(t["entry_ts"]) <= oos_end
        ]
        rets = [float(t.get("pnl_pct", 0.0)) for t in oos_trades]
        if not rets:
            folds.append({
                "fold": fm.get("window_id"),
                "oos_window": test_window,
                "n_trades": 0,
                "oos_sharpe": 0.0,
                "oos_return_pct": 0.0,
                "oos_max_dd_pct": 0.0,
            })
            continue
        mean = sum(rets) / len(rets)
        sd = (sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)) ** 0.5
        sharpe = (mean / sd) * math.sqrt(bars_per_year) if sd > 0 else 0.0
        total_return = float(np.prod([1.0 + r for r in rets]) - 1.0)
        cum = np.cumprod([1.0 + r for r in rets])
        peaks = np.maximum.accumulate(cum)
        drawdowns = (cum - peaks) / peaks
        max_dd = float(drawdowns.min()) if len(drawdowns) > 0 else 0.0
        folds.append({
            "fold": fm.get("window_id"),
            "oos_window": test_window,
            "n_trades": len(rets),
            "oos_sharpe": sharpe,
            "oos_return_pct": total_return,
            "oos_max_dd_pct": max_dd,
        })
    return folds


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    strategy = ZscoreVpvrFreqtradeStrategy(cfg)

    # Load all 3 pair trades CSVs
    trades = strategy._load_all_trades()
    n_trades_total = len(trades)

    # Compute OOS walk-forward metrics using B3 folds
    folds = replay_walk_forward_oos(trades, WF_PATH)

    # Aggregate per-metric means across folds
    sharpe_values = [f["oos_sharpe"] for f in folds if f.get("n_trades", 0) > 0]
    return_values = [f["oos_return_pct"] for f in folds if f.get("n_trades", 0) > 0]
    maxdd_values = [f["oos_max_dd_pct"] for f in folds if f.get("n_trades", 0) > 0]

    sharpe_mean = float(np.mean(sharpe_values)) if sharpe_values else 0.0
    return_mean = float(np.mean(return_values)) if return_values else 0.0
    maxdd_worst = float(min(maxdd_values)) if maxdd_values else 0.0

    # In-house metrics from metrics.json (full sample, for divergence reference)
    inhouse_metrics: Dict[str, float] = {}
    if METRICS_PATH.exists():
        m = json.loads(METRICS_PATH.read_text())
        # map top-level keys (this strategy uses sharpe / total_return_pct / max_drawdown_pct)
        inhouse_metrics = {
            "sharpe": float(m.get("sharpe", 0.0)),
            "total_return_pct": float(m.get("total_return_pct", m.get("annualized_pct", 0.0))),
            "max_drawdown_pct": float(m.get("max_drawdown_pct", m.get("max_dd", 0.0))),
            "n_trades": int(m.get("n_trades", 0)),
            "profit_factor": float(m.get("profit_factor", 0.0)),
        }

    # Per-fold in-house OOS walk-forward (from walk_forward.json windows)
    inhouse_window_sharpes: List[float] = []
    inhouse_window_returns: List[float] = []
    inhouse_window_mdds: List[float] = []
    wf_oos_aggregate: Dict[str, float] = {}
    if WF_PATH.exists():
        wf = json.loads(WF_PATH.read_text())
        wf_oos_aggregate = wf.get("aggregate", {})
        inhouse_window_sharpes = [
            float(w.get("test_sharpe", 0.0)) for w in wf.get("windows", [])
        ]
        inhouse_window_returns = [
            float(w.get("test_return", w.get("test_return_pct", 0.0))) for w in wf.get("windows", [])
        ]
        inhouse_window_mdds = [
            float(w.get("test_mdd", w.get("test_max_dd_pct", 0.0))) for w in wf.get("windows", [])
        ]

    inhouse_oos_sharpe_mean = float(np.mean(inhouse_window_sharpes)) if inhouse_window_sharpes else 0.0
    inhouse_oos_return_mean = float(np.mean(inhouse_window_returns)) if inhouse_window_returns else 0.0
    inhouse_oos_mdd_worst = float(min(inhouse_window_mdds)) if inhouse_window_mdds else 0.0

    inhouse_sharpe_full = inhouse_metrics.get("sharpe", 0.0)
    inhouse_total_return_full = inhouse_metrics.get("total_return_pct", 0.0)
    inhouse_mdd_full = inhouse_metrics.get("max_drawdown_pct", 0.0)

    # W5 divergence:
    eps = 1e-6
    sharpe_div_oos = abs(sharpe_mean - inhouse_oos_sharpe_mean) / max(abs(inhouse_oos_sharpe_mean), eps)
    ret_div_oos = abs(return_mean - inhouse_oos_return_mean) / max(abs(inhouse_oos_return_mean), eps)
    mdd_div_oos = abs(maxdd_worst - inhouse_oos_mdd_worst) / max(abs(inhouse_oos_mdd_worst), eps)
    max_div_oos = max(sharpe_div_oos, ret_div_oos, mdd_div_oos)

    sharpe_div_full = abs(sharpe_mean - inhouse_sharpe_full) / max(abs(inhouse_sharpe_full), eps)
    ret_div_full = abs(return_mean - inhouse_total_return_full) / max(abs(inhouse_total_return_full), eps)
    mdd_div_full = abs(maxdd_worst - inhouse_mdd_full) / max(abs(inhouse_mdd_full), eps)
    max_div_full = max(sharpe_div_full, ret_div_full, mdd_div_full)

    W5_THRESHOLD_PCT = 50.0
    w5_auto_archive = (max_div_oos * 100.0) > W5_THRESHOLD_PCT
    w5_verdict = (
        "AUTO-ARCHIVE per W5 (NOT-PROFITABLE)" if w5_auto_archive
        else "WITHIN_TOLERANCE (OOS walk-forward agreement)"
    )

    out = {
        "engine": "freqtrade",
        "engine_available": _HAS_FREQTRADE,
        "variant": cfg.get("strategy"),
        "iteration": cfg.get("iteration"),
        "timeframe": cfg.get("timeframe"),
        "n_pairs": len(cfg.get("pairs", [])),
        "n_trades_total": n_trades_total,
        "n_folds": len(folds),
        "folds": folds,
        # Framework OOS (per fold → mean across folds)
        "oos_sharpe_mean": sharpe_mean,
        "oos_sharpe_median": float(np.median(sharpe_values)) if sharpe_values else 0.0,
        "oos_sharpe_min": float(np.min(sharpe_values)) if sharpe_values else 0.0,
        "oos_total_return_mean": return_mean,
        "oos_max_dd_max": maxdd_worst,
        # In-house OOS walk-forward comparison
        "inhouse_oos_sharpe_mean": inhouse_oos_sharpe_mean,
        "inhouse_oos_return_mean": inhouse_oos_return_mean,
        "inhouse_oos_mdd_worst": inhouse_oos_mdd_worst,
        "inhouse_oos_walkforward_aggregate": wf_oos_aggregate,
        # In-house full-period reference
        "inhouse_sharpe_full": inhouse_sharpe_full,
        "inhouse_total_return_full": inhouse_total_return_full,
        "inhouse_mdd_full": inhouse_mdd_full,
        # W5 divergence
        "divergence_pct_oos": {
            "sharpe": float(sharpe_div_oos) * 100.0,
            "total_return": float(ret_div_oos) * 100.0,
            "max_dd": float(mdd_div_oos) * 100.0,
        },
        "max_abs_rel_divergence_pct_oos": float(max_div_oos) * 100.0,
        "divergence_pct_full_period": {
            "sharpe": float(sharpe_div_full) * 100.0,
            "total_return": float(ret_div_full) * 100.0,
            "max_dd": float(mdd_div_full) * 100.0,
        },
        "max_abs_rel_divergence_pct_full_period": float(max_div_full) * 100.0,
        # G5 / W5 verdicts
        "w5_threshold_pct": W5_THRESHOLD_PCT,
        "w5_auto_archive": bool(w5_auto_archive),
        "w5_verdict": w5_verdict,
        "g5_threshold": 1.0,
        "g5_passed": sharpe_mean >= 1.0,
        "approach": (
            "freqtrade IStrategy contract replay — closed-pair trades loaded from B3 trades CSVs "
            "(trades_A_iter75_BTCUSDT_ETHUSDT.csv, trades_A_iter75_BTCUSDT_SOLUSDT.csv, "
            "trades_A_iter75_ETHUSDT_SOLUSDT.csv), PnL per fold mapped to walk_forward.json test "
            "windows, framework OOS Sharpe/return/max_dd computed via numpy across folds (4h "
            "timeframe, annualisation=sqrt(2190)). Compare against inhouse OOS walk-forward mean."
        ),
    }
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
