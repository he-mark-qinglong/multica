"""SMA-34928 — vpvr_funding_carry_asym on BTC 15m with lowered / percentile-based
funding gate.

Runs the existing ``strategy.run_backtest`` end-to-end on BTCUSDT 15m
(last 30d, matching SMA-34920's window) across a small grid:

  1) absolute threshold 0.0001   = the actual 30d max (kept for ref)
  2) absolute threshold 0.00005  = ~50th pct of the 30d distribution
  3) percentile gate q=95 (top 5%)
  4) percentile gate q=90 (top 10%)
  5) percentile gate q=80 (top 20%)
  6) percentile gate q=70 (top 30%)

Reports daily-resampled OOS Sharpe, annualized return, maxDD, and
trade count for each. Writes a single summary JSON.

This is the SMA-34928 "lowered OR percentile gate" experiment; see
issue 590facde-d6a8-46e2-bf73-cad8ab2a3a16.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from strategy import VARIANT_KEY, run_backtest  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "funding-carry-asym"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)


# ---------------------------------------------------------------------------
# Data loading — mirrors run_backtest._load_tf for 15m
# ---------------------------------------------------------------------------
def load_btc_15m(window_days: int) -> tuple[pd.DataFrame, dict]:
    """Load BTCUSDT 15m OHLCV + funding (ffilled) for the last `window_days`.

    Returns (df, funding_stats) where funding_stats describes the
    raw funding-event distribution on the loaded window (used for
    the cross-check below).
    """
    # live_data is the canonical up-to-date location (spans to ~today).
    # The strategy-local snapshot is from a 2024 hot-window experiment
    # and is intentionally NOT used here.
    candidates = [
        QUANT_LOOP / "live_data" / "BTCUSDT_15m.parquet",
        DATA_DIR / "BTCUSDT__15m.parquet",
    ]
    df = None
    for p in candidates:
        if p.exists():
            df = pd.read_parquet(p)
            if "open_time" in df.columns:
                df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
                df = df.set_index("ts")
            df = df.sort_index()
            break
    if df is None:
        raise FileNotFoundError("no BTC 15m OHLCV parquet found")
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    df = df[["open", "high", "low", "close", "volume"]].astype(np.float64)

    funding_p = QUANT_LOOP / "data" / "funding" / "BTCUSDT.parquet"
    fdf = pd.read_parquet(funding_p)
    if "ts" in fdf.columns:
        fdf["ts"] = pd.to_datetime(fdf["ts"], utc=True)
        fdf = fdf.set_index("ts")
    elif "fundingTime" in fdf.columns:
        fdf["ts"] = pd.to_datetime(fdf["fundingTime"], unit="ms", utc=True)
        fdf = fdf.set_index("ts")
    fdf = fdf.sort_index()
    if fdf.index.tz is not None:
        fdf.index = fdf.index.tz_convert(None)
    if fdf.index.dtype != df.index.dtype:
        fdf.index = pd.DatetimeIndex(fdf.index.values, tz=None)
    funding = fdf[["fundingRate"]].astype(np.float64)
    funding_aligned = funding.reindex(df.index, method="ffill")
    df["funding"] = funding_aligned["fundingRate"].fillna(0.0)

    end = df.index.max()
    start = end - pd.Timedelta(days=window_days)
    window_df = df.loc[start:end].copy()

    # Funding stats on the event series within the window
    events_in_window = fdf.loc[start:end]["fundingRate"] if fdf.index.tz is None else fdf["fundingRate"].loc[start.tz_localize("UTC"):end.tz_localize("UTC")]
    # Use event series directly with same tz as window_df
    events = fdf["fundingRate"].loc[pd.to_datetime(start, utc=True).tz_convert(None):
                                    pd.to_datetime(end, utc=True).tz_convert(None)]
    stats = {
        "n_events": int(len(events)),
        "max": float(events.max()) if len(events) else 0.0,
        "p99": float(events.quantile(0.99)) if len(events) else 0.0,
        "p95": float(events.quantile(0.95)) if len(events) else 0.0,
        "p90": float(events.quantile(0.90)) if len(events) else 0.0,
        "p80": float(events.quantile(0.80)) if len(events) else 0.0,
        "p70": float(events.quantile(0.70)) if len(events) else 0.0,
        "p50": float(events.quantile(0.50)) if len(events) else 0.0,
        "mean": float(events.mean()) if len(events) else 0.0,
    }
    return window_df, stats


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _daily_resampled_sharpe(equity: np.ndarray, idx: pd.DatetimeIndex) -> float:
    series = pd.Series(equity, index=idx, dtype=np.float64)
    daily_eq = series.resample("1D").last().dropna()
    if len(daily_eq) < 2:
        return 0.0
    rets = daily_eq.pct_change().dropna()
    if rets.std() == 0 or not np.isfinite(rets.std()):
        return 0.0
    return float(rets.mean() / rets.std() * SQRT_BPY_DAILY)


def compute_metrics(result: dict, idx: pd.DatetimeIndex) -> dict:
    equity = np.asarray(result["equity"], dtype=np.float64)
    trades = result.get("trades", [])
    n_bars = int(result.get("n_bars", len(equity)))
    starting = float(equity[0]) if len(equity) else 0.0
    final = float(equity[-1]) if len(equity) else 0.0

    if len(equity) < 2 or starting <= 0:
        return {
            "n_bars": n_bars, "n_trades": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "sharpe_daily": 0.0,
            "total_return": 0.0, "annualized_return": 0.0,
            "max_drawdown_pct": 0.0, "avg_bars_held": 0.0,
        }

    total_return = (final / starting) - 1.0
    eq_idx = idx[: len(equity)]
    daily_eq = pd.Series(equity, index=eq_idx, dtype=np.float64).resample("1D").last().dropna()
    if len(daily_eq) >= 2:
        n_days = max(1, (daily_eq.index[-1] - daily_eq.index[0]).days)
        n_years = n_days / BARS_PER_YEAR_DAILY
    else:
        n_years = n_bars / (BARS_PER_YEAR_DAILY * 1440)
    annualized = ((final / starting) ** (1.0 / n_years) - 1.0) if n_years > 0 and final > 0 else 0.0

    sharpe = _daily_resampled_sharpe(equity, eq_idx)
    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_dd_pct = float(np.min(drawdowns)) * 100.0 if drawdowns.size else 0.0

    n_trades = len(trades)
    pnl_pcts = np.array([t["pnl_pct"] for t in trades], dtype=np.float64) if n_trades else np.array([])
    gross_profit = float(pnl_pcts[pnl_pcts > 0].sum()) if pnl_pcts.size else 0.0
    gross_loss = float(abs(pnl_pcts[pnl_pcts < 0].sum())) if pnl_pcts.size else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = float((pnl_pcts > 0).sum() / n_trades) if n_trades > 0 else 0.0
    avg_bars_held = float(np.mean([t["bars_held"] for t in trades])) if n_trades > 0 else 0.0

    return {
        "n_bars": n_bars,
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if np.isfinite(profit_factor) else float("inf"),
        "sharpe_daily": round(sharpe, 4),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "avg_bars_held": round(avg_bars_held, 2),
    }


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Variant runner
# ---------------------------------------------------------------------------
def run_variant(df: pd.DataFrame, label: str, params_override: dict,
                base_params: dict, starting_capital: float = 100000.0) -> dict:
    cfg = {
        "variant": VARIANT_KEY,
        "strategy_key": VARIANT_KEY,
        "iteration": 1,
        "date": "2026-07-18",
        "source_spec": "SMA-34928",
        "instruments": ["BTCUSDT"],
        "starting_capital_usd": starting_capital,
        "timeframes": ["15m"],
        "window_days": int((df.index[-1] - df.index[0]).days + 1),
        "params": {**base_params, **params_override},
    }
    t0 = time.time()
    result = run_backtest(df, cfg)
    metrics = compute_metrics(result, df.index)
    metrics["tf"] = "15m"
    metrics["variant"] = VARIANT_KEY
    metrics["diagnostics"] = result.get("diagnostics", {})
    metrics["span_start"] = result["span_start"]
    metrics["span_end"] = result["span_end"]
    metrics["symbol"] = result["symbol"]
    metrics["sharpe_method"] = "daily_resampled_per_SMA-34787"
    metrics["label"] = label
    metrics["params_override"] = _sanitize(params_override)
    metrics["elapsed_sec"] = round(time.time() - t0, 2)
    return metrics


def main() -> int:
    window_days = 30
    df, funding_stats = load_btc_15m(window_days)
    print(f"[load] window {df.index[0]} -> {df.index[-1]}  rows={len(df)}")
    print(f"[load] funding event stats (30d): {funding_stats}")

    # Base params — mirrors config.json. Use 15m max_hold = 8.
    base = {
        "funding_threshold": 0.0003,           # ignored when percentile gate is set
        "support_kind": "HVN",
        "proximity_atr": 1.0,
        "atr_period": 14,
        "vpvr_window_bars": 180,
        "vpvr_snapshot_every_bars": 6,
        "vpvr_bins": 24,
        "vpvr_hvn_quantile": 0.85,
        "vpvr_lvn_quantile": 0.15,
        "vpvr_num_hvn": 3,
        "vpvr_num_lvn": 3,
        "take_profit_atr_k": 1.5,
        "hard_stop_atr_k": 1.0,
        "max_hold_bars_15m": 8,
        "max_hold_bars": 8,
        "risk_target_pct": 0.005,
        "cooldown_bars": 5,
        "fee_bps_per_fill": 4.0,
        "slippage_bps_per_fill": 1.0,
        "funding_carry_bps_per_bar": 0.01,
        # SMA-34928 — percentile gate lookback (30d @ 8h events = ~90 events)
        "funding_lookback_events": 90,
    }

    variants = [
        ("abs_0.0003",      {"funding_threshold": 0.0003},  None),  # SMA-34854 baseline
        ("abs_0.0001",      {"funding_threshold": 0.0001},  None),  # at the 30d max
        ("abs_0.00005",     {"funding_threshold": 0.00005}, None),  # ~50th pct
        ("pct_q95",         {"funding_percentile_q": 95.0}, None),
        ("pct_q90",         {"funding_percentile_q": 90.0}, None),
        ("pct_q80",         {"funding_percentile_q": 80.0}, None),
        ("pct_q70",         {"funding_percentile_q": 70.0}, None),
    ]

    out_results = []
    for label, override, _ in variants:
        print(f"\n=== variant: {label}  override={override} ===", flush=True)
        m = run_variant(df, label, override, base)
        out_results.append(m)
        d = m["diagnostics"]
        print(f"  long_signals={d.get('signal_bars_with_positive_signal', 0)} "
              f"funding_above={d.get('signal_bars_funding_above_threshold', 0)} "
              f"near_support={d.get('signal_bars_near_support', 0)}")
        print(f"  trades={m['n_trades']} sharpe_d={m['sharpe_daily']:.3f} "
              f"ann={m['annualized_return']:.4f} maxDD={m['max_drawdown_pct']:.3f}% "
              f"WR={m['win_rate']:.3f}")

    # Save summary
    summary = {
        "variant_key": VARIANT_KEY,
        "iteration": "SMA-34928",
        "source_spec": "SMA-34928",
        "instruments": ["BTCUSDT"],
        "timeframes": ["15m"],
        "window_days": window_days,
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "funding_event_stats_30d": funding_stats,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sharpe_method": "daily_resampled_per_SMA-34787",
        "variants": [_sanitize(r) for r in out_results],
    }
    out_path = RESULTS_DIR / "sma34928_summary.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nWrote {out_path}")

    # Also drop a top-level copy under results/funding-carry-asym/ for cross-strategy comparison
    toplevel_path = TOPLEVEL_RESULTS / "backtest_15m_sma34928.json"
    toplevel_path.write_text(json.dumps(summary, indent=2))
    print(f"Wrote {toplevel_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
