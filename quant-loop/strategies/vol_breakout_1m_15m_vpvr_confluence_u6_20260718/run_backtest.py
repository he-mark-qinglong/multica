"""G1-G7 runner for U6 vol_breakout_1m_15m_vpvr_confluence_u6_20260718 (SMA-34932).

Public API:
    main()

Writes per-(symbol, TF) metrics to ``results/<symbol>_<tf>_metrics.json``,
equity + trades CSVs, and a combined ``results/u6_metrics.json`` envelope.
Computes G1-G6 + G7 from the daily-resampled Sharpe (per SMA-34787 audit)
plus a bootstrap CI (block-1d, 1000 resamples, seed=42).

Window: 30 days (per SMA-34802 LOID+VPVR harness).
Primary variants: BTCUSDT 15m, BTCUSDT 1m.
Secondary (if BTC 15m Sharpe >= 0.5): ETHUSDT 15m, SOLUSDT 15m.

Designed to match the LOID harness so U6 results are apples-to-apples
with U4/U5 ledger rows.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from data_loader import load_symbol  # noqa: E402
from strategy import run_backtest  # noqa: E402

CONFIG_PATH = REPO_ROOT / "config.json"
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)


# ---------------------------------------------------------------------------
# Primary + secondary variants.
# ---------------------------------------------------------------------------

PRIMARY = [
    ("BTCUSDT", "15m"),
    ("BTCUSDT", "1m"),
]
SECONDARY_TRIGGER_SAFE = [
    ("ETHUSDT", "15m"),
    ("SOLUSDT", "15m"),
]


# ---------------------------------------------------------------------------
# Daily-resampled Sharpe (per SMA-34787).
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
    equity: np.ndarray,
    idx: pd.DatetimeIndex,
    n_iter: int = 1000,
    seed: int = 42,
) -> tuple[float, float, float]:
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
    return (
        float(point),
        float(np.quantile(boot, 0.05)),
        float(np.quantile(boot, 0.95)),
    )


# ---------------------------------------------------------------------------
# G1-G6 envelope.
# ---------------------------------------------------------------------------

def _envelope(result, idx: pd.DatetimeIndex) -> dict:
    """Compute G1-G6 + G7 metadata from a single backtest."""
    equity = np.array([v for _, v in result.equity], dtype=np.float64)
    trades = result.trades
    n_bars = len(equity)
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
    daily_eq = pd.Series(equity, index=idx[:n_bars]).resample("1D").last().dropna()
    n_days = max(1, (daily_eq.index[-1] - daily_eq.index[0]).days) if len(daily_eq) >= 2 else n_bars
    n_years = n_days / BARS_PER_YEAR_DAILY
    annualized = (
        (final / starting) ** (1.0 / n_years) - 1.0
        if (n_years > 0 and final > 0 and starting > 0) else 0.0
    )

    sharpe_point, sharpe_lo, sharpe_hi = _bootstrap_sharpe_ci(equity, idx[:n_bars])

    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_dd_pct = float(np.min(drawdowns)) * 100.0 if drawdowns.size else 0.0

    n_trades = len(trades)
    pnls = np.array([t.pnl_pct for t in trades], dtype=np.float64) if n_trades else np.array([])
    gross_profit = float(pnls[pnls > 0].sum()) if pnls.size else 0.0
    gross_loss = float(abs(pnls[pnls < 0].sum())) if pnls.size else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    win_rate = float((pnls > 0).sum() / n_trades) if n_trades > 0 else 0.0
    avg_bars_held = float(np.mean([t.bars_held for t in trades])) if n_trades > 0 else 0.0

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
        "g1_sharpe_pass": bool(sharpe_point >= 1.0),
        "g2_ann_pass": bool(annualized >= 0.15),
        "g3_pf_pass": bool(np.isfinite(profit_factor) and profit_factor >= 1.5),
        "g4_cv_pass": None,  # not run; harness is in-house only — see iter#84 lesson
        "g5_bootstrap_pass": bool(sharpe_lo >= 0.5),
        "g6_dd_pass": bool(max_dd_pct >= -25.0),
        "g7_bonferroni_alpha": 0.0125,
        "g7_doc_only": True,
    }


# ---------------------------------------------------------------------------
# Diagnostics: signal / confluence stats.
# ---------------------------------------------------------------------------

def _diagnostics(df: pd.DataFrame, ind_module):
    """Return n_signal_bars, n_confluence, hit_rates for the entry rule."""
    from indicators import annotate
    cfg = json.loads(CONFIG_PATH.read_text())
    tf_match = [k for k in df.attrs if k.endswith("tf")]  # not used
    raise NotImplementedError  # placeholder


# ---------------------------------------------------------------------------
# Per-(symbol, TF) runner.
# ---------------------------------------------------------------------------

def _write_equity_csv(result, idx: pd.DatetimeIndex, symbol: str, tf: str) -> None:
    eq = [v for _, v in result.equity]
    pd.DataFrame({"equity": eq}, index=idx[:len(eq)]).rename_axis("timestamp").to_csv(
        RESULTS_DIR / f"equity_{symbol}_{tf}.csv"
    )


def _write_trades_csv(result, symbol: str, tf: str) -> None:
    trades = result.trades
    cols = [
        "symbol", "direction",
        "entry_signal_date", "entry_fill_date", "entry_price",
        "exit_signal_date", "exit_fill_date", "exit_price",
        "reason", "pnl_usd", "pnl_pct", "bars_held",
        "atr_at_entry", "vpvr_dist_atr_at_entry", "realized_vol_at_entry",
        "size_units", "nav_at_entry",
    ]
    if not trades:
        pd.DataFrame(columns=cols).to_csv(
            RESULTS_DIR / f"trades_{symbol}_{tf}.csv", index=False
        )
        return
    rows = []
    for t in trades:
        rows.append({
            "symbol": t.symbol, "direction": t.direction,
            "entry_signal_date": t.entry_signal_date.isoformat() if t.entry_signal_date is not None else None,
            "entry_fill_date": t.entry_fill_date.isoformat() if t.entry_fill_date is not None else None,
            "entry_price": t.entry_price,
            "exit_signal_date": t.exit_signal_date.isoformat() if t.exit_signal_date is not None else None,
            "exit_fill_date": t.exit_fill_date.isoformat() if t.exit_fill_date is not None else None,
            "exit_price": t.exit_price,
            "reason": t.reason,
            "pnl_usd": t.pnl_usd,
            "pnl_pct": t.pnl_pct,
            "bars_held": t.bars_held,
            "atr_at_entry": t.atr_at_entry,
            "vpvr_dist_atr_at_entry": t.vpvr_dist_atr_at_entry,
            "realized_vol_at_entry": t.realized_vol_at_entry,
            "size_units": t.size_units,
            "nav_at_entry": t.nav_at_entry,
        })
    pd.DataFrame(rows).to_csv(RESULTS_DIR / f"trades_{symbol}_{tf}.csv", index=False)


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


def _run_one(symbol: str, tf: str, cfg: dict, window_days: Optional[int] = None) -> dict:
    if window_days is None:
        window_days = cfg["window_days"]
    df = load_symbol(symbol, tf, window_days)
    print(
        f"[{symbol} {tf} {window_days}d] rows={len(df)} "
        f"span={df.index[0].date()}..{df.index[-1].date()}",
        flush=True,
    )
    result = run_backtest(df, cfg, tf=tf, symbol=symbol)
    env = _envelope(result, df.index)
    env["symbol"] = symbol
    env["tf"] = tf
    env["window_days"] = window_days
    env["variant"] = "donchian_vpvr_confluence"
    env["sharpe_method"] = "daily_resampled_per_SMA-34787"
    env["sharpe_method_audit_ref"] = "SMA-34787"

    _write_equity_csv(result, df.index, symbol, tf)
    _write_trades_csv(result, symbol, tf)

    payload = _sanitize(env)
    (RESULTS_DIR / f"{symbol}_{tf}_{window_days}d_metrics.json").write_text(
        json.dumps(payload, indent=2)
    )

    # diagnostics: signal/confluence stats from the annotated frame
    from indicators import annotate
    ann = annotate(df, tf, cfg)
    n_signal = int(ann["long_entry"].fillna(False).sum())
    n_donchian_break = int(((ann["close"] > ann["range_high"]).fillna(False)).sum())
    n_regime_ok = int(((ann["vol_regime"] > cfg[f"indicators_{tf}"]["vol_regime_min"]).fillna(False)).sum())
    n_confluence = int(((ann["vpvr_dist_atr"] <= cfg[f"indicators_{tf}"]["proximity_atr_k"]).fillna(False)).sum())
    n_long_break_and_regime = int(
        ((ann["close"] > ann["range_high"]).fillna(False)
         & (ann["vol_regime"] > cfg[f"indicators_{tf}"]["vol_regime_min"]).fillna(False)).sum()
    )
    diagnostics = {
        "n_signal_bars": n_signal,
        "n_donchian_break": n_donchian_break,
        "n_regime_ok": n_regime_ok,
        "n_poc_confluence": n_confluence,
        "n_long_break_and_regime": n_long_break_and_regime,
        "donchian_break_rate": round(n_donchian_break / len(df), 6),
        "vpvr_confluence_rate": round(n_confluence / len(df), 6),
        "filter_passes_break_to_signal": round(n_signal / n_donchian_break, 4) if n_donchian_break else 0.0,
        "filter_passes_break_and_regime_to_signal": (
            round(n_signal / n_long_break_and_regime, 4) if n_long_break_and_regime else 0.0
        ),
    }
    payload["diagnostics"] = diagnostics
    (RESULTS_DIR / f"{symbol}_{tf}_{window_days}d_metrics.json").write_text(
        json.dumps(payload, indent=2)
    )
    return payload


def _format_metric(m: dict) -> str:
    pf = m["profit_factor"]
    pf_s = f"{pf:.3f}" if (pf and pf == pf and abs(pf) != float("inf")) else "inf"
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
    cfg = json.loads(CONFIG_PATH.read_text())
    per: List[dict] = []
    btc_15m_sharpe: Optional[float] = None

    print(f"\n--- PRIMARY (window={cfg['window_days']}d per SMA-34802) ---\n", flush=True)
    for sym, tf in PRIMARY:
        try:
            m = _run_one(sym, tf, cfg)
            per.append(m)
            if sym == "BTCUSDT" and tf == "15m":
                btc_15m_sharpe = m["sharpe_daily"]
        except Exception as exc:  # noqa: BLE001
            print(f"[{sym} {tf}] FAILED: {type(exc).__name__}: {exc}", flush=True)
            per.append({
                "symbol": sym, "tf": tf, "n_trades": 0, "sharpe_daily": 0.0,
                "error": str(exc),
            })

    # Secondary (15m cross-asset): only if BTC 15m Sharpe >= 0.5
    if btc_15m_sharpe is not None and btc_15m_sharpe >= 0.5:
        print(f"\n--- SECONDARY cross-asset (BTC 15m Sharpe >= 0.5) ---\n", flush=True)
        for sym, tf in SECONDARY_TRIGGER_SAFE:
            try:
                m = _run_one(sym, tf, cfg)
                per.append(m)
            except Exception as exc:  # noqa: BLE001
                print(f"[{sym} {tf}] FAILED: {type(exc).__name__}: {exc}", flush=True)
                per.append({
                    "symbol": sym, "tf": tf, "n_trades": 0, "sharpe_daily": 0.0,
                    "error": str(exc),
                })
    else:
        print(
            f"\nBTC 15m Sharpe={btc_15m_sharpe} (<0.5) -> skipping ETH/SOL 15m "
            f"secondary variants per SMA-34932 spec.",
            flush=True,
        )

    # Extended windows — only run for primary variants to satisfy >=30 trades
    # floor without losing the 30d diagnostic for spec compliance.
    print(f"\n--- EXTENDED WINDOWS (>=30 trades floor) ---\n", flush=True)
    ext_per: List[dict] = []
    for sym, tf in PRIMARY:
        ext_d = cfg.get("extended_windows_for_trade_floor", {}).get(tf, {}).get("secondary_days")
        if ext_d is None:
            continue
        try:
            m = _run_one(sym, tf, cfg, window_days=ext_d)
            m["is_extended_window"] = True
            ext_per.append(m)
        except Exception as exc:  # noqa: BLE001
            print(f"[{sym} {tf} {ext_d}d] FAILED: {type(exc).__name__}: {exc}", flush=True)
            ext_per.append({
                "symbol": sym, "tf": tf, "window_days": ext_d, "n_trades": 0,
                "sharpe_daily": 0.0, "is_extended_window": True, "error": str(exc),
            })

    envelope = {
        "strategy": cfg["strategy"],
        "iteration": cfg["iteration"],
        "parent_iteration": cfg["parent_iteration"],
        "parent_strategy": cfg["parent_strategy"],
        "axis": cfg["axis"],
        "hypothesis_source": cfg["hypothesis_source"],
        "source_spec": "SMA-34932",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "instruments": cfg["instruments"],
        "primary": [list(t) for t in PRIMARY],
        "secondary_eligible": btc_15m_sharpe is not None and btc_15m_sharpe >= 0.5,
        "btc_15m_sharpe_daily_30d": btc_15m_sharpe,
        "window_days": cfg["window_days"],
        "extended_windows": cfg.get("extended_windows_for_trade_floor", {}),
        "sharpe_method": "daily_resampled_per_SMA-34787",
        "sharpe_method_audit_ref": "SMA-34787",
        "bootstrap_method": "block-1d, n_iter=1000, seed=42",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "per_variant": per,
        "per_variant_extended": ext_per,
    }
    metrics_path = RESULTS_DIR / "u6_metrics.json"
    metrics_path.write_text(json.dumps(_sanitize(envelope), indent=2))

    lines = [
        "=== U6 vol_breakout TF-downshift — SMA-34932 ===",
        f"strategy={cfg['strategy']}  iter={cfg['iteration']}  parent={cfg['parent_strategy']}",
        f"sharpe_method=daily_resampled sqrt({BARS_PER_YEAR_DAILY:.2f}) per SMA-34787",
        f"bootstrap=block-1d n_iter=1000 seed=42",
        f"primary_window={cfg['window_days']}d",
        "",
        "--- PRIMARY ---",
        f"{'Sym':<8}{'TF':<6}{'Days':>6}{'Trades':>7}{'WR':>7}{'PF':>7}{'Sharpe_d':>10}{'CI95':>17}"
        f"{'AnnRet%':>10}{'MaxDD%':>9}{'G1':>4}{'G2':>4}{'G3':>4}"
        f"{'G4':>4}{'G5':>4}{'G6':>4}",
    ]
    for m in per:
        if "error" in m:
            lines.append(f"{m['symbol']:<8}{m['tf']:<6}ERROR: {m['error']}")
            continue
        ci = f"[{m['sharpe_daily_ci_lo']:+.2f},{m['sharpe_daily_ci_hi']:+.2f}]"
        pf = m["profit_factor"]
        pf_s = f"{pf:>7.3f}" if pf and pf == pf and abs(pf) != float("inf") else "    inf"
        lines.append(
            f"{m['symbol']:<8}{m['tf']:<6}{m['window_days']:>6d}{m['n_trades']:>7d}"
            f"{m['win_rate']:>7.3f}{pf_s}{m['sharpe_daily']:>+10.3f}{ci:>17}"
            f"{m['annualized_return']*100:>+10.3f}{m['max_drawdown_pct']:>+9.3f}"
            f"{'Y' if m['g1_sharpe_pass'] else 'N':>4}"
            f"{'Y' if m['g2_ann_pass'] else 'N':>4}"
            f"{'Y' if m['g3_pf_pass'] else 'N':>4}"
            f"{'-' if m['g4_cv_pass'] is None else ('Y' if m['g4_cv_pass'] else 'N'):>4}"
            f"{'Y' if m['g5_bootstrap_pass'] else 'N':>4}"
            f"{'Y' if m['g6_dd_pass'] else 'N':>4}"
        )

    lines.append("")
    lines.append("--- EXTENDED WINDOWS (>=30 trades floor) ---")
    for m in ext_per:
        if "error" in m:
            lines.append(f"{m['symbol']:<8}{m['tf']:<6}{m['window_days']:>5d}ERROR: {m['error']}")
            continue
        ci = f"[{m['sharpe_daily_ci_lo']:+.2f},{m['sharpe_daily_ci_hi']:+.2f}]"
        pf = m["profit_factor"]
        pf_s = f"{pf:>7.3f}" if pf and pf == pf and abs(pf) != float("inf") else "    inf"
        lines.append(
            f"{m['symbol']:<8}{m['tf']:<6}{m['window_days']:>6d}{m['n_trades']:>7d}"
            f"{m['win_rate']:>7.3f}{pf_s}{m['sharpe_daily']:>+10.3f}{ci:>17}"
            f"{m['annualized_return']*100:>+10.3f}{m['max_drawdown_pct']:>+9.3f}"
            f"{'Y' if m['g1_sharpe_pass'] else 'N':>4}"
            f"{'Y' if m['g2_ann_pass'] else 'N':>4}"
            f"{'Y' if m['g3_pf_pass'] else 'N':>4}"
            f"{'-' if m['g4_cv_pass'] is None else ('Y' if m['g4_cv_pass'] else 'N'):>4}"
            f"{'Y' if m['g5_bootstrap_pass'] else 'N':>4}"
            f"{'Y' if m['g6_dd_pass'] else 'N':>4}"
        )

    lines.append("")
    lines.append("Legend: G1 Sharpe>=1.0 | G2 AnnRet>=15% | G3 PF>=1.5 | G4 framework CV (NOT RUN, in-house only) | G5 bootstrap CI lo>=0.5 | G6 MaxDD>=-25%")
    summary = "\n".join(lines) + "\n"
    (RESULTS_DIR / "u6_summary.txt").write_text(summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())