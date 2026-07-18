"""Multi-TF smoke backtest for funding_carry_asym (SMA-34793).

Runs the signal across BTCUSDT 1m / 15m / 4h on the last 30 days of
each resolution, then writes a per-TF metrics envelope and an
aggregate BacktestResult JSON. This is a wiring test, not a tuning
pass: the SMA-34793 done criteria is "just to prove it runs
end-to-end".

Output files
------------
  results/metrics.json                    — overall envelope (all TFs combined)
  results/per_resolution_summary.json     — per-TF breakdown
  results/equity_<tf>.csv                 — equity curve per TF
  results/trades_<tf>.csv                 — per-trade ledger per TF
  results/summary.txt                     — human-readable run log

The Top-level BacktestResult JSONs are also written to
``~/multica/quant-loop/results/funding-carry-asym/`` for cross-
strategy comparison.
"""
from __future__ import annotations

import json
import math
import sys
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

CONFIG_PATH = REPO_ROOT / "config.json"

BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)


# ---------------------------------------------------------------------------
# Per-TF data loader.
# ---------------------------------------------------------------------------
def _load_tf(tf: str, window_days: int, symbol: str) -> pd.DataFrame:
    """Load the last `window_days` for one symbol at one timeframe.

    Resolution order:
      1. ``data/{symbol}__{tf}.parquet`` — staged by the harness.
      2. ``~/multica/quant-loop/live_data/{symbol}_{tf}.parquet`` —
         canonical catalog location.
    Funding is ffilled from the SMA-34789 Binance USDT-M perpetual
    parquet under ``data/funding/``.
    """
    candidates = [
        DATA_DIR / f"{symbol}__{tf}.parquet",
        QUANT_LOOP / "live_data" / f"{symbol}_{tf}.parquet",
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
    # Some staged parquets (1m/15m) already ship with a tz-aware UTC
    # DatetimeIndex; coerce to tz-naive so the funding reindex works.
    if df is not None and df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    if df is None:
        raise FileNotFoundError(
            f"no OHLCV for {symbol}@{tf}; looked in {[str(p) for p in candidates]}"
        )

    # All three TFs (1m / 15m / 4h) have their own staged parquet in
    # ``data/`` or ``live_data/``; no resampling needed.

    df = df[["open", "high", "low", "close", "volume"]].astype(np.float64)

    funding_p = QUANT_LOOP / "data" / "funding" / f"{symbol}.parquet"
    if not funding_p.exists():
        funding_p = QUANT_LOOP / "data" / "funding" / f"{symbol}_bybit_funding.parquet"
    if funding_p.exists():
        fdf = pd.read_parquet(funding_p)
        if "ts" in fdf.columns:
            fdf["ts"] = pd.to_datetime(fdf["ts"], utc=True)
            fdf = fdf.set_index("ts")
        elif "fundingTime" in fdf.columns:
            fdf["ts"] = pd.to_datetime(fdf["fundingTime"], unit="ms", utc=True)
            fdf = fdf.set_index("ts")
        fdf = fdf.sort_index()
        # Force the funding index onto the OHLCV index's tz state.
        # OHLCV is tz-naive datetime64 (loaded via `unit="ms", utc=False`)
        # while `pd.to_datetime(..., utc=True)` is tz-aware UTC. We
        # strip tz on the funding side so `reindex` works.
        if fdf.index.tz is not None:
            fdf.index = fdf.index.tz_convert(None)
        funding = fdf[["fundingRate"]].astype(np.float64)
        # Defensive: if the index resolution doesn't align, upcast
        # the funding index to match the OHLCV resolution.
        if funding.index.dtype != df.index.dtype:
            funding.index = pd.DatetimeIndex(funding.index.values, tz=None)
        funding_aligned = funding.reindex(df.index, method="ffill")
        df["funding"] = funding_aligned["fundingRate"].fillna(0.0)
    else:
        df["funding"] = 0.0  # Funding absent → all signals are FLAT by design.

    end = df.index.max()
    start = end - pd.Timedelta(days=window_days)
    return df.loc[start:end].copy()


# ---------------------------------------------------------------------------
# Metrics helpers.
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


def _compute_metrics(result: dict, idx: pd.DatetimeIndex) -> dict:
    equity = np.asarray(result["equity"], dtype=np.float64)
    trades = result["trades"]
    n_bars = int(result["n_bars"])
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
    if n_years > 0 and final > 0 and starting > 0:
        annualized = (final / starting) ** (1.0 / n_years) - 1.0
    else:
        annualized = 0.0

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


def _tf_to_freq(tf: str) -> str:
    return {"1m": "1min", "15m": "15min", "4h": "4h"}.get(tf, "1min")


def _write_equity_csv(result: dict, tf: str) -> None:
    eq = result["equity"]
    n = len(eq)
    freq = _tf_to_freq(tf)
    idx = pd.date_range(start=result["span_start"], periods=n, freq=freq, tz="UTC")
    pd.DataFrame({"equity": eq}, index=idx).rename_axis("timestamp").to_csv(
        RESULTS_DIR / f"equity_{tf}.csv"
    )


def _write_trades_csv(result: dict, tf: str) -> None:
    trades = result["trades"]
    cols = [
        "variant", "symbol", "direction", "entry_ts", "entry_price",
        "exit_ts", "exit_price", "pnl_pct", "bars_held", "exit_reason",
        "funding_at_entry", "support_level_price", "support_distance_atr",
        "near_support",
    ]
    if not trades:
        pd.DataFrame(columns=cols).to_csv(RESULTS_DIR / f"trades_{tf}.csv", index=False)
        return
    pd.DataFrame(trades).to_csv(RESULTS_DIR / f"trades_{tf}.csv", index=False)


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


def _tf_params(cfg: dict, tf: str) -> dict:
    p = dict(cfg["params"])
    p["max_hold_bars"] = int(p[f"max_hold_bars_{tf}"])
    return p


def _run_one_tf(cfg: dict, tf: str) -> dict:
    window_days = int(cfg["window_days"])
    symbol = cfg["instruments"][0]
    df = _load_tf(tf, window_days, symbol)
    tf_cfg = dict(cfg)
    tf_cfg["params"] = _tf_params(cfg, tf)
    print(f"[{tf}] rows={len(df)} start={df.index[0]} end={df.index[-1]}", flush=True)

    result = run_backtest(df, tf_cfg)
    metrics = _compute_metrics(result, df.index)
    metrics["tf"] = tf
    metrics["variant"] = VARIANT_KEY
    metrics["diagnostics"] = result.get("diagnostics", {})
    metrics["span_start"] = result["span_start"]
    metrics["span_end"] = result["span_end"]
    metrics["symbol"] = result["symbol"]
    metrics["sharpe_method"] = "daily_resampled_per_SMA-34787"
    metrics["sharpe_method_audit_ref"] = "SMA-34787"
    payload = _sanitize(metrics)
    (TOPLEVEL_RESULTS / f"backtest_{tf}.json").write_text(
        json.dumps(payload, indent=2)
    )
    _write_equity_csv(result, tf)
    _write_trades_csv(result, tf)
    return {
        "tf": tf,
        "rows": int(len(df)),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "diagnostics": result.get("diagnostics", {}),
        "metrics": metrics,
    }


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    tfs = list(cfg["timeframes"])

    per_tf = []
    for tf in tfs:
        try:
            per_tf.append(_run_one_tf(cfg, tf))
        except Exception as exc:  # noqa: BLE001
            print(f"[{tf}] FAILED: {type(exc).__name__}: {exc}")
            per_tf.append({
                "tf": tf, "rows": 0,
                "metrics": {"n_trades": 0, "sharpe_daily": 0.0, "error": str(exc)},
            })

    sharpes = [
        m["metrics"]["sharpe_daily"] for m in per_tf if "error" not in m["metrics"]
    ]
    anns = [
        m["metrics"]["annualized_return"] for m in per_tf if "error" not in m["metrics"]
    ]
    n_trades_total = sum(m["metrics"].get("n_trades", 0) for m in per_tf)

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "source_spec": cfg["source_spec"],
        "instruments": cfg["instruments"],
        "timeframes": tfs,
        "window_days": cfg["window_days"],
        "sharpe_method": "daily_resampled_per_SMA-34787",
        "sharpe_method_audit_ref": "SMA-34787",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_trades_total": int(n_trades_total),
        "sharpe_daily_mean": round(float(np.mean(sharpes)) if sharpes else 0.0, 4),
        "annualized_return_mean": round(float(np.mean(anns)) if anns else 0.0, 6),
        "per_resolution": per_tf,
    }

    (RESULTS_DIR / "metrics.json").write_text(
        json.dumps(_sanitize(envelope), indent=2)
    )
    (RESULTS_DIR / "per_resolution_summary.json").write_text(
        json.dumps(_sanitize({
            "variant_key": VARIANT_KEY,
            "iteration": cfg["iteration"],
            "source_spec": cfg["source_spec"],
            "sharpe_method": "daily_resampled_per_SMA-34787",
            "sharpe_method_audit_ref": "SMA-34787",
            "per_resolution": per_tf,
        }), indent=2)
    )

    lines = [
        f"=== {VARIANT_KEY} (SMA-34793) ===",
        f"instruments={cfg['instruments']} window_days={cfg['window_days']} TFs={tfs}",
        f"sharpe_method=daily_resampled sqrt({BARS_PER_YEAR_DAILY:.2f}) per SMA-34787",
        "",
        f"{'TF':<6}{'Trades':>8}{'WinRate':>10}{'PF':>9}"
        f"{'Sharpe_d':>11}{'AnnRet':>11}{'MaxDD%':>10}",
    ]
    for m in per_tf:
        d = m["metrics"]
        if "error" in d:
            lines.append(f"{m['tf']:<6} ERROR: {d['error']}")
            continue
        pf = d["profit_factor"]
        pf_s = f"{pf:>9.3f}" if math.isfinite(pf) else f"{'inf':>9}"
        lines.append(
            f"{m['tf']:<6}{d['n_trades']:>8d}{d['win_rate']:>10.3f}"
            f"{pf_s}{d['sharpe_daily']:>11.3f}{d['annualized_return']:>11.4f}"
            f"{d['max_drawdown_pct']:>10.3f}"
        )
    lines.append("")
    lines.append(
        f"Mean Sharpe_d = {envelope['sharpe_daily_mean']:.3f}  "
        f"Mean AnnRet = {envelope['annualized_return_mean']:.4f}  "
        f"Trades total = {n_trades_total}"
    )
    summary_text = "\n".join(lines) + "\n"
    (RESULTS_DIR / "summary.txt").write_text(summary_text)
    print(summary_text)

    return 0 if per_tf and not any("error" in m["metrics"] for m in per_tf) else 1


if __name__ == "__main__":
    raise SystemExit(main())
