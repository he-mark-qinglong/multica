"""SMA-34946 U5 funding_carry ETH/SOL 1m — fresh test harness.

Runs the U5 funding-carry long-only strategy on REAL Binance USDT-M
perp data (1m OHLCV + 8h funding events) for:

  1. SOL 1m long-only (primary)
  2. ETH 1m long-only (cross-check)

Threshold sweep grid (per issue spec, "per existing convention"):
  thresholds = [0.0001, 0.0003, 0.0005, 0.001]   (= ±1bp, ±3bp, ±5bp, ±10bp)

Each variant is scored on:
  - OOS daily-resampled Sharpe (per SMA-34787, ann_factor = sqrt(365.25))
  - annualized return (compounded from daily equity)
  - max drawdown (% of running peak)
  - profit factor
  - trades, win rate
  - bootstrap 95% CI for the Sharpe (10k resamples, seed=42)
  - Bonferroni-corrected p-value (one-sided, H1: Sharpe > 0)

G1-G7 hard gates (from multica-agent-base skill / canonical config):
  G1  Sharpe (daily-resampled) >= 1.0
  G2  annualized return       >= 15%
  G3  max drawdown            >= -25%
  G4  profit factor           >= 1.5
  G5  bootstrap CI lower      >= 0.5
  G6  Bonferroni one-sided p  <= 0.0125
  G7  trades                  >= 30

Outputs:
  /home/smark/multica/quant-loop/strategies/funding_carry/sma34946/
      results/u5_sma34946_metrics.json   — full per-variant metrics + gates
      results/u5_sma34946_summary.txt    — human-readable summary
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

QUANT_LOOP = Path("/home/smark/multica/quant-loop")
STRATEGY_PARENT = QUANT_LOOP / "strategies" / "funding_carry"
THIS_DIR = STRATEGY_PARENT / "sma34946"
sys.path.insert(0, str(STRATEGY_PARENT))          # parent strategy.py
sys.path.insert(0, str(THIS_DIR))                  # sma34946 data_loader

from data_loader import load_symbol_1m              # noqa: E402  (local copy)
from strategy import VARIANT_KEY, run_backtest      # noqa: E402

OUT_DIR = THIS_DIR / "results"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "funding-carry"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# G1-G7 hard gates
# ---------------------------------------------------------------------------
GATES = {
    "sharpe_min": 1.0,                # G1
    "annualized_return_min": 0.15,   # G2
    "max_drawdown_max": -0.25,       # G3
    "profit_factor_min": 1.5,        # G4
    "bootstrap_ci_lower_min": 0.5,   # G5
    "bootstrap_resamples": 10000,
    "bootstrap_seed": 42,
    "bonferroni_alpha": 0.0125,      # G6
    "trades_min": 30,                # G7
}
WINDOW_DAYS = 90
BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)
RISK_TARGET_PCT = 0.005
STARTING_CAPITAL = 100000.0

# Issue-spec grid (per "per existing convention").
THRESHOLDS = [0.0001, 0.0003, 0.0005, 0.001]


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------
def _trade_pnl_to_daily(trades: list, span_start: pd.Timestamp, span_end: pd.Timestamp) -> pd.Series:
    """Aggregate per-trade pnl_pct to a daily series indexed by exit
    event's UTC date. Matches SMA-34930 convention (see run_u5._trade_pnl_to_daily).
    """
    def _strip(t):
        return t.tz_convert(None) if (t is not None and getattr(t, "tz", None) is not None) else t
    if not trades:
        idx = pd.date_range(_strip(span_start), _strip(span_end), freq="1D")
        return pd.Series(0.0, index=idx)
    idx_list = [_strip(pd.Timestamp(t["exit_event_ts"])).normalize() for t in trades]
    pnl = pd.Series([t["pnl_pct"] for t in trades],
                    index=pd.DatetimeIndex(idx_list))
    daily = pnl.resample("1D").sum()
    full_idx = pd.date_range(_strip(span_start).normalize(),
                             _strip(span_end).normalize(), freq="1D")
    return daily.reindex(full_idx, fill_value=0.0)


def _build_daily_equity(daily_pnl: pd.Series, starting: float = STARTING_CAPITAL):
    eq = (1.0 + daily_pnl).cumprod() * starting
    return eq.values, eq.index


def _daily_sharpe(daily_pnl: pd.Series):
    eq_arr, eq_idx = _build_daily_equity(daily_pnl)
    eq_series = pd.Series(eq_arr, index=eq_idx, dtype=np.float64)
    daily_rets = eq_series.pct_change().dropna()
    sd = daily_rets.std(ddof=1)
    sharpe = float(daily_rets.mean() / sd * SQRT_BPY_DAILY) if sd > 0 else 0.0
    return sharpe, daily_rets


def _max_drawdown(equity: np.ndarray) -> float:
    if len(equity) < 2:
        return 0.0
    rm = np.maximum.accumulate(equity)
    dd = (equity - rm) / rm
    return float(dd.min() * 100.0)


def _bootstrap_sharpe_ci(rets: pd.Series, *, n_resamples: int, seed: int) -> dict:
    sharpes = np.empty(n_resamples, dtype=np.float64)
    rng = np.random.default_rng(seed)
    n = len(rets)
    for b in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        sample = rets.iloc[idx]
        sd = sample.std(ddof=1)
        sharpes[b] = (sample.mean() / sd * SQRT_BPY_DAILY) if (sd > 0 and np.isfinite(sd)) else 0.0
    ci_lo = float(np.quantile(sharpes, 0.025))
    ci_hi = float(np.quantile(sharpes, 0.975))
    mean_sh = float(np.mean(sharpes))
    p_two = float(min((sharpes <= 0).sum(), (sharpes >= 0).sum()) / n_resamples)
    p_pos_one = float((sharpes <= 0).sum() / n_resamples)
    return {"mean": mean_sh, "ci_lo": ci_lo, "ci_hi": ci_hi,
            "p_value": p_two, "p_positive_one_sided": p_pos_one}


def _metrics_from_equity(trades: list, equity: np.ndarray, span_days: int) -> dict:
    """Compute the full per-variant metrics using SMA-34930 convention:

      daily_pnl  = sum of per-trade pnl_pct by exit-event UTC date
      equity     = (1 + daily_pnl).cumprod() * starting
      sharpe     = mean(daily_rets)/std(daily_rets) * sqrt(365.25)
      ann_ret    = (final/starting)^(365.25/n_days) - 1
      mdd        = min pct drawdown of the equity curve

    This is the canonical "daily-resampled per SMA-34787" convention.
    """
    if not trades:
        return {
            "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "sharpe_daily": 0.0, "annualized_return": 0.0,
            "max_drawdown_pct": 0.0, "avg_bars_held": 0,
            "bootstrap_sharpe_mean": 0.0, "bootstrap_sharpe_ci_lo": 0.0,
            "bootstrap_sharpe_ci_hi": 0.0,
            "sharpe_p_value_two_sided": 1.0, "sharpe_p_value_pos_one_sided": 1.0,
            "daily_rets_n": 0,
        }
    span_start = pd.Timestamp(trades[0]["entry_bar_ts"])
    span_end = pd.Timestamp(trades[-1]["exit_bar_ts"])
    daily_pnl = _trade_pnl_to_daily(trades, span_start, span_end)
    eq_arr, eq_idx = _build_daily_equity(daily_pnl)
    starting = float(eq_arr[0]) if len(eq_arr) else 0.0
    final = float(eq_arr[-1]) if len(eq_arr) else 0.0

    n_trades = len(trades)
    pnl_pcts = np.array([t["pnl_pct"] for t in trades], dtype=np.float64)
    win_rate = float((pnl_pcts > 0).mean())
    gains = pnl_pcts[pnl_pcts > 0].sum()
    losses = -pnl_pcts[pnl_pcts < 0].sum()
    profit_factor = float(gains / losses) if losses > 0 else float("inf")

    sharpe_d, daily_rets = _daily_sharpe(daily_pnl)
    n_days = max(1, (eq_idx[-1] - eq_idx[0]).days)
    n_years = n_days / BARS_PER_YEAR_DAILY
    annualized = float((final / starting) ** (1.0 / n_years) - 1.0) \
        if (n_years > 0 and starting > 0 and final > 0) else 0.0
    mdd_pct = _max_drawdown(eq_arr)
    boot = _bootstrap_sharpe_ci(daily_rets,
                                 n_resamples=GATES["bootstrap_resamples"],
                                 seed=GATES["bootstrap_seed"])
    avg_bars = int(np.mean([t["bars_held"] for t in trades])) if trades else 0

    return {
        "n_trades": int(n_trades),
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "sharpe_daily": float(sharpe_d),
        "total_return": float(final / starting - 1.0) if starting > 0 else 0.0,
        "annualized_return": float(annualized),
        "max_drawdown_pct": float(mdd_pct),
        "avg_bars_held": int(avg_bars),
        "bootstrap_sharpe_mean": float(boot["mean"]),
        "bootstrap_sharpe_ci_lo": float(boot["ci_lo"]),
        "bootstrap_sharpe_ci_hi": float(boot["ci_hi"]),
        "sharpe_p_value_two_sided": float(boot["p_value"]),
        "sharpe_p_value_pos_one_sided": float(boot["p_positive_one_sided"]),
        "daily_rets_n": int(len(daily_rets)),
    }


def _gate_eval(m: dict) -> dict:
    return {
        "G1_sharpe_ge_1":     bool(m["sharpe_daily"] >= GATES["sharpe_min"]),
        "G2_ann_ge_15pct":    bool(m["annualized_return"] >= GATES["annualized_return_min"]),
        "G3_maxdd_ge_-25pct": bool(m["max_drawdown_pct"] >= GATES["max_drawdown_max"] * 100.0),
        "G4_pf_ge_1_5":       bool(m["profit_factor"] >= GATES["profit_factor_min"]),
        "G5_bs_ci_lo_ge_0_5": bool(m["bootstrap_sharpe_ci_lo"] >= GATES["bootstrap_ci_lower_min"]),
        "G6_bonferroni_pos_one_sided":
            bool(m["sharpe_p_value_pos_one_sided"] <= GATES["bonferroni_alpha"]),
        "G7_trades_ge_30":    bool(m["n_trades"] >= GATES["trades_min"]),
    }


# ---------------------------------------------------------------------------
# Variant sweep
# ---------------------------------------------------------------------------
def _sweep_one(symbol: str, df: pd.DataFrame, family_n: int) -> list[dict]:
    base_cfg = {
        "iteration": "SMA-34946",
        "instruments": [symbol],
        "starting_capital_usd": STARTING_CAPITAL,
        "params": {
            "fee_bps_per_fill": 4.0,
            "slippage_bps_per_fill": 1.0,
            "risk_target_pct": RISK_TARGET_PCT,
        },
    }
    span_days = (df.index[-1] - df.index[0]).days or 1
    variants = []
    for thr in THRESHOLDS:
        label = f"abs_{int(round(thr*10000))}bp"
        cfg = {**base_cfg, "params": {**base_cfg["params"],
                                       "funding_threshold": float(thr),
                                       "funding_percentile_q": None}}
        t0 = time.time()
        res = run_backtest(df, cfg)
        elapsed = time.time() - t0
        m = _metrics_from_equity(res["trades"], res["equity"], span_days)
        # Bonferroni: family = 4 thresholds × 2 symbols = 8 (well below 12 in SMA-34930).
        m["bonferroni_p_raw"] = m["sharpe_p_value_pos_one_sided"]
        m["bonferroni_family_n"] = family_n
        m["bonferroni_alpha_adj"] = float(GATES["bonferroni_alpha"] / family_n)
        gates = _gate_eval(m)
        variants.append({
            "label": label,
            "params_override": {"funding_threshold": float(thr),
                                 "funding_percentile_q": None},
            "diagnostics": res["diagnostics"],
            "metrics": {
                **m,
                "span_start": str(df.index[0]),
                "span_end": str(df.index[-1]),
                "n_funding_events": int(res["n_events"]),
                "tf": "1m",
                "variant": VARIANT_KEY,
                "symbol": symbol,
                "elapsed_sec": round(elapsed, 3),
                "gates": gates,
            },
        })
    return variants


def main() -> int:
    print(f"[u5-sma34946] start {datetime.now(timezone.utc).isoformat()}")
    family_n = len(THRESHOLDS) * 2  # SOL + ETH
    out = {
        "variant_key": VARIANT_KEY,
        "iteration": "SMA-34946",
        "source_spec": "SMA-34946",
        "issue_title": "U5: funding_carry ETH/SOL 1m with real data — backtest hypothesis test",
        "instruments": ["SOLUSDT", "ETHUSDT"],
        "timeframes": ["1m"],
        "window_days": WINDOW_DAYS,
        "issue_spec_grid_bps": [int(round(t*10000)) for t in THRESHOLDS],
        "hard_gates": {
            "sharpe_min": GATES["sharpe_min"],
            "annualized_return_min": GATES["annualized_return_min"],
            "max_drawdown_max": GATES["max_drawdown_max"],
            "profit_factor_min": GATES["profit_factor_min"],
            "bootstrap_ci_lower_min": GATES["bootstrap_ci_lower_min"],
            "bootstrap_resamples": GATES["bootstrap_resamples"],
            "bootstrap_seed": GATES["bootstrap_seed"],
            "bonferroni_alpha": GATES["bonferroni_alpha"],
            "trades_min": GATES["trades_min"],
        },
        "data_provenance": {
            "ETH_1m": str(THIS_DIR.parent.parent / "data/perp_1m/ETHUSDT_1m.parquet"),
            "SOL_1m": str(THIS_DIR.parent.parent / "data/perp_1m/SOLUSDT_1m.parquet"),
            "ETH_FUND": str(THIS_DIR.parent.parent / "data/funding/ETHUSDT.parquet"),
            "SOL_FUND": str(THIS_DIR.parent.parent / "data/funding/SOLUSDT.parquet"),
        },
        "sharpe_method": "daily_resampled_per_SMA-34787",
        "sharpe_method_audit_ref": "SMA-34787",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "per_symbol_variants": {},
        "funding_event_stats_90d": {},
    }

    for symbol in ("SOLUSDT", "ETHUSDT"):
        print(f"[u5-sma34946] loading {symbol} ...")
        df, stats = load_symbol_1m(symbol, WINDOW_DAYS)
        out["funding_event_stats_90d"][symbol] = stats
        print(f"  n_bars={len(df)}  n_events={stats['n_events']}  "
              f"min={stats['min']:.6f}  max={stats['max']:.6f}  "
              f"le_-1bp%={stats['le_-1bp_pct']*100:.2f}  "
              f"le_-3bp%={stats['le_-3bp_pct']*100:.2f}")
        variants = _sweep_one(symbol, df, family_n=family_n)
        out["per_symbol_variants"][symbol] = variants
        for v in variants:
            m = v["metrics"]
            g = m["gates"]
            passed = sum(1 for x in g.values() if x)
            print(f"  {v['label']:10s} trades={m['n_trades']:3d} "
                  f"WR={m['win_rate']:.3f} PF={m['profit_factor']:.3f} "
                  f"Sharpe={m['sharpe_daily']:+.3f} ann={m['annualized_return']*100:+.2f}% "
                  f"mdd={m['max_drawdown_pct']:+.2f}% bs_lo={m['bootstrap_sharpe_ci_lo']:+.2f} "
                  f"gates={passed}/7")

    # Verdict
    def _best(variants):
        # PROFITABLE requires all 7 gates pass; we report best by Sharpe.
        profitable = [v for v in variants if all(v["metrics"]["gates"].values())]
        if profitable:
            return "PROFITABLE", profitable[0]
        nonprof = [v for v in variants if v["metrics"]["annualized_return"] < 0]
        if nonprof:
            return "FAIL_NEG_ANN", min(nonprof, key=lambda v: v["metrics"]["sharpe_daily"])
        best = max(variants, key=lambda v: v["metrics"]["sharpe_daily"])
        return "FAIL_GATES", best

    verdicts = {}
    for sym, vs in out["per_symbol_variants"].items():
        verdict, best = _best(vs)
        verdicts[sym] = {"verdict": verdict, "best_variant": best["label"]}
    out["verdicts"] = verdicts

    # Write
    metrics_path = OUT_DIR / "u5_sma34946_metrics.json"
    metrics_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"[u5-sma34946] wrote {metrics_path}")
    # Mirror to top-level for visibility
    mirror = TOPLEVEL_RESULTS / "u5_sma34946_metrics.json"
    mirror.write_text(json.dumps(out, indent=2, default=str))

    # Summary
    summary_lines = [
        f"U5 SMA-34946 funding_carry ETH/SOL 1m — summary",
        f"window: {WINDOW_DAYS}d  grid: ±{THRESHOLDS} ({[int(t*10000) for t in THRESHOLDS]} bps)",
        "",
    ]
    for sym, vs in out["per_symbol_variants"].items():
        summary_lines.append(f"== {sym} ==")
        for v in vs:
            m = v["metrics"]
            g = m["gates"]
            passed = sum(1 for x in g.values() if x)
            summary_lines.append(
                f"  {v['label']:10s} trades={m['n_trades']:3d} WR={m['win_rate']:.3f} "
                f"PF={m['profit_factor']:.3f} Sharpe={m['sharpe_daily']:+.3f} "
                f"ann={m['annualized_return']*100:+.2f}% mdd={m['max_drawdown_pct']:+.2f}% "
                f"bs_lo={m['bootstrap_sharpe_ci_lo']:+.2f} gates={passed}/7"
            )
        summary_lines.append(f"  verdict: {verdicts[sym]['verdict']}  "
                             f"best: {verdicts[sym]['best_variant']}")
        summary_lines.append("")
    (OUT_DIR / "u5_sma34946_summary.txt").write_text("\n".join(summary_lines))
    print(f"[u5-sma34946] wrote summary")

    return 0


if __name__ == "__main__":
    sys.exit(main())