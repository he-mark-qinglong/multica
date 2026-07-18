"""Multi-TF backtest runner for loid_vpvr_confluence_20260717 (SMA-34803).

For each of 1m / 15m / 4h over the last 30d of BTCUSDT bars:

  1. Slice the last ``window_days`` from the staged parquet.
  2. Build LOID + LOID+VPVR signals via ``strategy.run_backtest``.
  3. Compute daily-resampled Sharpe, max DD, hit rate, profit
     factor, total return, annualized return (per SMA-34787 audit).
  4. Write ``results/metrics.json`` (portable envelope) and
     ``results/per_resolution_summary.json`` (per-TF breakdown).

Output files
------------
  results/metrics.json                 — overall envelope (all TFs combined)
  results/per_resolution_summary.json  — per-TF × per-variant metrics
  results/equity_<variant>_<tf>.csv    — equity curve
  results/trades_<variant>_<tf>.csv    — per-trade ledger
  results/summary.txt                  — human-readable run log

Top-level BacktestResult JSONs are also written to
``~/multica/quant-loop/results/loid-vpvr/`` for cross-strategy
comparison (acceptance criterion referenced by the work-pool).
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
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "loid-vpvr"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO_ROOT / "config.json"

BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)


# ---------------------------------------------------------------------------
# Per-TF config.
# ---------------------------------------------------------------------------

def _tf_params(cfg: dict, tf: str) -> dict:
    p = dict(cfg["params"])
    p["vpvr_window_bars"] = int(p[f"vpvr_window_bars_{tf}"])
    p["vpvr_snapshot_every_bars"] = int(p[f"vpvr_snapshot_every_bars_{tf}"])
    p["max_hold_bars"] = int(p[f"max_hold_bars_{tf}"])
    return p


def _load_tf(tf: str, window_days: int) -> pd.DataFrame:
    path = DATA_DIR / f"BTCUSDT__{tf}.parquet"
    df = pd.read_parquet(path)
    if "openTime" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index("openTime")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()
    end = df.index.max()
    start = end - pd.Timedelta(days=window_days)
    return df.loc[start:end].copy()


# ---------------------------------------------------------------------------
# Metrics: daily-resampled Sharpe per SMA-34787 audit.
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
            "n_bars": n_bars,
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sharpe_daily": 0.0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_bars_held": 0.0,
        }

    # Total return & annualized return based on **calendar time** of the
    # backtest window (per-bar count inflates the denominator for high-
    # cadence TFs: 43201 1m bars / 365 = 118 years, which collapses the
    # compounding factor to ~0). Use the per-day equity resample to
    # derive the span, then annualise from the realised horizon.
    total_return = (final / starting) - 1.0
    eq_idx = idx[: len(equity)]
    daily_eq = pd.Series(equity, index=eq_idx, dtype=np.float64).resample("1D").last().dropna()
    if len(daily_eq) >= 2:
        n_days = max(1, (daily_eq.index[-1] - daily_eq.index[0]).days)
        n_years = n_days / BARS_PER_YEAR_DAILY
    else:
        n_years = n_bars / (BARS_PER_YEAR_DAILY * 1440)  # last-resort fallback
    if n_years > 0 and final > 0 and starting > 0:
        annualized = (final / starting) ** (1.0 / n_years) - 1.0
    else:
        annualized = 0.0

    # Daily-resampled Sharpe (SMA-34787).
    # Use per-bar index aligned to the equity length.
    eq_idx = idx[: len(equity)]
    sharpe = _daily_resampled_sharpe(equity, eq_idx)

    # Max drawdown on per-bar equity.
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


# ---------------------------------------------------------------------------
# Per-TF run + IO.
# ---------------------------------------------------------------------------

def _tf_to_freq(tf: str) -> str:
    return {"1m": "1min", "15m": "15min", "4h": "4h"}.get(tf, "1min")


def _write_equity_csv(result: dict, tf: str, variant: str) -> None:
    eq = result["equity"]
    n = len(eq)
    freq = _tf_to_freq(tf)
    idx = pd.date_range(start=result["span_start"], periods=n, freq=freq, tz="UTC")
    pd.DataFrame({"equity": eq}, index=idx).rename_axis("timestamp").to_csv(
        RESULTS_DIR / f"equity_{variant}_{tf}.csv"
    )


def _write_trades_csv(result: dict, tf: str, variant: str) -> None:
    trades = result["trades"]
    if not trades:
        pd.DataFrame(columns=[
            "variant", "symbol", "direction", "entry_ts", "entry_price",
            "exit_ts", "exit_price", "pnl_pct", "bars_held", "exit_reason",
            "iceberg_evidence", "vpvr_level", "vpvr_distance_atr",
        ]).to_csv(RESULTS_DIR / f"trades_{variant}_{tf}.csv", index=False)
        return
    pd.DataFrame(trades).to_csv(RESULTS_DIR / f"trades_{variant}_{tf}.csv", index=False)


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


def _run_one_tf(cfg: dict, tf: str) -> dict:
    window_days = int(cfg["window_days"])
    df = _load_tf(tf, window_days)
    tf_cfg = dict(cfg)
    tf_cfg["params"] = _tf_params(cfg, tf)
    print(f"[{tf}] rows={len(df)} start={df.index[0]} end={df.index[-1]}", flush=True)

    combined = run_backtest(df, tf_cfg)
    base_metrics = _compute_metrics(combined["iceberg_only"], df.index)
    conf_metrics = _compute_metrics(combined["iceberg_vpvr_confluence"], df.index)

    for variant, m_in in (("iceberg_only", base_metrics), ("iceberg_vpvr_confluence", conf_metrics)):
        m = dict(m_in)
        m["tf"] = tf
        m["variant"] = variant
        m["diagnostics"] = combined[variant].get("diagnostics", {})
        m["span_start"] = combined[variant]["span_start"]
        m["span_end"] = combined[variant]["span_end"]
        m["symbol"] = combined[variant]["symbol"]
        m["sharpe_method"] = "daily_resampled_per_SMA-34787"
        m["sharpe_method_audit_ref"] = "SMA-34787"
        payload = _sanitize(m)
        (TOPLEVEL_RESULTS / f"backtest_{variant}_{tf}.json").write_text(
            json.dumps(payload, indent=2)
        )
        # Save equity and trades per variant.
        _write_equity_csv(combined[variant], tf, variant)
        _write_trades_csv(combined[variant], tf, variant)

    return {
        "tf": tf,
        "rows": int(len(df)),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "diagnostics": combined.get("diagnostics", {}),
        "iceberg_only": base_metrics,
        "iceberg_vpvr_confluence": conf_metrics,
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
                "iceberg_only": {"n_trades": 0, "sharpe_daily": 0.0, "error": str(exc)},
                "iceberg_vpvr_confluence": {"n_trades": 0, "sharpe_daily": 0.0, "error": str(exc)},
            })

    # ---- Aggregate envelope (portable metrics.json) ----
    conf_sharpes = [
        m["iceberg_vpvr_confluence"]["sharpe_daily"]
        for m in per_tf
        if "error" not in m["iceberg_vpvr_confluence"]
    ]
    conf_anns = [
        m["iceberg_vpvr_confluence"]["annualized_return"]
        for m in per_tf
        if "error" not in m["iceberg_vpvr_confluence"]
    ]
    conf_trades = sum(m["iceberg_vpvr_confluence"].get("n_trades", 0) for m in per_tf)
    base_trades = sum(m["iceberg_only"].get("n_trades", 0) for m in per_tf)

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
        "n_trades_iceberg_only_total": int(base_trades),
        "n_trades_iceberg_vpvr_confluence_total": int(conf_trades),
        "sharpe_daily_mean_confluence": round(float(np.mean(conf_sharpes)) if conf_sharpes else 0.0, 4),
        "annualized_return_mean_confluence": round(float(np.mean(conf_anns)) if conf_anns else 0.0, 6),
        "per_resolution": per_tf,
    }

    metrics_path = RESULTS_DIR / "metrics.json"
    metrics_path.write_text(json.dumps(_sanitize(envelope), indent=2))

    per_resolution_path = RESULTS_DIR / "per_resolution_summary.json"
    per_resolution_path.write_text(json.dumps(_sanitize({
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "source_spec": cfg["source_spec"],
        "sharpe_method": "daily_resampled_per_SMA-34787",
        "sharpe_method_audit_ref": "SMA-34787",
        "per_resolution": per_tf,
    }), indent=2))

    # ---- Human-readable summary ----
    lines = [
        f"=== {VARIANT_KEY} (SMA-34803) ===",
        f"instruments={cfg['instruments']} window_days={cfg['window_days']} TFs={tfs}",
        f"sharpe_method=daily_resampled sqrt({BARS_PER_YEAR_DAILY:.2f}) per SMA-34787",
        "",
        f"{'TF':<6}{'Variant':<28}{'Trades':>8}{'WinRate':>10}{'PF':>9}"
        f"{'Sharpe_d':>11}{'AnnRet':>11}{'MaxDD%':>10}",
    ]
    for m in per_tf:
        for variant_key_in in ("iceberg_only", "iceberg_vpvr_confluence"):
            d = m[variant_key_in]
            if "error" in d:
                lines.append(f"{m['tf']:<6}{variant_key_in:<28} ERROR: {d['error']}")
                continue
            pf = d["profit_factor"]
            pf_s = f"{pf:>9.3f}" if math.isfinite(pf) else f"{'inf':>9}"
            lines.append(
                f"{m['tf']:<6}{variant_key_in:<28}{d['n_trades']:>8d}{d['win_rate']:>10.3f}"
                f"{pf_s}{d['sharpe_daily']:>11.3f}{d['annualized_return']:>11.4f}"
                f"{d['max_drawdown_pct']:>10.3f}"
            )
    lines.append("")
    lines.append(
        f"Mean Sharpe_d (confluence) = {envelope['sharpe_daily_mean_confluence']:.3f}  "
        f"Mean AnnRet (confluence) = {envelope['annualized_return_mean_confluence']:.4f}  "
        f"Trades (LOID/confluence) = {base_trades}/{conf_trades}"
    )
    summary_text = "\n".join(lines) + "\n"
    (RESULTS_DIR / "summary.txt").write_text(summary_text)
    print(summary_text)

    return 0 if per_tf and not any("error" in m["iceberg_vpvr_confluence"] for m in per_tf) else 1


if __name__ == "__main__":
    raise SystemExit(main())
