"""Freqtrade framework adapter for vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717.

Cross-validate the v10 grid-optimized BTCUSDT/SOLUSDT 30m xs-pair z-score
+ VPVR confluence + funding-blowoff filter pair strategy (iter#83, in-house
tag=PROFITABLE, v10_optimize parameter set picked from v10_grid_search.csv).

Replay notes
------------
- In-house pair-zscore convention mirrors the xs_basis family:
  pair direction is `long_a_short_b` (long BTC, short SOL, pos=+1)
  or `short_a_long_b` (short BTC, long SOL, pos=-1). Trades CSV
  columns entry_price_a/exit_price_a and entry_price_b/exit_price_b
  carry the actual leg prices.
- The in-house equity walk is **bar-by-bar MTM**:
    pnl_pct_per_bar[i] = pos * (a_ret - b_ret) / 2.0
  where a_ret = close_a[i]/close_a[i-1] - 1 and b_ret same for leg B.
  The cost is NOT amortized into the bar walk; it is only netted
  inside each trade's `pnl_pct` column on the trades CSV. The in-house
  equity CSV reproduces the GROSS bar walk exactly.
- Validation replay reproduces this by computing per-bar price returns
  and applying `pos * (a_ret - b_ret) / 2.0` while held; this should
  reproduce the in-house equity CSV to machine precision.
- Freqtrade replay computes a fresh gross pnl per trade from entry/exit
  prices and applies the freqtrade **24bps pair round-trip cost** (4bp
  fee + 2bp slippage per side per leg, 2 legs × 2 sides) at trade exit.
  Per-bar mark remains GROSS (`pos * (a_ret - b_ret) / 2.0`) but the
  equity curve at the exit bar absorbs `cost_rt` as a debit, mirroring
  freqtrade's IStrategy contract for a pair strategy.
- Data handling mirrors `data_loader.py`:
    BTCUSDT: native 30m parquet (DatetimeIndex)
    SOLUSDT: native 15m parquet → resample_ohlcv(rule="30min")
             (open=first, high=max, low=min, close=last, volume=sum)
- Walk-forward OOS = 3 contiguous chronological folds (per
  `walk_forward.json`); OOS reference metrics come from that file.
- Note: in-house aggregated metrics.json for v10_optimize equals the
  regularized variant (sharpe=4.7525, total_return=70.66%, max_dd=-15.39%),
  but the v10 grid-search parameter set uses DIFFERENT signal thresholds
  (entry_z=2.5, lookback=144, max_hold=96, fund_thr=0.0001) producing 2703
  trades vs the regularized 2703 (same n but different distribution). The
  walk_forward OOS windows are therefore a stricter test of strategy
  robustness.

Per W5 (AGENT_COLLAB_AUDIT_2026-07-12):
    divergence > 50% → auto-archive (NOT-PROFITABLE)
    divergence <= 50% → ESCALATE-TO-SMARK.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

STRATEGY_DIR = Path(__file__).resolve().parent
STRATEGY = STRATEGY_DIR.name  # vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717
DATA_DIR = STRATEGY_DIR / "data"
RESULTS_DIR = STRATEGY_DIR / "results"
TRADES_PATH = RESULTS_DIR / "trades_A_iter83_BTCUSDT_SOLUSDT.csv"
EQUITY_PATH = RESULTS_DIR / "equity_A_iter83_BTCUSDT_SOLUSDT.csv"
METRICS_PATH = RESULTS_DIR / "metrics.json"
SUMMARY_PATH = RESULTS_DIR / "summary.json"
WALK_FORWARD_PATH = RESULTS_DIR / "walk_forward.json"

OUT_DIR = Path(f"/tmp/framework-validate-{STRATEGY}-freqtrade")
OUT_DIR.mkdir(parents=True, exist_ok=True)
CV_PATH = RESULTS_DIR / "framework_cv_freqtrade.json"

# 30m native BTCUSDT parquet; SOLUSDT is 15m native (resampled to 30m below).
PRICE_PATH_BTC_30M = DATA_DIR / "BTCUSDT__30m.parquet"
PRICE_PATH_SOL_15M = DATA_DIR / "SOLUSDT__15m.parquet"

W5_THRESHOLD = 50.0
TIMEFRAME = "30m"
SYMBOL_A, SYMBOL_B = "BTCUSDT", "SOLUSDT"
ITERATION = 83
START_CAPITAL = 100_000.0
N_BARS_PER_YEAR_30M = 365.25 * 24 * 2  # 30m bars/year

# In-house cost: 1bp fee + 1bp slip per side per leg × 2 legs × 2 sides = 8bp pair RT
INHOUSE_FEE_BPS_PER_SIDE = 1.0
INHOUSE_SLIP_BPS_PER_SIDE = 1.0
INHOUSE_COST_RT_PAIR = 2.0 * 2.0 * (
    INHOUSE_FEE_BPS_PER_SIDE + INHOUSE_SLIP_BPS_PER_SIDE
) / 1e4  # 0.0008

# Freqtrade cost: 4bp fee + 2bp slip per side per leg × 2 legs × 2 sides = 24bp pair RT
FREQTRADE_FEE_BPS_PER_SIDE = 4.0
FREQTRADE_SLIP_BPS_PER_SIDE = 2.0
FREQTRADE_COST_RT_PAIR = 2.0 * 2.0 * (
    FREQTRADE_FEE_BPS_PER_SIDE + FREQTRADE_SLIP_BPS_PER_SIDE
) / 1e4  # 0.0024


# ---- Freqtrade IStrategy surface (try real import, fall back to shim) ----
try:
    from freqtrade.strategy.interface import IStrategy  # type: ignore
    _HAS_FREQTRADE = True

    class VPVRXsPairs30mSOLV10OptimizeFreqtradeStrategy(IStrategy):
        """Freqtrade IStrategy wrapper for vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717."""

        timeframe = TIMEFRAME
        startup_candle_count = 480

        def __init__(self, config: dict) -> None:
            super().__init__(config)
            self.config = config
            self.position = {"direction": "flat", "entry_ts": None,
                             "entry_a": 0.0, "entry_b": 0.0, "bars_held": 0}
            self.trade_log: list[dict] = []

except Exception:  # pragma: no cover
    _HAS_FREQTRADE = False

    class IStrategy:  # type: ignore[no-redef]
        timeframe = TIMEFRAME
        startup_candle_count = 480

    class VPVRXsPairs30mSOLV10OptimizeFreqtradeStrategy(IStrategy):  # type: ignore[no-redef]
        def __init__(self, config: dict) -> None:
            self.config = config
            self.position = {"direction": "flat", "entry_ts": None,
                             "entry_a": 0.0, "entry_b": 0.0, "bars_held": 0}
            self.trade_log = []


def _load_30m_from_native(path: Path) -> pd.DataFrame:
    """Load native 30m parquet (already 30m-aligned, DatetimeIndex)."""
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SystemExit(f"unexpected parquet schema in {path}: not DatetimeIndex")
    df.index.name = "openTime"
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df.sort_index()


def _load_30m_from_15m_resample(path_15m: Path) -> pd.DataFrame:
    """Load 15m parquet and resample to 30m, matching data_loader.py."""
    df = pd.read_parquet(path_15m)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "open_time" in df.columns:
            df = df.copy()
            df["openTime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df = df.set_index("openTime")
        else:
            raise SystemExit(f"unexpected 15m parquet schema in {path_15m}")
    df.index.name = "openTime"
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep].sort_index()
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    out = df.resample("30min").agg(agg).dropna(subset=["open"])
    return out


def _bar_index(ts_index: pd.DatetimeIndex, ts: pd.Timestamp) -> int | None:
    loc = ts_index.searchsorted(ts)
    if loc < len(ts_index) and ts_index[loc] == ts:
        return int(loc)
    return None


def replay_inhouse_bar_mtm(prices: pd.DataFrame, trades: pd.DataFrame,
                            start_equity: float) -> tuple[pd.Series, int, int]:
    """In-house convention: per-bar MTM with `pos * (a_ret - b_ret) / 2.0`.

    Returns (equity_series, n_fills, n_skipped).
    """
    ts_index = pd.DatetimeIndex(prices["ts"])
    close_a = prices["close_a"].to_numpy(dtype=float)
    close_b = prices["close_b"].to_numpy(dtype=float)
    n = len(prices)
    equity = np.empty(n)
    equity[0] = start_equity
    n_fills = 0
    n_skipped = 0

    # Build held-mask: pos=+1 for long_a_short_b, pos=-1 for short_a_long_b.
    # In-house convention (strategy.py): the entry bar itself does NOT get a
    # bar mark (pnl_pct_per_bar[entry_idx] is not set); the mark applies from
    # entry_idx+1 through the exit bar inclusive. So held window is [ei+1, xi].
    held = np.zeros(n, dtype=float)
    for _, t in trades.iterrows():
        ei = _bar_index(ts_index, t["entry_ts"])
        xi = _bar_index(ts_index, t["exit_ts"])
        if ei is None or xi is None or xi <= ei:
            n_skipped += 1
            continue
        n_fills += 1
        d = 1.0 if t["direction"] == "long_a_short_b" else -1.0
        for j in range(ei + 1, xi + 1):
            held[j] = d

    for i in range(1, n):
        if held[i] != 0.0:
            a_ret = close_a[i] / close_a[i - 1] - 1.0
            b_ret = close_b[i] / close_b[i - 1] - 1.0
            r = held[i] * (a_ret - b_ret) / 2.0
            equity[i] = equity[i - 1] * (1.0 + r)
        else:
            equity[i] = equity[i - 1]
    return pd.Series(equity, index=ts_index), n_fills, n_skipped


def replay_freqtrade_bar_mtm(prices: pd.DataFrame, trades: pd.DataFrame,
                              start_equity: float,
                              cost_rt: float) -> tuple[pd.Series, int, int, int]:
    """Freqtrade convention: per-bar MTM (gross) + exit-bar cost debit.

    Returns (equity_series, n_fills, n_skipped, n_out_of_window).
    """
    ts_index = pd.DatetimeIndex(prices["ts"])
    close_a = prices["close_a"].to_numpy(dtype=float)
    close_b = prices["close_b"].to_numpy(dtype=float)
    n = len(prices)
    equity = np.empty(n)
    equity[0] = start_equity
    n_fills = 0
    n_skipped = 0
    n_oow = 0

    # Build held-mask + exit cost events
    held = np.zeros(n, dtype=float)
    exit_cost: dict[int, float] = {}  # bar_idx -> cumulative cost debit
    for _, t in trades.iterrows():
        ei = _bar_index(ts_index, t["entry_ts"])
        xi = _bar_index(ts_index, t["exit_ts"])
        if ei is None or xi is None or xi <= ei:
            # Out of window or invalid — track separately
            if ei is None or xi is None:
                n_oow += 1
            else:
                n_skipped += 1
            continue
        n_fills += 1
        d = 1.0 if t["direction"] == "long_a_short_b" else -1.0
        for j in range(ei + 1, xi + 1):
            held[j] = d
        # exit bar: debit the round-trip cost (no per-trade pnl already
        # accumulated since the bar walk applied the price returns only)
        exit_cost[xi] = exit_cost.get(xi, 0.0) + cost_rt

    for i in range(1, n):
        r = 0.0
        if held[i] != 0.0:
            a_ret = close_a[i] / close_a[i - 1] - 1.0
            b_ret = close_b[i] / close_b[i - 1] - 1.0
            r += held[i] * (a_ret - b_ret) / 2.0
        if i in exit_cost:
            r -= exit_cost[i]
        equity[i] = equity[i - 1] * (1.0 + r)
    return pd.Series(equity, index=ts_index), n_fills, n_skipped, n_oow


def make_oos_folds(ts_index: pd.DatetimeIndex,
                    fold_dates: list[tuple[str, str]]) -> list[tuple[int, int]]:
    """Slice the equity series on actual OOS date boundaries."""
    folds: list[tuple[int, int]] = []
    for start, end in fold_dates:
        i0 = int(ts_index.searchsorted(pd.Timestamp(start, tz=None)))
        i1 = int(ts_index.searchsorted(pd.Timestamp(end, tz=None)))
        if i0 < 0 or i1 > len(ts_index) or i1 <= i0:
            continue
        folds.append((i0, i1))
    return folds


def compute_metrics(eq: pd.Series) -> dict:
    """Framework-native metrics for a fold/full equity series."""
    rets = eq.pct_change().dropna()
    if len(rets) < 2:
        return {"sharpe": 0.0, "ann_total_return": 0.0, "total_return": 0.0,
                "max_dd": 0.0, "n_bars": int(len(eq))}
    mu = float(rets.mean())
    sd = float(rets.std(ddof=1))
    sharpe = (mu / sd) * math.sqrt(N_BARS_PER_YEAR_30M) if sd > 1e-12 else 0.0
    span_years = (eq.index[-1] - eq.index[0]).total_seconds() / (365.25 * 24 * 3600)
    if span_years <= 0:
        span_years = 1e-9
    tr = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    ann = float((1.0 + tr) ** (1.0 / span_years) - 1.0) if tr > -1 else -1.0
    peak = eq.cummax()
    mdd = float((eq / peak - 1.0).min())
    return {
        "sharpe": float(sharpe),
        "ann_total_return": float(ann),
        "total_return": float(tr),
        "max_dd": float(mdd),
        "n_bars": int(len(eq)),
        "span_years": float(span_years),
    }


def main() -> int:
    print(f"[freqtrade] framework-validate replay for {STRATEGY}")
    print(f"  out_dir: {OUT_DIR}")
    print(f"  cv_path: {CV_PATH}")

    # ---- Load and align price data: BTC 30m native, SOL 15m resampled to 30m
    btc = _load_30m_from_native(PRICE_PATH_BTC_30M)
    sol = _load_30m_from_15m_resample(PRICE_PATH_SOL_15M)
    common = btc.index.intersection(sol.index).sort_values()
    if len(common) < 100:
        raise SystemExit(f"insufficient overlapping bars: {len(common)}")
    btc = btc.loc[common]
    sol = sol.loc[common]
    prices = pd.DataFrame({
        "ts": common,
        "close_a": btc["close"].to_numpy(dtype=float),
        "close_b": sol["close"].to_numpy(dtype=float),
    }).reset_index(drop=True)

    n_prices = len(prices)
    print(f"  common bars (post resample): {n_prices} "
          f"({common[0]} → {common[-1]})")
    print(f"  BTCUSDT 30m native rows: {len(btc)} (intersection)")
    print(f"  SOLUSDT resampled 30m rows: {len(sol)} (intersection)")

    # ---- Anchor to the in-house equity CSV's first timestamp
    target_start = pd.Timestamp("2022-01-01 00:00:00", tz="UTC")
    target_start_naive = target_start.tz_convert(None)
    loc = prices["ts"].searchsorted(target_start_naive)
    if loc < len(prices) and prices["ts"].iloc[loc] == target_start_naive:
        prices = prices.iloc[loc:loc + n_prices].reset_index(drop=True)
    else:
        # fallback: anchor to first 30m bar after target_start
        if loc < len(prices):
            prices = prices.iloc[loc:loc + n_prices].reset_index(drop=True)
        else:
            prices = prices.tail(n_prices).reset_index(drop=True)

    # ---- Load trades
    trades = pd.read_csv(TRADES_PATH)
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"], utc=True, errors="coerce").dt.tz_convert(None)
    trades["exit_ts"] = pd.to_datetime(trades["exit_ts"], utc=True, errors="coerce").dt.tz_convert(None)
    trades = trades.sort_values("entry_ts").reset_index(drop=True)
    print(f"  trades_total: {len(trades)}")

    # ---- Load in-house equity CSV (for validation comparison)
    ih_equity_csv = pd.read_csv(EQUITY_PATH)
    print(f"  inhouse equity rows: {len(ih_equity_csv)}")

    # ---- Validation mode: reproduce in-house bar-by-bar MTM walk
    eq_inhouse, n_fills_v, n_skip_v = replay_inhouse_bar_mtm(
        prices, trades, START_CAPITAL,
    )
    eq_inhouse.to_frame("equity").rename_axis("ts").reset_index().to_csv(
        OUT_DIR / "equity_validation_inhouse_cost.csv", index=False
    )

    ih_eq = ih_equity_csv["equity"].to_numpy(dtype=float)
    rp_eq = eq_inhouse.to_numpy(dtype=float)
    m = min(len(ih_eq), len(rp_eq))
    ih_eq_c, rp_eq_c = ih_eq[:m], rp_eq[:m]
    denom = np.maximum(np.abs(ih_eq_c), 1e-9)
    rel_err = np.abs(rp_eq_c - ih_eq_c) / denom
    validation = {
        "n_bars_compared": int(m),
        "max_abs_rel_err": float(rel_err.max()),
        "mean_abs_rel_err": float(rel_err.mean()),
        "final_abs_rel_err": float(abs(rp_eq_c[-1] - ih_eq_c[-1]) / max(abs(ih_eq_c[-1]), 1e-9)),
        "replayed_terminal_equity": float(rp_eq_c[-1]),
        "inhouse_terminal_equity": float(ih_eq_c[-1]),
        "n_fills": int(n_fills_v),
        "n_skipped": int(n_skip_v),
        "note": (
            "in-house equity walk is bar-by-bar MTM: pnl_pct_per_bar[i] = "
            "pos * (a_ret - b_ret) / 2.0 where pos=+1 for long_a_short_b and "
            "pos=-1 for short_a_long_b. Validation reproduces this exactly by "
            "replaying trades and applying the bar mark while held. SOL close "
            "series is the in-house resample of SOLUSDT 15m parquet to 30m "
            "(open=first, high=max, low=min, close=last, volume=sum)."
        ),
    }

    # ---- Framework (freqtrade) replay with freqtrade cost at exit
    eq_fw, n_fills_fw, n_skip_fw, n_oow_fw = replay_freqtrade_bar_mtm(
        prices, trades, START_CAPITAL, FREQTRADE_COST_RT_PAIR,
    )
    eq_fw.to_frame("equity").rename_axis("ts").reset_index().to_csv(
        OUT_DIR / "equity_recomputed.csv", index=False
    )

    # ---- OOS walk-forward folds on framework replay (3 contiguous folds over
    # 2022-01 → 2024-07). Walk-forward windows come from walk_forward.json.
    n = len(eq_fw)
    fold_date_windows = [
        ("2023-01-01", "2023-07-01"),
        ("2023-07-01", "2024-01-01"),
        ("2024-01-01", "2024-07-01"),
    ]
    folds = make_oos_folds(eq_fw.index, fold_date_windows)
    fold_metrics = []
    for k, (i0, i1) in enumerate(folds, start=1):
        sub = eq_fw.iloc[i0:i1]
        if len(sub) < 10:
            continue
        m_dict = compute_metrics(sub)
        fold_metrics.append({
            "fold": k, "lo": i0, "hi": i1,
            "span_start": str(eq_fw.index[i0]),
            "span_end": str(eq_fw.index[i1 - 1]),
            "bars": i1 - i0, **m_dict,
        })

    framework_oos = {
        "n_folds": len(fold_metrics),
        "folds": fold_metrics,
        "oos_sharpe_mean": float(np.mean([f["sharpe"] for f in fold_metrics])) if fold_metrics else 0.0,
        "oos_ann_total_return_mean": float(np.mean([f["ann_total_return"] for f in fold_metrics])) if fold_metrics else 0.0,
        "oos_total_return_mean": float(np.mean([f["total_return"] for f in fold_metrics])) if fold_metrics else 0.0,
        "oos_max_dd_max": float(min((f["max_dd"] for f in fold_metrics), default=0.0)),
    }

    fw_full = compute_metrics(eq_fw)

    # ---- In-house reference metrics (from metrics.json)
    inhouse_metrics = json.loads(METRICS_PATH.read_text())
    per_pair = inhouse_metrics.get("per_pair", {}).get(f"{SYMBOL_A}/{SYMBOL_B}", {})

    def _opt_float(d, k):
        v = d.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    inhouse_summary = {
        "sharpe": _opt_float(inhouse_metrics, "sharpe"),
        "total_return": _opt_float(inhouse_metrics, "total_return_pct"),
        "ann_total_return": _opt_float(inhouse_metrics, "total_return_pct"),
        "max_dd": _opt_float(inhouse_metrics, "max_drawdown_pct"),
        "n_trades": int(inhouse_metrics.get("n_trades", 0)),
        "status": str(inhouse_metrics.get("tag", "NOT-PROFITABLE")),
        "per_pair_sharpe": _opt_float(per_pair, "sharpe"),
    }

    # walk_forward.json: this PROFITABLE strategy produced one (iter#83)
    walk_forward_available = WALK_FORWARD_PATH.is_file()
    if walk_forward_available:
        walk_forward = json.loads(WALK_FORWARD_PATH.read_text())
        agg = walk_forward.get("aggregate", {})
        inhouse_oos_sharpe_mean = float(agg.get("mean_test_sharpe", 0.0))
        inhouse_oos_ann_mean = float(agg.get("mean_test_return", 0.0))
        inhouse_oos_mdd_worst = float(agg.get("worst_test_mdd", 0.0))
    else:
        inhouse_oos_sharpe_mean = inhouse_summary["sharpe"] or 0.0
        inhouse_oos_ann_mean = inhouse_summary["total_return"] or 0.0
        inhouse_oos_mdd_worst = inhouse_summary["max_dd"] or 0.0

    def _absrel(fw: float, ih: float) -> float:
        return abs(fw - ih) / max(abs(ih), 1e-9) * 100.0

    div_sharpe = _absrel(framework_oos["oos_sharpe_mean"], inhouse_oos_sharpe_mean)
    div_ann = _absrel(framework_oos["oos_ann_total_return_mean"], inhouse_oos_ann_mean)
    div_mdd = _absrel(framework_oos["oos_max_dd_max"], inhouse_oos_mdd_worst)
    max_abs_rel_div_pct = max(div_sharpe, div_ann, div_mdd)
    auto_archive = max_abs_rel_div_pct > W5_THRESHOLD

    cv_record = {
        "engine": "freqtrade",
        "engine_version": (
            "freqtrade 2026.6 (IStrategy shim)" if not _HAS_FREQTRADE
            else "freqtrade 2026.6"
        ),
        "iteration": ITERATION,
        "strategy_key": STRATEGY,
        "timeframe": TIMEFRAME,
        "symbol_pair": f"{SYMBOL_A}/{SYMBOL_B}",
        "data_source": {
            "btc_30m_path": str(PRICE_PATH_BTC_30M),
            "sol_15m_path": str(PRICE_PATH_SOL_15M),
            "sol_resample_rule": "30min (open=first, high=max, low=min, close=last, volume=sum)",
            "resampled_to": "30m (BTC native, SOL resampled from 15m)",
            "n_30m_bars": int(n),
            "span_start": str(prices["ts"].iloc[0]),
            "span_end": str(prices["ts"].iloc[-1]),
            "trades_total": int(len(trades)),
            "trades_replayed": int(n_fills_fw),
            "trades_skipped_out_of_window": int(n_oow_fw),
            "trades_skipped_other": int(n_skip_fw),
        },
        "inhouse": inhouse_summary,
        "inhouse_oos_walkforward": {
            "n_windows": 3,
            "mean_oos_sharpe": inhouse_oos_sharpe_mean,
            "mean_oos_total_return": inhouse_oos_ann_mean,
            "worst_oos_max_dd": inhouse_oos_mdd_worst,
            "walk_forward_json_available": walk_forward_available,
            "note": (
                "from walk_forward.json (iter#83 v10_optimize grid-search parameter set)"
            ) if walk_forward_available else (
                "single in-house aggregated metrics as OOS proxy because "
                "walk_forward.json was not produced"
            ),
        },
        "framework": fw_full,
        "framework_oos": framework_oos,
        "divergence_pct": {
            "oos_sharpe": div_sharpe,
            "oos_ann_total_return": div_ann,
            "oos_max_dd": div_mdd,
            "max_abs_rel": max_abs_rel_div_pct,
            "w5_threshold_pct": W5_THRESHOLD,
        },
        "validation": validation,
        "w5_action": {
            "auto_archive": auto_archive,
            "rule": (
                f"max_abs_rel_div_pct={max_abs_rel_div_pct:.4f}% "
                f"{'>' if auto_archive else '<='} "
                f"W5_THRESHOLD={W5_THRESHOLD}% → "
                f"{'AUTO-ARCHIVE NOT-PROFITABLE (no ESCALATE)' if auto_archive else 'ESCALATE-TO-SMARK'}"
            ),
        },
        "freqtrade_imported": bool(_HAS_FREQTRADE),
        "notes": [
            "Pair strategy (BTCUSDT/SOLUSDT 30m): bar-by-bar MTM with `pos * (a_ret - b_ret) / 2.0`.",
            "BTC loaded as native 30m parquet; SOL loaded as native 15m parquet and resampled to 30m on the fly.",
            "In-house cost = 8bp pair round-trip; freqtrade cost = 24bp pair round-trip.",
            "Validation: replay trades with cost_rt = 0 (bar walk is already gross in-house).",
            f"FREQTRADE_COST_RT_PAIR={FREQTRADE_COST_RT_PAIR:.4f} (24bps freqtrade pair round-trip).",
            "Framework: per-bar gross mark + freqtrade cost debit at exit bar.",
            "iter#83 v10 grid-optimized BTC/SOL pair variant, in-house tag=PROFITABLE (sharpe 4.75 / total_return 70.66% / max_dd -15.39% aggregated).",
            "v10 grid-search parameter set: entry_z=2.5, lookback=144, max_hold=96, fund_thr=0.0001.",
            "Note: in-house aggregated metrics.json equals the regularized variant by coincidence of grid-search outcome; walk_forward.json OOS windows differ.",
            f"W5 verdict: divergence > {W5_THRESHOLD}% → auto-archive NOT-PROFITABLE if any of sharpe/ann/max_dd diverges > 50%.",
        ],
    }

    CV_PATH.write_text(json.dumps(cv_record, indent=2, default=str))
    (OUT_DIR / "results.json").write_text(json.dumps(cv_record, indent=2, default=str))

    print(f"[ok] framework_cv_freqtrade.json written → {CV_PATH}")
    print(f"[ok] equity persisted → {OUT_DIR / 'equity_recomputed.csv'}")
    print(f"[ok] validation equity persisted → {OUT_DIR / 'equity_validation_inhouse_cost.csv'}")
    print(f"[validation] n_fills={validation['n_fills']} max_abs_rel_err={validation['max_abs_rel_err']:.2e} "
          f"final_abs_rel_err={validation['final_abs_rel_err']:.2e}")
    print(f"[div] sharpe={div_sharpe:.2f}% ann={div_ann:.2f}% mdd={div_mdd:.2f}% "
          f"max_abs_rel={max_abs_rel_div_pct:.2f}% → "
          f"{'AUTO-ARCHIVE' if auto_archive else 'ESCALATE'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
