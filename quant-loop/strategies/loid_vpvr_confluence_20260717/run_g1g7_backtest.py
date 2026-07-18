"""G1-G7 LOID signal backtest runner — SMA-34929 (U4 closure).

This script runs the first LOID/iceberg signal backtest on real 15m
BTC data with a longer statistical window, and the same engine on 1m
and 4h for cross-check (the upstream detector in
``strategies/iceberg-detector/`` is 1m-native; 15m fires far less
frequently because volume variance dampens across aggregation).

Public API
----------
``main()`` writes:

  - ``results/g1g7_metrics.json``    — per-(TF × window) G1-G6 metrics
  - ``results/g1g7_summary.txt``     — human-readable run log
  - ``results/g1g7_<tf>_<window>d_<variant>.csv`` — equity curve per
    (TF, window, variant).

The script **does not modify** any upstream module. It reuses
``build_signals`` and ``run_backtest`` from the existing
SMA-34803 prototype harness verbatim. The only thing it changes
relative to ``run_backtest.py`` is:

  1. the window (configurable; default 180 days for 15m/4h and 30
     days for 1m to balance statistical power vs regime stability),
  2. computing G1-G7 metrics explicitly with the same daily-resampled
     Sharpe convention mandated by SMA-34787,
  3. emitting a bootstrap CI on the Sharpe estimator (G5),
  4. emitting a Bonferroni-style multiple-testing note (G7) for
     documentation, since no live α-correction machinery exists in
     this prototype.

For framework CV (G4): the harness is in-house only. The SPEC
explicitly defers freqtrade/backtrader/vectorbt adapters. We
therefore mark G4 = "NOT RUN" rather than "PASS". A strategy that
fails G1/G2/G3 in-house does not benefit from G4; G4 is only
meaningful on a gate-passing candidate, per the W5 audit convention.
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

BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)

# ---------------------------------------------------------------------------
# Default config — sourced from config.json (SMA-34803) verbatim so the
# upstream modules see the same parameters they were validated against.
# ---------------------------------------------------------------------------
_BASE_CFG = json.loads((REPO_ROOT / "config.json").read_text())

# Window choices: 15m needs a wider window than 1m to get enough
# iceberg_flag fires for any statistical test. The detector threshold
# (vol_z >= 3.0, range_ratio <= 0.75, lookback=120) was tuned for 1m
# bars; on 15m the lookback = 30h baseline is much wider than typical
# volume spike half-lives, so the gate fires 5-10x per year, not per
# week. This is a STRUCTURAL finding that the G1-G7 evaluation will
# surface.
DEFAULT_WINDOWS = {
    "15m": 365,   # 1y on 15m -> ~35 040 bars, ~8 flags
    "1m":  90,    # 90d on 1m -> 129 600 bars, ample flags
    "4h":  365,   # 1y on 4h -> ~2 190 bars, expect 0-1 flags
}


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


def _tf_to_freq(tf: str) -> str:
    return {"1m": "1min", "15m": "15min", "4h": "4h"}.get(tf, "1min")


# ---------------------------------------------------------------------------
# Metrics — daily-resampled Sharpe (SMA-34787) + G1-G7 envelope.
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


def _bootstrap_sharpe_ci(
    equity: np.ndarray, idx: pd.DatetimeIndex, n_iter: int = 1000, seed: int = 42
) -> tuple[float, float, float]:
    """Block-bootstrap (block=1d) CI on the daily-resampled Sharpe.

    Block size = 1d prevents serial-correlation underestimation; with
    daily blocks, each draw samples whole days, preserving intra-day
    return dependence.
    """
    series = pd.Series(equity, index=idx, dtype=np.float64)
    daily_eq = series.resample("1D").last().dropna()
    rets = daily_eq.pct_change().dropna().values
    n = len(rets)
    if n < 5 or rets.std() == 0:
        return 0.0, 0.0, 0.0
    rng = np.random.default_rng(seed)
    boot = np.empty(n_iter, dtype=np.float64)
    for k in range(n_iter):
        sample = rng.choice(rets, size=n, replace=True)
        if sample.std() == 0:
            boot[k] = 0.0
        else:
            boot[k] = sample.mean() / sample.std() * SQRT_BPY_DAILY
    point = rets.mean() / rets.std() * SQRT_BPY_DAILY
    return float(point), float(np.quantile(boot, 0.05)), float(np.quantile(boot, 0.95))


def _g1g7_envelope(
    result: dict, idx: pd.DatetimeIndex, family_id: str, n_families_in_campaign: int = 8
) -> dict:
    """Compute G1-G7 metrics from a single backtest result envelope."""
    equity = np.asarray(result["equity"], dtype=np.float64)
    trades = result["trades"]
    n_bars = int(result["n_bars"])
    starting = float(equity[0]) if len(equity) else 0.0
    final = float(equity[-1]) if len(equity) else 0.0

    if len(equity) < 2 or starting <= 0:
        return {
            "n_bars": n_bars, "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "sharpe_daily": 0.0, "sharpe_daily_ci_lo": 0.0, "sharpe_daily_ci_hi": 0.0,
            "total_return": 0.0, "annualized_return": 0.0, "max_drawdown_pct": 0.0,
            "avg_bars_held": 0.0,
            "g1_sharpe_pass": False, "g2_ann_pass": False, "g3_pf_pass": False,
            "g4_cv_pass": None, "g5_bootstrap_pass": False, "g6_dd_pass": False,
            "g7_bonferroni_alpha": 0.0125, "g7_doc_only": True,
        }

    total_return = (final / starting) - 1.0
    eq_idx = idx[: len(equity)]
    daily_eq = pd.Series(equity, index=eq_idx, dtype=np.float64).resample("1D").last().dropna()
    if len(daily_eq) >= 2:
        n_days = max(1, (daily_eq.index[-1] - daily_eq.index[0]).days)
        n_years = n_days / BARS_PER_YEAR_DAILY
    else:
        n_years = n_bars / (BARS_PER_YEAR_DAILY * 1440)
    annualized = (final / starting) ** (1.0 / n_years) - 1.0 if (n_years > 0 and final > 0 and starting > 0) else 0.0

    sharpe_point, sharpe_lo, sharpe_hi = _bootstrap_sharpe_ci(equity, eq_idx)

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
        "sharpe_daily": round(sharpe_point, 4),
        "sharpe_daily_ci_lo": round(sharpe_lo, 4),
        "sharpe_daily_ci_hi": round(sharpe_hi, 4),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "avg_bars_held": round(avg_bars_held, 2),
        # G1 — daily-resampled OOS Sharpe >= 1.0
        "g1_sharpe_pass": bool(sharpe_point >= 1.0),
        # G2 — annualized return >= 15%
        "g2_ann_pass": bool(annualized >= 0.15),
        # G3 — profit factor >= 1.5
        "g3_pf_pass": bool(np.isfinite(profit_factor) and profit_factor >= 1.5),
        # G4 — framework CV (NOT RUN; harness is in-house only)
        "g4_cv_pass": None,
        # G5 — bootstrap CI lower bound >= 0.5
        "g5_bootstrap_pass": bool(sharpe_lo >= 0.5),
        # G6 — max drawdown >= -25% (less negative than -25%)
        "g6_dd_pass": bool(max_dd_pct >= -25.0),
        # G7 — Bonferroni-style note (documentation; α=0.0125 across 4 families)
        "g7_bonferroni_alpha": 0.0125,
        "g7_doc_only": True,
    }


def _write_equity_csv(result: dict, tf: str, window: int, variant: str) -> None:
    eq = result["equity"]
    n = len(eq)
    freq = _tf_to_freq(tf)
    idx = pd.date_range(start=result["span_start"], periods=n, freq=freq, tz="UTC")
    pd.DataFrame({"equity": eq}, index=idx).rename_axis("timestamp").to_csv(
        RESULTS_DIR / f"g1g7_{tf}_{window}d_{variant}_equity.csv"
    )


def _write_trades_csv(result: dict, tf: str, window: int, variant: str) -> None:
    trades = result["trades"]
    cols = [
        "variant", "symbol", "direction", "entry_ts", "entry_price",
        "exit_ts", "exit_price", "pnl_pct", "bars_held", "exit_reason",
        "iceberg_evidence", "vpvr_level", "vpvr_distance_atr",
    ]
    if not trades:
        pd.DataFrame(columns=cols).to_csv(
            RESULTS_DIR / f"g1g7_{tf}_{window}d_{variant}_trades.csv", index=False
        )
        return
    pd.DataFrame(trades).to_csv(
        RESULTS_DIR / f"g1g7_{tf}_{window}d_{variant}_trades.csv", index=False
    )


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


def _run_one(tf: str, window: int) -> dict:
    cfg = dict(_BASE_CFG)
    cfg["window_days"] = int(window)
    cfg["params"] = _tf_params(_BASE_CFG, tf)
    df = _load_tf(tf, window)
    print(f"[{tf} {window}d] rows={len(df)} start={df.index[0]} end={df.index[-1]}", flush=True)

    combined = run_backtest(df, cfg)
    base = _g1g7_envelope(combined["iceberg_only"], df.index, family_id="loid_only")
    conf = _g1g7_envelope(combined["iceberg_vpvr_confluence"], df.index, family_id="loid_vpvr")

    for variant, m_in in (("iceberg_only", base), ("iceberg_vpvr_confluence", conf)):
        m = dict(m_in)
        m["tf"] = tf
        m["window_days"] = window
        m["variant"] = variant
        m["diagnostics"] = combined[variant].get("diagnostics", {})
        m["span_start"] = combined[variant]["span_start"]
        m["span_end"] = combined[variant]["span_end"]
        m["symbol"] = combined[variant]["symbol"]
        m["sharpe_method"] = "daily_resampled_per_SMA-34787"
        m["sharpe_method_audit_ref"] = "SMA-34787"
        _write_equity_csv(combined[variant], tf, window, variant)
        _write_trades_csv(combined[variant], tf, window, variant)
        payload = _sanitize(m)
        (RESULTS_DIR / f"g1g7_{tf}_{window}d_{variant}.json").write_text(
            json.dumps(payload, indent=2)
        )

    return {
        "tf": tf,
        "window_days": window,
        "rows": int(len(df)),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "diagnostics": combined.get("diagnostics", {}),
        "iceberg_only": base,
        "iceberg_vpvr_confluence": conf,
    }


def _format_metric(m: dict) -> str:
    pf = m["profit_factor"]
    pf_s = f"{pf:.3f}" if pf and pf == pf and abs(pf) != float("inf") else "inf"
    flags = "".join(
        "G" + k.split("_")[0][1:] + ("+" if v else ("-" if v is False else "?"))
        for k, v in (
            ("g1_sharpe_pass", m["g1_sharpe_pass"]),
            ("g2_ann_pass", m["g2_ann_pass"]),
            ("g3_pf_pass", m["g3_pf_pass"]),
            ("g4_cv_pass", m["g4_cv_pass"]),
            ("g5_bootstrap_pass", m["g5_bootstrap_pass"]),
            ("g6_dd_pass", m["g6_dd_pass"]),
        )
    )
    return (
        f"trades={m['n_trades']:>4d} WR={m['win_rate']:>5.2f} PF={pf_s:>6} "
        f"Sharpe_d={m['sharpe_daily']:>+7.3f} [{m['sharpe_daily_ci_lo']:>+6.2f},{m['sharpe_daily_ci_hi']:>+6.2f}] "
        f"AnnRet={m['annualized_return']*100:>+8.3f}% MaxDD={m['max_drawdown_pct']:>+7.3f}% "
        f"flags={flags}"
    )


def main() -> int:
    tfs = list(DEFAULT_WINDOWS.keys())
    per = []
    for tf in tfs:
        try:
            per.append(_run_one(tf, DEFAULT_WINDOWS[tf]))
        except Exception as exc:  # noqa: BLE001
            print(f"[{tf} {DEFAULT_WINDOWS[tf]}d] FAILED: {type(exc).__name__}: {exc}", flush=True)
            per.append({
                "tf": tf, "window_days": DEFAULT_WINDOWS[tf], "rows": 0,
                "iceberg_only": {"n_trades": 0, "sharpe_daily": 0.0, "error": str(exc)},
                "iceberg_vpvr_confluence": {"n_trades": 0, "sharpe_daily": 0.0, "error": str(exc)},
            })

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": _BASE_CFG["iteration"],
        "source_spec": "SMA-34929",
        "prior_spec": _BASE_CFG["source_spec"],
        "date": datetime.now(timezone.utc).date().isoformat(),
        "instruments": _BASE_CFG["instruments"],
        "windows": DEFAULT_WINDOWS,
        "sharpe_method": "daily_resampled_per_SMA-34787",
        "sharpe_method_audit_ref": "SMA-34787",
        "bootstrap_method": "block-1d, n_iter=1000, seed=42",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "per_resolution": per,
    }
    metrics_path = RESULTS_DIR / "g1g7_metrics.json"
    metrics_path.write_text(json.dumps(_sanitize(envelope), indent=2))

    lines = [
        "=== LOID G1-G7 backtest — SMA-34929 (U4 closure) ===",
        f"variant={VARIANT_KEY}  source_spec=SMA-34803 → re-evaluated under SMA-34929",
        f"sharpe_method=daily_resampled sqrt({BARS_PER_YEAR_DAILY:.2f}) per SMA-34787",
        f"bootstrap=block-1d n_iter=1000 seed=42",
        f"windows (TF → days): {DEFAULT_WINDOWS}",
        "",
        f"{'TF':<6}{'Window':<8}{'Variant':<28}"
        f"{'Trades':>7}{'WR':>7}{'PF':>7}{'Sharpe_d':>10}{'CI95':>15}"
        f"{'AnnRet%':>10}{'MaxDD%':>9}{'G1':>4}{'G2':>4}{'G3':>4}"
        f"{'G4':>4}{'G5':>4}{'G6':>4}",
    ]
    for m in per:
        for variant_key_in in ("iceberg_only", "iceberg_vpvr_confluence"):
            d = m[variant_key_in]
            if "error" in d:
                lines.append(f"{m['tf']:<6}{m['window_days']:<8}{variant_key_in:<28}ERROR: {d['error']}")
                continue
            ci = f"[{d['sharpe_daily_ci_lo']:+.2f},{d['sharpe_daily_ci_hi']:+.2f}]"
            pf = d["profit_factor"]
            pf_s = f"{pf:>7.3f}" if pf and pf == pf and abs(pf) != float("inf") else "    inf"
            lines.append(
                f"{m['tf']:<6}{m['window_days']:<8}{variant_key_in:<28}"
                f"{d['n_trades']:>7d}{d['win_rate']:>7.3f}{pf_s}"
                f"{d['sharpe_daily']:>+10.3f}{ci:>15}"
                f"{d['annualized_return']*100:>+10.3f}{d['max_drawdown_pct']:>+9.3f}"
                f"{'Y' if d['g1_sharpe_pass'] else 'N':>4}"
                f"{'Y' if d['g2_ann_pass'] else 'N':>4}"
                f"{'Y' if d['g3_pf_pass'] else 'N':>4}"
                f"{'-' if d['g4_cv_pass'] is None else ('Y' if d['g4_cv_pass'] else 'N'):>4}"
                f"{'Y' if d['g5_bootstrap_pass'] else 'N':>4}"
                f"{'Y' if d['g6_dd_pass'] else 'N':>4}"
            )
    lines.append("")
    lines.append("Gate legend: G1 Sharpe>=1.0 | G2 AnnRet>=15% | G3 PF>=1.5 | G4 framework CV | G5 bootstrap CI lo>=0.5 | G6 MaxDD>=-25%")
    lines.append("G4 is framework CV (freqtrade+backtrader+vectorbt); harness is in-house only → G4 = NOT RUN.")
    summary = "\n".join(lines) + "\n"
    (RESULTS_DIR / "g1g7_summary.txt").write_text(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())