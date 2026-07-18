"""U5 funding_carry ETH/SOL 1m backtest harness (SMA-34930) — event-driven.

Runs the long-only funding-carry strategy on real Binance 1m data
for:

  1. SOL 1m long-only  (carry-only)
  2. ETH 1m long-only  (carry-only)
  3. Combined ETH+SOL 1m equal-risk-contribution portfolio

For each instrument the harness sweeps:

  - absolute threshold: 1 bp (0.0001), 0.5 bp (0.00005), 0.25 bp
  - percentile gate:    q=20 (bottom 20% of funding),
                        q=10 (bottom 10%), q=05 (bottom 5%)

Each variant is scored on:

  - OOS daily-resampled Sharpe (per SMA-34787)
  - annualized return  (compounded from daily equity)
  - max drawdown       (% of running peak)
  - profit factor
  - trades, win rate
  - bootstrap 95% CI for the Sharpe (10k resamples, seed=42)
  - Bonferroni-corrected p-value (one-sided, H1: Sharpe > 0)

G1-G7 gates (from the canonical config in
vpvr_volume_edge_3tf_v1_20260711/config.json):

  G1  Sharpe (daily-resampled) >= 1.0
  G2  annualized return       >= 15%
  G3  max drawdown            >= -25% (more positive is better)
  G4  profit factor           >= 1.5
  G5  bootstrap CI lower      >= 0.5  (over 10k resamples at alpha=0.05)
  G6  Bonferroni p-value (one-sided) <= 0.0125
  G7  trades                  >= 30

Outputs go to ``~/multica/quant-loop/backtests/u5_funding_carry_eth_sol_1m/``:

  - u5_metrics.json      — full per-variant results + gate evaluation
  - u5_summary.txt       — human-readable run log
  - u5_equity_<sym>_<label>.csv  — daily equity curve per (sym, variant)
  - u5_trades_<sym>_<label>.csv  — per-trade ledger per (sym, variant)
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

# --- Paths -------------------------------------------------------------------
QUANT_LOOP = Path("/home/smark/multica/quant-loop")
STRATEGY_DIR = QUANT_LOOP / "strategies" / "funding_carry"
sys.path.insert(0, str(STRATEGY_DIR))

OUT_DIR = QUANT_LOOP / "backtests" / "u5_funding_carry_eth_sol_1m"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "funding-carry"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

from data_loader import load_symbol_1m  # noqa: E402
from strategy import VARIANT_KEY, run_backtest  # noqa: E402

# --- G1-G7 hard gates (from vpvr_volume_edge_3tf_v1_20260711/config.json) ---
GATES = {
    "sharpe_min": 1.0,                # G1
    "annualized_return_min": 0.15,   # G2
    "max_drawdown_max": -0.25,       # G3 (upper bound, more positive = better)
    "profit_factor_min": 1.5,        # G4
    "bootstrap_ci_lower_min": 0.5,   # G5
    "bootstrap_resamples": 10000,
    "bootstrap_seed": 42,
    "bonferroni_alpha": 0.0125,      # G6 (one-sided, H1: Sharpe > 0)
    "trades_min": 30,                # G7
}
DEFAULT_WINDOW_DAYS = 90
BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)
RISK_TARGET_PCT = 0.005
STARTING_CAPITAL = 100000.0

# Back-compat alias (some downstream scripts still reference this name).
WINDOW_DAYS = DEFAULT_WINDOW_DAYS


# ---------------------------------------------------------------------------
# Sweep grid — U3 fix: absolute (<=1bp) AND percentile (bottom 20%).
# ---------------------------------------------------------------------------
def variant_grid() -> list[tuple[str, dict]]:
    return [
        ("abs_1bp",   {"funding_threshold": 0.00010, "funding_percentile_q": None}),
        ("abs_0.5bp", {"funding_threshold": 0.00005, "funding_percentile_q": None}),
        ("abs_0.25bp",{"funding_threshold": 0.000025,"funding_percentile_q": None}),
        ("pct_q20",   {"funding_threshold": None,    "funding_percentile_q": 20.0}),
        ("pct_q10",   {"funding_threshold": None,    "funding_percentile_q": 10.0}),
        ("pct_q05",   {"funding_threshold": None,    "funding_percentile_q": 5.0}),
    ]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _trade_pnl_to_daily(trades: list, span_start: pd.Timestamp, span_end: pd.Timestamp) -> pd.Series:
    """Aggregate per-trade pnl_pct to a daily series indexed by the exit
    event's UTC date. Days with no exits get a 0 return; days with
    multiple exits get the sum of pnls.

    Output is always tz-naive to align with the resample/reindex
    below without an implicit tz-conversion that would silently drop
    rows.
    """
    def _strip(t: pd.Timestamp) -> pd.Timestamp:
        return t.tz_convert(None) if (t is not None and t.tz is not None) else t
    if not trades:
        idx = pd.date_range(_strip(span_start), _strip(span_end), freq="1D")
        return pd.Series(0.0, index=idx)
    idx_list = [_strip(pd.Timestamp(t["exit_event_ts"])).normalize() for t in trades]
    pnl = pd.Series([t["pnl_pct"] for t in trades],
                    index=pd.DatetimeIndex(idx_list))
    daily = pnl.resample("1D").sum()
    full_idx = pd.date_range(_strip(span_start).normalize(),
                             _strip(span_end).normalize(), freq="1D")
    daily = daily.reindex(full_idx, fill_value=0.0)
    return daily


def _build_daily_equity(daily_pnl: pd.Series, starting: float = STARTING_CAPITAL) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """Compounded equity curve from a daily pnl series (each value is
    interpreted as a per-day fractional return)."""
    eq = (1.0 + daily_pnl).cumprod() * starting
    return eq.values, eq.index


def _daily_sharpe_from_equity(equity: np.ndarray, idx: pd.DatetimeIndex) -> tuple[float, pd.Series]:
    """Compute daily-resampled Sharpe from an equity curve.

    The Sharpe is mean / std of daily pct_change of the equity curve,
    multiplied by sqrt(365.25) for annualisation (per SMA-34787).
    """
    eq_series = pd.Series(equity, index=idx, dtype=np.float64)
    rets = eq_series.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0 or not np.isfinite(rets.std()):
        return 0.0, rets
    return float(rets.mean() / rets.std() * SQRT_BPY_DAILY), rets


def _daily_sharpe(daily_pnl: pd.Series) -> tuple[float, pd.Series]:
    """Backward-compat alias: build equity then take pct_change."""
    eq_arr, eq_idx = _build_daily_equity(daily_pnl)
    return _daily_sharpe_from_equity(eq_arr, eq_idx)


def _max_drawdown(equity: np.ndarray) -> float:
    if len(equity) < 2:
        return 0.0
    rm = np.maximum.accumulate(equity)
    dd = (equity - rm) / rm
    return float(np.min(dd)) if dd.size else 0.0


def _bootstrap_sharpe_ci(rets: pd.Series, *, n_resamples: int, seed: int) -> dict:
    """Block-bootstrap (block=1 day) CI for the daily-resampled Sharpe.

    Returns:
        mean_sh, ci_lo, ci_hi, p_positive, p_two_sided
    where p_positive is the one-sided p-value for H1: Sharpe > 0
    (fraction of bootstrap Sharpes <= 0).
    """
    if len(rets) < 2:
        return {"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0,
                "p_value": 1.0, "p_positive_one_sided": 1.0}
    rng = np.random.RandomState(seed)
    rets_arr = rets.values
    sharpes = np.empty(n_resamples, dtype=np.float64)
    for b in range(n_resamples):
        idx = rng.randint(0, len(rets_arr), size=len(rets_arr))
        sample = rets_arr[idx]
        sd = sample.std(ddof=1)
        sharpes[b] = (sample.mean() / sd * SQRT_BPY_DAILY) if (sd > 0 and np.isfinite(sd)) else 0.0
    ci_lo = float(np.quantile(sharpes, 0.025))
    ci_hi = float(np.quantile(sharpes, 0.975))
    mean_sh = float(np.mean(sharpes))
    p_two = float(min((sharpes <= 0).sum(), (sharpes >= 0).sum()) / n_resamples)
    p_pos_one = float((sharpes <= 0).sum() / n_resamples)   # H1: Sharpe > 0
    return {"mean": mean_sh, "ci_lo": ci_lo, "ci_hi": ci_hi,
            "p_value": p_two, "p_positive_one_sided": p_pos_one}


def _bonferroni_correct(p_values: list[float], alpha: float) -> dict:
    """Bonferroni-correct one-sided tests (H1: Sharpe > 0).
    The corrected test REJECTS H0 (i.e., claims positive Sharpe) when
    p_pos_one_sided <= alpha / n_tests.
    """
    n = len(p_values)
    if n == 0:
        return {"alpha": alpha, "n_tests": 0, "per_variant": [], "all_pass_positive": False,
                "alpha_adj": alpha}
    adj_alpha = alpha / n
    per = []
    for p in p_values:
        per.append({
            "p_raw": float(p),
            "alpha_adj": float(adj_alpha),
            "passes_bonferroni_positive": float(p) <= adj_alpha,
        })
    return {
        "alpha": alpha, "n_tests": n, "alpha_adj": adj_alpha,
        "per_variant": per,
        "all_pass_positive": all(x["passes_bonferroni_positive"] for x in per),
    }


def _compute_metrics(result: dict, daily_pnl: pd.Series) -> dict:
    equity_arr, equity_idx = _build_daily_equity(daily_pnl)
    starting = float(equity_arr[0]) if len(equity_arr) else 0.0
    final = float(equity_arr[-1]) if len(equity_arr) else 0.0
    n_trades = int(result.get("n_trades_fired", len(result["trades"])))

    if len(equity_arr) < 2 or starting <= 0:
        return {"n_trades": n_trades, "win_rate": 0.0,
                "profit_factor": 0.0, "sharpe_daily": 0.0,
                "total_return": 0.0, "annualized_return": 0.0,
                "max_drawdown_pct": 0.0, "avg_bars_held": 0.0,
                "bootstrap_sharpe_mean": 0.0, "bootstrap_sharpe_ci_lo": 0.0,
                "bootstrap_sharpe_ci_hi": 0.0, "sharpe_p_value_two_sided": 1.0,
                "sharpe_p_value_pos_one_sided": 1.0, "daily_rets_n": 0,
                "n_funding_events": int(result.get("n_events", 0))}

    total_return = final / starting - 1.0
    n_days = max(1, (equity_idx[-1] - equity_idx[0]).days)
    n_years = n_days / BARS_PER_YEAR_DAILY
    annualized = (final / starting) ** (1.0 / n_years) - 1.0 if (n_years > 0 and final > 0) else 0.0

    sharpe_d, daily_rets = _daily_sharpe(daily_pnl)
    max_dd = _max_drawdown(equity_arr)

    pnl_arr = np.array([t["pnl_pct"] for t in result["trades"]], dtype=np.float64) if n_trades else np.array([])
    if pnl_arr.size:
        gross_profit = float(pnl_arr[pnl_arr > 0].sum())
        gross_loss = float(abs(pnl_arr[pnl_arr < 0].sum()))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        wr = float((pnl_arr > 0).sum() / n_trades)
        avg_bars = float(np.mean([t["bars_held"] for t in result["trades"]]))
    else:
        pf = 0.0
        wr = 0.0
        avg_bars = 0.0

    boot = _bootstrap_sharpe_ci(daily_rets, n_resamples=GATES["bootstrap_resamples"], seed=GATES["bootstrap_seed"])
    return {
        "n_trades": n_trades,
        "win_rate": round(wr, 4),
        "profit_factor": round(pf, 4) if math.isfinite(pf) else float("inf"),
        "sharpe_daily": round(sharpe_d, 4),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "avg_bars_held": round(avg_bars, 2),
        "bootstrap_sharpe_mean": round(boot["mean"], 4),
        "bootstrap_sharpe_ci_lo": round(boot["ci_lo"], 4),
        "bootstrap_sharpe_ci_hi": round(boot["ci_hi"], 4),
        "sharpe_p_value_two_sided": round(boot["p_value"], 4),
        "sharpe_p_value_pos_one_sided": round(boot["p_positive_one_sided"], 4),
        "daily_rets_n": int(len(daily_rets)),
        "n_funding_events": int(result.get("n_events", 0)),
    }


def _apply_gates(metrics: dict) -> dict:
    pf = metrics["profit_factor"]
    pf_pass = pf is not None and (math.isfinite(pf) if pf is not None else False) and pf >= GATES["profit_factor_min"]
    return {
        "G1_sharpe_ge_1":      metrics["sharpe_daily"] >= GATES["sharpe_min"],
        "G2_ann_ge_15pct":     metrics["annualized_return"] >= GATES["annualized_return_min"],
        "G3_maxdd_ge_-25pct":  metrics["max_drawdown_pct"] / 100.0 >= GATES["max_drawdown_max"],
        "G4_pf_ge_1_5":        pf_pass,
        "G5_bs_ci_lo_ge_0_5":  metrics["bootstrap_sharpe_ci_lo"] >= GATES["bootstrap_ci_lower_min"],
        "G7_trades_ge_30":     metrics["n_trades"] >= GATES["trades_min"],
        # G6 (Bonferroni) applied across variant family below.
    }


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Single-variant runner
# ---------------------------------------------------------------------------
def _run_one(df: pd.DataFrame, sym: str, label: str, params_override: dict,
             base_params: dict, starting_capital: float = STARTING_CAPITAL,
             iteration: str = "SMA-34930",
             window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    p = {**base_params}
    for k, v in params_override.items():
        if v is not None:
            p[k] = v
        elif k in p:
            del p[k]
    cfg = {
        "variant": VARIANT_KEY,
        "strategy_key": VARIANT_KEY,
        "iteration": iteration,
        "date": "2026-07-18",
        "source_spec": "SMA-34930",
        "instruments": [sym],
        "starting_capital_usd": starting_capital,
        "timeframes": ["1m"],
        "window_days": window_days,
        "params": p,
    }
    t0 = time.time()
    res = run_backtest(df, cfg)
    span_start = pd.Timestamp(res["span_start"]).tz_convert(None) if pd.Timestamp(res["span_start"]).tz is not None else pd.Timestamp(res["span_start"])
    span_end = pd.Timestamp(res["span_end"]).tz_convert(None) if pd.Timestamp(res["span_end"]).tz is not None else pd.Timestamp(res["span_end"])
    daily_pnl = _trade_pnl_to_daily(res["trades"], span_start, span_end)
    m = _compute_metrics(res, daily_pnl)
    m["tf"] = "1m"
    m["variant"] = VARIANT_KEY
    m["diagnostics"] = res.get("diagnostics", {})
    m["span_start"] = res["span_start"]
    m["span_end"] = res["span_end"]
    m["symbol"] = sym
    m["label"] = label
    m["params_override"] = _sanitize(params_override)
    m["sharpe_method"] = "daily_resampled_per_SMA-34787"
    m["sharpe_method_audit_ref"] = "SMA-34787"
    m["elapsed_sec"] = round(time.time() - t0, 2)
    m["gates"] = _apply_gates(m)

    # Build a daily equity curve for this variant.
    equity_arr, equity_idx = _build_daily_equity(daily_pnl)
    daily_equity = pd.DataFrame({"equity": equity_arr}, index=equity_idx).rename_axis("date")
    return {"label": label, "metrics": m, "trades": res["trades"],
            "daily_equity": daily_equity, "diagnostics": res.get("diagnostics", {}),
            "daily_pnl": daily_pnl}


# ---------------------------------------------------------------------------
# Portfolio combine (equal risk contribution)
# ---------------------------------------------------------------------------
def _combine_portfolio(per_sym: dict[str, list[dict]], label_filter: str,
                       starting_capital: float = STARTING_CAPITAL) -> dict:
    """Equal-risk contribution: build a daily PnL table where each
    row is a day and each column is a symbol's daily return (in
    pnl_pct units). The portfolio's daily return is the mean of the
    symbol returns on each day; the portfolio equity curve is the
    compounded product of (1 + mean_return).
    """
    syms = list(per_sym.keys())
    series_by_sym = {}
    n_trades_total = 0
    for sym in syms:
        for v in per_sym[sym]:
            if v["label"] == label_filter:
                series_by_sym[sym] = v["daily_pnl"]
                n_trades_total += int(v["metrics"]["n_trades"])
                break
    if not series_by_sym:
        return {"label": f"portfolio_{label_filter}", "n_trades": 0,
                "sharpe_daily": 0.0, "annualized_return": 0.0,
                "max_drawdown_pct": 0.0, "profit_factor": 0.0, "win_rate": 0.0,
                "bootstrap_sharpe_mean": 0.0, "bootstrap_sharpe_ci_lo": 0.0,
                "bootstrap_sharpe_ci_hi": 0.0,
                "sharpe_p_value_two_sided": 1.0,
                "sharpe_p_value_pos_one_sided": 1.0, "symbols": syms}
    aligned = pd.concat(series_by_sym, axis=1).fillna(0.0)
    daily_pnl = aligned.mean(axis=1)
    equity_arr, equity_idx = _build_daily_equity(daily_pnl, starting_capital)
    sharpe_d, daily_rets = _daily_sharpe(daily_pnl)
    total_return = float(equity_arr[-1] / equity_arr[0] - 1.0)
    n_days = max(1, (equity_idx[-1] - equity_idx[0]).days)
    n_years = n_days / BARS_PER_YEAR_DAILY
    annualized = float((equity_arr[-1] / equity_arr[0]) ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0
    max_dd = _max_drawdown(equity_arr)
    boot = _bootstrap_sharpe_ci(daily_rets, n_resamples=GATES["bootstrap_resamples"], seed=GATES["bootstrap_seed"])
    return {
        "label": f"portfolio_{label_filter}",
        "n_trades": int(n_trades_total),
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "sharpe_daily": round(sharpe_d, 4),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "bootstrap_sharpe_mean": round(boot["mean"], 4),
        "bootstrap_sharpe_ci_lo": round(boot["ci_lo"], 4),
        "bootstrap_sharpe_ci_hi": round(boot["ci_hi"], 4),
        "sharpe_p_value_two_sided": round(boot["p_value"], 4),
        "sharpe_p_value_pos_one_sided": round(boot["p_positive_one_sided"], 4),
        "daily_rets_n": int(len(daily_rets)),
        "symbols": syms,
        "sharpe_method": "daily_resampled_per_SMA-34787",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(window_days: int = DEFAULT_WINDOW_DAYS,
         out_dir: Path = OUT_DIR,
         iteration: str = "SMA-34930") -> int:
    print(f"[u5] starting at {datetime.now(timezone.utc).isoformat()}  "
          f"window_days={window_days}  iteration={iteration}")
    base = {
        "funding_threshold": 0.0001,
        "funding_lookback_events": 90,
        "fee_bps_per_fill": 4.0,
        "slippage_bps_per_fill": 1.0,
        "risk_target_pct": RISK_TARGET_PCT,
    }
    grid = variant_grid()

    loaded = {}
    for sym in ("SOLUSDT", "ETHUSDT"):
        df, stats = load_symbol_1m(sym, window_days)
        loaded[sym] = (df, stats)
        print(f"[load] {sym} window {df.index[0]} -> {df.index[-1]} rows={len(df)}")
        print(f"[load] {sym} funding event stats ({window_days}d): "
              f"n={stats['n_events']} max={stats['max']*100:.4f}% "
              f"min={stats['min']*100:.4f}% neg%={stats['neg_pct']*100:.1f}% "
              f"<=1bp%={stats['le_-1bp_pct']*100:.1f}% <=0.5bp%={stats['le_-0.5bp_pct']*100:.1f}%")

    per_sym_results: dict[str, list[dict]] = {}
    for sym in ("SOLUSDT", "ETHUSDT"):
        df, _stats = loaded[sym]
        per_sym_results[sym] = []
        for label, override in grid:
            print(f"\n=== {sym} variant: {label}  override={override} ===", flush=True)
            m = _run_one(df, sym, label, override, dict(base),
                         iteration=iteration, window_days=window_days)
            per_sym_results[sym].append(m)
            mm = m["metrics"]
            d = m["diagnostics"]
            print(f"  events={mm['n_funding_events']} fires={mm['n_trades']} "
                  f"fund<thr={d.get('funding_below_threshold_events', 0)} "
                  f"fund<pct={d.get('funding_below_percentile_events', 0)}")
            print(f"  sharpe_d={mm['sharpe_daily']:.3f} ann={mm['annualized_return']*100:.3f}% "
                  f"maxDD={mm['max_drawdown_pct']:.3f}% WR={mm['win_rate']*100:.1f}% PF={mm['profit_factor']} "
                  f"BS_CIlo={mm['bootstrap_sharpe_ci_lo']:.3f}")

    # Bonferroni across SOL×ETH×variant family (one-sided: H1 Sharpe > 0)
    raw_p = []
    pair_keys = []
    for sym in ("SOLUSDT", "ETHUSDT"):
        for v in per_sym_results[sym]:
            raw_p.append(v["metrics"]["sharpe_p_value_pos_one_sided"])
            pair_keys.append(f"{sym}/{v['label']}")
    bonferroni = _bonferroni_correct(raw_p, GATES["bonferroni_alpha"])
    for sym in ("SOLUSDT", "ETHUSDT"):
        for v in per_sym_results[sym]:
            key = f"{sym}/{v['label']}"
            idx = pair_keys.index(key)
            v["metrics"]["gates"]["G6_bonferroni_pos_one_sided"] = bonferroni["per_variant"][idx]["passes_bonferroni_positive"]
            v["metrics"]["bonferroni_p_raw"] = bonferroni["per_variant"][idx]["p_raw"]
            v["metrics"]["bonferroni_alpha_adj"] = bonferroni["per_variant"][idx]["alpha_adj"]
            v["metrics"]["bonferroni_family_n"] = bonferroni["n_tests"]

    portfolio_results = {}
    for label, _ in grid:
        port = _combine_portfolio(per_sym_results, label)
        pf_v = port["profit_factor"]
        pf_pass = pf_v is not None and (math.isfinite(pf_v) if pf_v is not None else False) and pf_v >= GATES["profit_factor_min"]
        port["gates"] = {
            "G1_sharpe_ge_1":      port["sharpe_daily"] >= GATES["sharpe_min"],
            "G2_ann_ge_15pct":     port["annualized_return"] >= GATES["annualized_return_min"],
            "G3_maxdd_ge_-25pct":  port["max_drawdown_pct"] / 100.0 >= GATES["max_drawdown_max"],
            "G4_pf_ge_1_5":        pf_pass,
            "G5_bs_ci_lo_ge_0_5":  port["bootstrap_sharpe_ci_lo"] >= GATES["bootstrap_ci_lower_min"],
            "G7_trades_ge_30":     port["n_trades"] >= GATES["trades_min"],
        }
        portfolio_results[label] = port

    sol_eth_per_variant = {
        sym: [_sanitize({"label": v["label"], "metrics": v["metrics"],
                         "diagnostics": v["diagnostics"]}) for v in per_sym_results[sym]]
        for sym in ("SOLUSDT", "ETHUSDT")
    }

    funding_stats = {sym: loaded[sym][1] for sym in ("SOLUSDT", "ETHUSDT")}
    out = {
        "variant_key": VARIANT_KEY,
        "iteration": iteration,
        "source_spec": iteration,
        "instruments": ["SOLUSDT", "ETHUSDT"],
        "timeframes": ["1m"],
        "window_days": window_days,
        "hard_gates": GATES,
        "funding_event_stats": funding_stats,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sharpe_method": "daily_resampled_per_SMA-34787",
        "sharpe_method_audit_ref": "SMA-34787",
        "data_provenance": {
            "ETH_1m":  "data/perp_1m/ETHUSDT_1m.parquet (shared pool, real Binance)",
            "SOL_1m":  ("strategies/vpvr_volume_edge_3tf_v1_20260711/data/SOLUSDT__1m.parquet "
                        "and identical-copy at strategies/vpvr_xs_pairs_mr_1m_v1_20260711/"
                        "data/SOLUSDT__1m.parquet — SHA256 match dd7f59ea…6a7e, "
                        "2,378,800 rows 2022-01-01..2026-07-10, real Binance 1m, no synthetic)"),
            "ETH_FUND": "data/funding/ETHUSDT.parquet (Binance USDT-M, 5100 events 8h cadence)",
            "SOL_FUND": "data/funding/SOLUSDT.parquet (Binance USDT-M, 5175 events 8h cadence)",
        },
        "per_symbol_variants": sol_eth_per_variant,
        "bonferroni": _sanitize(bonferroni),
        "portfolio_variants": _sanitize(portfolio_results),
    }
    metrics_path = out_dir / "u5_metrics.json"
    metrics_path.write_text(json.dumps(_sanitize(out), indent=2))
    print(f"\nWrote {metrics_path}")
    (TOPLEVEL_RESULTS / "u5_metrics.json").write_text(json.dumps(_sanitize(out), indent=2))
    print(f"Wrote {TOPLEVEL_RESULTS / 'u5_metrics.json'}")

    for sym in ("SOLUSDT", "ETHUSDT"):
        for v in per_sym_results[sym]:
            label = v["label"]
            v["daily_equity"].to_csv(out_dir / f"u5_equity_{sym}_{label}.csv")
            if v["trades"]:
                pd.DataFrame(v["trades"]).to_csv(
                    out_dir / f"u5_trades_{sym}_{label}.csv", index=False)
            else:
                pd.DataFrame(columns=[
                    "variant","symbol","direction","entry_event_ts","entry_bar_ts",
                    "entry_price","exit_event_ts","exit_bar_ts","exit_price",
                    "price_pnl_pct","funding_pnl_pct","pnl_pct","funding_at_entry",
                    "funding_received","bars_held","exit_reason"
                ]).to_csv(out_dir / f"u5_trades_{sym}_{label}.csv", index=False)

    # Human-readable summary
    lines = [
        f"=== {VARIANT_KEY} ({iteration}) — event-driven ===",
        f"window_days={window_days}  bars_per_year={int(BARS_PER_YEAR_DAILY)}  "
        f"sqrt_bpy={SQRT_BPY_DAILY:.4f}  sharpe_method=daily_resampled_per_SMA-34787",
        f"G1-G7 hard gates: "
        f"sharpe>={GATES['sharpe_min']}, ann>={GATES['annualized_return_min']*100:.0f}%, "
        f"maxdd>={GATES['max_drawdown_max']*100:.0f}%, pf>={GATES['profit_factor_min']}, "
        f"bs_ci_lo>={GATES['bootstrap_ci_lower_min']}, "
        f"bonferroni_alpha={GATES['bonferroni_alpha']} (one-sided H1: Sharpe>0), "
        f"trades>={GATES['trades_min']}",
        "",
        f"Funding event stats ({window_days}d, raw 8h events):",
    ]
    for sym in ("SOLUSDT", "ETHUSDT"):
        s = funding_stats[sym]
        lines.append(
            f"  {sym}: n={s['n_events']} max={s['max']*100:.4f}% min={s['min']*100:.4f}% "
            f"neg={s['neg_pct']*100:.1f}% <=-1bp={s['le_-1bp_pct']*100:.1f}% "
            f"<=-0.5bp={s['le_-0.5bp_pct']*100:.1f}%"
        )
    lines.append("")
    lines.append("Per-symbol results:")
    lines.append(f"{'Sym':<8}{'Variant':<10}{'Fires':>7}{'Sharpe_d':>11}{'Ann%':>9}"
                 f"{'MaxDD%':>10}{'WR%':>8}{'PF':>9}{'BS_CIlo':>10}{'p_one':>9}  Gates")
    for sym in ("SOLUSDT", "ETHUSDT"):
        for v in per_sym_results[sym]:
            mm = v["metrics"]
            g = mm["gates"]
            gates_s = "".join(["Y" if g[k] else "N" for k in sorted(g)])
            pf_s = f"{mm['profit_factor']:.2f}" if (isinstance(mm['profit_factor'], (int, float)) and math.isfinite(mm['profit_factor'])) else "inf"
            lines.append(
                f"{sym:<8}{v['label']:<10}{mm['n_trades']:>7d}{mm['sharpe_daily']:>11.3f}"
                f"{mm['annualized_return']*100:>8.2f}%{mm['max_drawdown_pct']:>10.3f}"
                f"{mm['win_rate']*100:>7.1f}%{pf_s:>9}{mm['bootstrap_sharpe_ci_lo']:>10.3f}"
                f"{mm['sharpe_p_value_pos_one_sided']:>9.3f}  {gates_s}"
            )
    lines.append("")
    lines.append("Portfolio (equal-risk, ETH+SOL) results:")
    lines.append(f"{'Variant':<20}{'Fires':>7}{'Sharpe_d':>11}{'Ann%':>9}{'MaxDD%':>10}{'BS_CIlo':>10}  Gates")
    for label, port in portfolio_results.items():
        g = port["gates"]
        gates_s = "".join(["Y" if g[k] else "N" for k in sorted(g)])
        lines.append(
            f"{port['label']:<20}{port['n_trades']:>7d}{port['sharpe_daily']:>11.3f}"
            f"{port['annualized_return']*100:>8.2f}%{port['max_drawdown_pct']:>10.3f}"
            f"{port['bootstrap_sharpe_ci_lo']:>10.3f}  {gates_s}"
        )
    lines.append("")
    lines.append("Bonferroni (one-sided H1: Sharpe > 0, across SOL×ETH×variant family):")
    lines.append(f"  alpha={bonferroni['alpha']}  n_tests={bonferroni['n_tests']}  "
                 f"alpha_adj={bonferroni['alpha_adj']:.6f}")
    for i, key in enumerate(pair_keys):
        e = bonferroni["per_variant"][i]
        lines.append(f"  {key:<24} p_raw={e['p_raw']:.4f}  passes_pos={e['passes_bonferroni_positive']}")

    summary_text = "\n".join(lines) + "\n"
    (out_dir / "u5_summary.txt").write_text(summary_text)
    print(summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())